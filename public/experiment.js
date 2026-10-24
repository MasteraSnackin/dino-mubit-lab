/* Explicit stages, durable evidence and one bounded provider suggestion. */
(() => {
  'use strict';
  const slots = ['baseline-a', 'repeat-a', 'baseline-b', 'candidate-a', 'candidate-b'];
  const labels = {'baseline-a':'Course A · baseline','repeat-a':'Course A · repeat','baseline-b':'Course B · baseline','candidate-a':'Course A · proposed change','candidate-b':'Course B · proposed change'};
  const parameters = {jumpLeadMs:'Jump lead time',duckLeadMs:'Duck lead time',duckExtraMs:'Extra duck time',jumpHoldMs:'Jump hold time'};
  let ui, selected = null, busy = false, loading = false, batch = false, operation = 'local', stopped = false, pending = null, revision = 0, message = '';
  const node = (tag, text, cls) => { const n = document.createElement(tag); if (text != null) n.textContent = text; if (cls) n.className = cls; return n; };
  const button = (text, action) => { const n = node('button',text); n.type='button'; n.addEventListener('click',action); return n; };
  const complete = run => run && ['collision','time_limit'].includes(run.outcome);
  const own = owner => window.dispatchEvent(new CustomEvent('dino-controller-change',{detail:{owner}}));
  async function request(path, body) {
    const response = await fetch(path, body === undefined ? {cache:'no-store'} : {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    const result = await response.json();
    if (!response.ok || !result.ok) throw new Error(result.message || 'The comparison could not be updated.');
    return result;
  }
  const path = suffix => `/api/experiments/${encodeURIComponent(selected.id)}${suffix || ''}`;
  function enter(kind='local') {
    if (busy || loading || pending || window.DinoMubit?.busy || window.DinoLocalPlayer?.active) return false;
    const event = new CustomEvent('dino-controller-request',{cancelable:true,detail:{owner:'experiment'}});
    if (!window.dispatchEvent(event)) { message='Another player is using the game.'; paint(); return false; }
    revision++; busy=true; operation=kind; stopped=false; own('experiment'); paint(); return true;
  }
  function publish() { if(selected) window.dispatchEvent(new CustomEvent('dino-experiment-updated',{detail:{experimentId:selected.id,status:selected.comparison?.status}})); }
  function leave() { busy=false; batch=false; own(null); paint(); publish(); }
  function candidateConfig() {
    const change = selected.proposal?.result;
    return change ? {...selected.plan.baselineConfig,[change.parameter]:change.value} : null;
  }
  function paint() {
    if (!ui) return;
    const runs = selected?.runs || {}, proposal = selected?.proposal, comparison = selected?.comparison;
    const baselinesReady = ['baseline-a','repeat-a','baseline-b'].every(slot => complete(runs[slot]));
    const interrupted = Object.values(runs).some(run => !complete(run));
    const externalBusy = !busy && (window.DinoMubit?.busy || window.DinoLocalPlayer?.active);
    const blocked = busy || loading || Boolean(pending) || externalBusy;
    ui.new.disabled=blocked; ui.history.disabled=blocked; ui.refresh.disabled=blocked;
    ui.reuse.hidden=proposal?.status!=='failed'; ui.reuse.disabled=blocked || comparison?.status!=='awaiting_proposal';
    ui.baseline.disabled=blocked || !selected || baselinesReady || interrupted;
    const status=window.DinoMubit?.status;
    ui.propose.disabled=blocked || selected?.reviewAllowed===false || !baselinesReady || interrupted || comparison?.status!=='awaiting_proposal' || proposal?.status!=='idle' || !status?.ready || !status?.verified || status.callsRemaining===0;
    ui.candidate.disabled=blocked || proposal?.status!=='succeeded' || interrupted || ['candidate-a','candidate-b'].every(slot => runs[slot]);
    ui.stop.disabled=!batch || stopped;
    ui.retry.hidden=!pending; ui.retry.disabled=busy; ui.unsaved.hidden=!pending;
    ui.export.hidden=!selected; if (selected) ui.export.href=path('/export');
    ui.phase.textContent=message || 'Create a comparison to begin.';
    ui.identity.textContent=selected ? `Saved comparison ${selected.id.slice(0,8)} · ${new Date(selected.createdAt).toLocaleString('en-GB')} · Courses ${selected.plan.seeds.a} / ${selected.plan.seeds.b}${selected.baselineSourceExperimentId ? ' · Baselines reused from '+selected.baselineSourceExperimentId.slice(0,8) : ''}${selected.evaluationReserved ? ' · Reserved test courses' : ''}` : 'The usual player settings stay unchanged.';
    ui.steps.textContent=busy ? (batch ? 'Local player running at normal speed. Keep this tab visible.' : operation==='review' ? 'Mubit is reviewing the saved evidence…' : 'Updating the local comparison record…') : selected?.evaluationReserved ? 'Record test-course baselines locally. Model evaluation awaits a verified route that excludes training.' : '1. Baselines and repeat → 2. One Mubit suggestion → 3. Test and compare';
    ui.tbody.replaceChildren();
    for (const slot of slots) {
      const run=runs[slot], row=node('tr');
      const evidence=node('td');
      if (run) evidence.append(button('View evidence',()=>showEvidence(slot)));
      row.append(node('td',labels[slot]),node('td',run ? String(run.score) : '—'),node('td',run ? `${(run.ticks/60).toFixed(2)}s` : '—'),node('td',run ? run.outcome.replaceAll('_',' ') : 'Not run'),evidence);
      ui.tbody.append(row);
    }
    ui.proposal.replaceChildren();
    if (proposal?.status==='succeeded') {
      const change=proposal.result;
      ui.proposal.append(node('h3',`${parameters[change.parameter]}: ${selected.plan.baselineConfig[change.parameter]} → ${change.value} ms`),node('p',change.rationale),node('p',change.caveat,'lab-muted'));
      ui.proposal.append(node('p','This is a test setting. The normal player has not been changed.','lab-muted'));
    } else if (proposal?.status==='pending') {
      ui.proposal.append(node('p','The review was submitted. Its outcome is not yet confirmed; refresh to check the saved record. It will not be sent again automatically.','lab-error'));
    } else if (proposal?.status==='failed') {
      ui.proposal.append(node('p',proposal.message || proposal.error?.message || 'Mubit did not produce a valid change. The baseline records are preserved.','lab-error'));
    } else if (selected?.reviewAllowed===false) {
      ui.proposal.append(node('p',selected.reviewRestriction,'lab-muted'));
    } else {
      ui.proposal.append(node('p','Mubit receives the last six screenshots and up to 60 action events from each baseline course. One review request is sent only when you choose “Ask Mubit for one change”.','lab-muted'));
    }
    ui.results.replaceChildren();
    if (comparison?.status==='complete') {
      ui.results.append(node('h3','Measured result on these two courses'));
      for (const pair of comparison.pairs || []) {
        ui.results.append(node('p',`Course ${pair.course.toUpperCase()}: ${pair.baselineScore} → ${pair.candidateScore} points (${pair.scoreDelta >= 0 ? '+' : ''}${pair.scoreDelta}); ${(pair.baselineTicks/60).toFixed(2)} → ${(pair.candidateTicks/60).toFixed(2)} seconds.`));
      }
      const wording={improved_on_both:'The proposed setting survived longer on both courses.',mixed:'The results were mixed across the two courses.',unchanged:'The proposed setting did not change these results.',worse_on_both:'The proposed setting survived for less time on both courses.',inconclusive:'These results do not support a conclusion.'};
      ui.results.append(node('p',wording[comparison.aggregate?.conclusion] || comparison.reason),node('p','This is a two-course pilot, not proof of general improvement. The repeat checks that course A reproduces. No trained model version has been verified here; the default player is unchanged.','lab-muted'));
    } else if (comparison?.reason) ui.results.append(node('p',comparison.reason, ['baseline_mismatch','inconclusive'].includes(comparison.status) ? 'lab-error' : 'lab-muted'));
    if (interrupted) ui.results.append(node('p','An interrupted run is preserved. Start a new comparison for a complete test; this record will not be overwritten.','lab-error'));
  }
  function showEvidence(slot) {
    const run=selected?.runs[slot]; if (!run) return;
    ui.evidence.hidden=false; ui.evidenceTitle.textContent=labels[slot];
    ui.frames.replaceChildren();
    for (const frame of run.frames.slice(-6)) {
      const fig=node('figure'), image=node('img'); image.src=frame.image; image.alt=`${labels[slot]}, ${(frame.tMs/1000).toFixed(2)} seconds, score ${frame.score}`;
      fig.append(image,node('figcaption',`${(frame.tMs/1000).toFixed(2)}s · score ${frame.score}`)); ui.frames.append(fig);
    }
    ui.actions.replaceChildren();
    for (const action of run.actions.slice(-20)) ui.actions.append(node('li',`${(action.tMs/1000).toFixed(2)}s · ${action.action} · ${action.reason}`));
    ui.evidenceNote.textContent=`${run.actions.length} actions · ${run.frames.length} sampled images · ${run.ticks} game steps · longest frame gap ${run.maxFrameGapMs} ms. Course data is saved for comparison checks and is not supplied to the player or Mubit.`;
  }
  async function refreshList(selectId) {
    const token=++revision;
    loading=true; paint();
    try {
      const data=await request('/api/experiments'); if (token!==revision) return;
      ui.history.replaceChildren(node('option','Choose a saved comparison'));
      ui.history.firstChild.value='';
      for (const item of data.experiments || []) {
        const option=node('option',`${item.id.slice(0,8)} · ${new Date(item.createdAt).toLocaleString('en-GB')}`); option.value=item.id; ui.history.append(option);
      }
      ui.warning.textContent=data.unavailable?.length ? `${data.unavailable.length} record(s) could not be read. They have been preserved.` : '';
      const id=selectId || selected?.id || data.experiments?.[0]?.id;
      if (id) {
        const result=await request(`/api/experiments/${encodeURIComponent(id)}`); if (token!==revision) return;
        selected=result.experiment; ui.history.value=id; ui.evidence.hidden=true;
        message=selected.comparison?.status==='complete' ? 'Comparison saved. Review the measured results below.' : 'Saved comparison loaded. Continue with the next available step.';
      }
    } catch (error) { if (token===revision) { ui.warning.textContent=error.message; selected=null; ui.evidence.hidden=true; message='The saved comparison could not be loaded. Refresh to try reading it again.'; } }
    finally { if (token===revision) { loading=false; paint(); } }
  }
  async function create() {
    if (!enter()) return;
    try {
      const data=await request('/api/experiments',{}); selected=data.experiment;
      ui.evidence.hidden=true; await refreshList(selected.id);
      message='Ready to run course A, its repeat, then course B.';
    } catch(error) { message=error.message; }
    finally { leave(); }
  }
  async function reuseBaselines() {
    if (!selected || !enter()) return;
    try {
      const data=await request(path('/reuse-baselines'),{}); selected=data.experiment;
      ui.evidence.hidden=true; await refreshList(selected.id);
      message='Verified baseline evidence reused in a new comparison. The failed review is preserved. Choose “Ask Mubit for one change” to send a new request.';
    } catch(error) { message=error.message; }
    finally { leave(); }
  }
  async function savePending() {
    if (!pending) return;
    const current=pending;
    const result=await request(`/api/experiments/${encodeURIComponent(current.experimentId)}/runs`,current.record);
    if (pending!==current) return;
    pending=null;
    if (result.experiment) selected=result.experiment;
    else selected=(await request(`/api/experiments/${encodeURIComponent(current.experimentId)}`)).experiment;
  }
  async function runBatch(names) {
    if (!selected || !window.DinoExperimentRunner || !enter()) return;
    const experimentId=selected.id;
    const startPrompt=document.getElementById('messageBox'), priorVisibility=startPrompt?.style.visibility;
    if(startPrompt) startPrompt.style.visibility='hidden';
    batch=true; paint();
    try {
      for (const slot of names) {
        if (stopped || selected.runs[slot]) continue;
        message=`Running ${labels[slot].toLowerCase()}…`; paint();
        const plan=selected.plan, config=slot.startsWith('candidate') ? candidateConfig() : plan.baselineConfig;
        const record=await window.DinoExperimentRunner.run({slot,runId:crypto.randomUUID(),seed:plan.slots[slot].seed,maxTicks:plan.maxTicks,config});
        pending={experimentId,record}; own('experiment');
        message=`Saving ${labels[slot].toLowerCase()}…`; paint();
        await savePending();
        if (!complete(record) || ['baseline_mismatch','inconclusive'].includes(selected.comparison?.status)) { stopped=true; break; }
      }
      message=stopped ? (selected.comparison?.reason || 'The batch stopped. Its recorded evidence is preserved.') : names[0].startsWith('baseline') ? (selected.reviewAllowed===false ? 'Test-course baselines saved locally. Recorded model calls remain blocked for these courses.' : 'Baselines saved. You can now ask Mubit for one change.') : 'Proposed change tested. The comparison is saved below.';
    } catch(error) { message=`${error.message}${pending ? ' The run is still in this tab: retry its local save or download it before closing.' : ''}`; }
    finally { if(startPrompt) startPrompt.style.visibility=priorVisibility; leave(); }
  }
  async function propose() {
    if (!selected || !enter('review')) return;
    message='Sending the saved closing frames and action timings to Mubit…'; paint();
    try {
      const result=await request(path('/proposal'),{}); selected=result.experiment || (await request(path())).experiment;
      message=selected.proposal.status==='succeeded' ? 'Mubit’s one proposed change is saved. Test it on both courses.' : 'The review outcome is recorded below.';
    } catch(error) {
      message=error.message;
      try { selected=(await request(path())).experiment; } catch (_) { message+=' Refresh the saved record before taking another action.'; if(selected.proposal) selected.proposal.status='pending'; }
    } finally { leave(); window.DinoMubit?.refreshStatus(); }
  }
  async function retrySave() {
    if (busy || !pending) return;
    busy=true; own('experiment'); paint();
    try { await savePending(); message='The recorded run is now saved. Continue with the next available step.'; }
    catch(error) { message=error.message+' The unsaved run is still available to download.'; }
    finally { leave(); }
  }
  function downloadPending() {
    if (!pending) return;
    const url=URL.createObjectURL(new Blob([JSON.stringify(pending,null,2)],{type:'application/json'}));
    const a=node('a'); a.href=url; a.download=`dino-unsaved-${pending.record.runId}.json`; a.click(); setTimeout(()=>URL.revokeObjectURL(url),1000);
  }
  function init() {
    const panel=node('section',null,'experiment-panel'); panel.setAttribute('aria-label','Mubit comparison');
    const head=node('div',null,'experiment-head'); head.append(node('h2','Does Mubit’s advice help?'),node('span','Two-course pilot','experiment-badge'));
    const intro=node('p','A local player handles the jumps. Mubit reviews the failures between runs and proposes one timing change. Both settings face the same courses at normal speed.');
    const identity=node('p','', 'lab-muted'), steps=node('p','', 'experiment-steps');
    const controls=node('div',null,'experiment-controls');
    const fresh=button('New comparison',create), baseline=button('Run baseline courses',()=>runBatch(['baseline-a','repeat-a','baseline-b'])), proposeButton=button('Ask Mubit for one change',propose), candidate=button('Test proposed change',()=>runBatch(['candidate-a','candidate-b']));
    const stop=button('Stop comparison',()=>{stopped=true; window.DinoExperimentRunner?.stop(); message='Stopping and preserving this run…'; paint();});
    const reuse=button('New review from saved baselines',reuseBaselines);
    controls.append(fresh,baseline,proposeButton,candidate,stop,reuse);
    const phase=node('p','', 'experiment-phase'); phase.setAttribute('role','status');
    const table=node('table'), thead=node('thead'), row=node('tr'); ['Run','Score','Survived','Result','Evidence'].forEach(t=>row.append(node('th',t))); thead.append(row); const tbody=node('tbody'); table.append(thead,tbody);
    const tableWrap=node('div',null,'experiment-table'); tableWrap.append(table);
    const proposal=node('div',null,'experiment-proposal'), results=node('div',null,'experiment-results');
    const retry=button('Retry local save',retrySave), unsaved=button('Download unsaved run',downloadPending), download=node('a','Export comparison','lab-button'); download.setAttribute('download','');
    const history=node('select'); history.setAttribute('aria-label','Saved comparisons'); history.addEventListener('change',()=>{if(history.value) refreshList(history.value);});
    const refresh=button('Refresh comparisons',()=>refreshList()); const footer=node('div',null,'experiment-controls'); footer.append(history,refresh,download,retry,unsaved);
    const warning=node('p','','lab-error'), evidence=node('details',null,'experiment-evidence'); evidence.hidden=true; evidence.open=true;
    const evidenceTitle=node('summary'), frames=node('div',null,'experiment-frames'), evidenceNote=node('p','','lab-muted'), actions=node('ol',null,'run-actions'); evidence.append(evidenceTitle,frames,evidenceNote,actions);
    panel.append(head,intro,identity,steps,controls,phase,tableWrap,proposal,results,evidence,footer,warning,node('p','Up to 60 seconds per run. Course A is repeated to check reproducibility. Keep this tab visible and the window size unchanged during each run.','lab-muted'));
    document.getElementById('game-stage').after(panel);
    ui={panel,new:fresh,baseline,propose:proposeButton,candidate,stop,reuse,phase,identity,steps,tbody,proposal,results,retry,unsaved,export:download,history,refresh,warning,evidence,evidenceTitle,frames,evidenceNote,actions};
    for(const type of ['keydown','keyup','mousedown','mouseup','touchstart','touchend']) panel.addEventListener(type,event=>event.stopPropagation());
    window.addEventListener('dino-controller-request',event=>{if(busy && event.detail?.owner!=='experiment') event.preventDefault();});
    window.addEventListener('dino-controller-change',event=>{if(busy && !event.detail?.owner) queueMicrotask(()=>{if(busy)own('experiment');}); paint();});
    window.addEventListener('dino-mubit-status',paint);
    window.addEventListener('dino-experiment-progress',event=>{if(!busy || !batch) return; const p=event.detail; ui.phase.textContent=`${labels[p.slot]} · score ${p.score} · ${(p.ticks/60).toFixed(1)} / 60 seconds`;});
    window.addEventListener('beforeunload',event=>{if(pending || busy) {event.preventDefault(); event.returnValue='';}});
    paint(); refreshList();
  }
  window.DinoExperiments=Object.freeze({
    get busy() { return busy || loading || Boolean(pending); },
    get selectedId() { return selected?.id || null; },
    async open(id) {
      if(!ui || typeof id!=='string' || !/^[A-Za-z0-9_-]{1,80}$/.test(id) || busy || loading || pending || window.DinoMubit?.busy || window.DinoLocalPlayer?.active) return false;
      await refreshList(id);
      if(selected?.id!==id) return false;
      ui.panel.scrollIntoView({behavior:'smooth',block:'start'});
      return true;
    }
  });
  if(document.readyState==='loading') document.addEventListener('DOMContentLoaded',init,{once:true}); else init();
})();
