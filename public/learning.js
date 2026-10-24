/* Corrections and exports stay local. A saved correction is not a model update. */
(() => {
  'use strict';
  const names = {jumpLeadMs:'Jump lead time',duckLeadMs:'Duck lead time',duckExtraMs:'Extra duck time',jumpHoldMs:'Jump hold time'};
  const bounds = {jumpLeadMs:[100,240],duckLeadMs:[200,500],duckExtraMs:[0,250],jumpHoldMs:[120,350]};
  const kinds = {timing_change:'Timing change',no_change:'No useful change',insufficient_evidence:'Insufficient evidence'};
  let ui, selected=null, status=null, busy=false, loading=false, dirty=false, version=0, message='Reading saved correction records…', error='';
  let formBase='', cases=[], experiments=[], publishedCase='';
  const n=(tag,text,cls)=>{const el=document.createElement(tag);if(text!=null)el.textContent=text;if(cls)el.className=cls;return el;};
  const button=(label,fn)=>{const el=n('button',label);el.type='button';el.addEventListener('click',fn);return el;};
  const option=(label,value)=>{const el=n('option',label);el.value=value;return el;};
  const date=value=>value ? new Date(value).toLocaleString('en-GB') : 'Not recorded';
  const brief=id=>String(id || '').slice(0,12);
  const casePath=(id,suffix='')=>`/api/learning/cases/${encodeURIComponent(id)}${suffix}`;
  const answerReviewBlocked=()=>Boolean(window.DinoReviewTask?.dirty || window.DinoReviewTask?.targetBusy);
  const selectedCase=()=>selected ? {id:selected.id,revision:selected.revision,approved:selected.approval?.status==='approved'} : null;
  function publishCase() {
    const detail={case:selectedCase(),dirty,busy:busy || loading}, signature=JSON.stringify(detail);
    if(signature===publishedCase)return;
    publishedCase=signature;window.dispatchEvent(new CustomEvent('dino-learning-case-changed',{detail}));
  }
  async function request(path,payload) {
    const response=await fetch(path,payload===undefined ? {cache:'no-store'} : {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
    const data=await response.json();
    if(!response.ok || !data.ok) {const failure=new Error(data.message || 'This local record could not be read or saved.');failure.code=data.code;throw failure;}
    return data;
  }
  function recommendation() {
    const value={kind:ui.kind.value,rationale:ui.rationale.value,caveat:ui.caveat.value,
      evidenceRefs:[...ui.evidence.querySelectorAll('input[type="checkbox"]:checked')].map(el=>el.value)};
    if(value.kind==='timing_change') {value.parameter=ui.parameter.value;value.value=Number(ui.value.value);}
    return value;
  }
  function formSnapshot() {return JSON.stringify({recommendation:recommendation(),reason:ui.reason.value});}
  function updateDirty() {dirty=Boolean(selected) && formSnapshot()!==formBase;ui.checked.checked=false;paint();}
  function timingFields() {
    ui.timing.hidden=ui.kind.value!=='timing_change';
    const range=bounds[ui.parameter.value];ui.value.min=String(range[0]);ui.value.max=String(range[1]);ui.value.step='10';
    ui.range.textContent=`${range[0]}–${range[1]} milliseconds, in steps of 10.`;
  }
  function formError() {
    if(!selected) return 'Choose a correction case first.';
    const item=recommendation();
    if(!item.rationale.trim() || !item.caveat.trim() || !ui.reason.value.trim()) return 'Add the corrected review, its uncertainty and the reason for the correction.';
    if(item.rationale.length>600 || item.caveat.length>600) return 'Keep the corrected review and uncertainty to 600 characters each.';
    if(item.evidenceRefs.length<1 || item.evidenceRefs.length>12) return 'Select between one and twelve evidence items.';
    if(item.kind==='timing_change') {
      const [min,max]=bounds[item.parameter];
      if(!Number.isInteger(item.value) || item.value<min || item.value>max || item.value%10!==0) return 'Choose a timing value within the displayed range, in steps of 10 milliseconds.';
      if(item.value===selected.source?.baselineConfig?.[item.parameter]) return 'A timing change must differ from the recorded baseline.';
    }
    return '';
  }
  function paint() {
    if(!ui)return;
    const blocked=busy || loading || answerReviewBlocked();
    const savedDraft=selected?.latestDraft, approved=selected?.approval?.status==='approved';
    ui.refresh.disabled=blocked || dirty;ui.importSelect.disabled=blocked || dirty;ui.caseSelect.disabled=blocked || dirty;
    ui.importButton.disabled=blocked || dirty || !ui.importSelect.value;
    ui.planSelect.disabled=blocked || dirty;ui.prepare.disabled=blocked || dirty || !ui.planSelect.value || Boolean(window.DinoExperiments?.busy);
    ui.editor.hidden=!selected;
    ui.save.disabled=blocked || !dirty;
    ui.suggest.disabled=blocked || dirty || !selected;
    ui.approve.disabled=blocked || dirty || !savedDraft || approved || !ui.checked.checked;
    ui.checked.disabled=blocked || dirty || !savedDraft || approved;
    ui.discard.disabled=blocked;ui.downloadDraft.disabled=blocked;
    ui.recovery.hidden=!dirty;
    for(const el of ui.form.querySelectorAll('input:not([type="checkbox"]),select,textarea'))el.disabled=blocked;
    for(const el of ui.evidence.querySelectorAll('input'))el.disabled=blocked;
    ui.phase.textContent=message;ui.error.textContent=error;
    ui.countCases.textContent=status ? String(status.caseCount) : '—';
    ui.countApproved.textContent=status ? String(status.approvedCount) : '—';
    ui.countHeldOut.textContent=status?.plan ? String(status.plan.evaluationTarget ?? 10) : '—';
    ui.export.setAttribute('aria-disabled',String(blocked || !status || status.approvedCount<1));
    ui.exportHint.textContent=status?.approvedCount ? 'Exports approved reviews of already-tested advice, with the original answer, measured outcomes and referenced evidence. This local JSON format is not a verified Mubit upload format.' : 'An approved correction is required before export. Each exported example includes the original advice and its measured outcome. Drafts and reserved test cases are excluded.';
    ui.providerTitle.textContent='Training connection not verified';
    ui.providerDetail.textContent=status?.provider?.message || 'Saved corrections stay local. The route from approved corrections to Mubit training has not been verified.';
    ui.modelComparison.textContent='No verified updated Mubit model yet. A before-and-after Mubit model result will be shown only when a real model version and its evaluation are available.';
    if(selected) {
      ui.caseTitle.textContent=`Correction case ${brief(selected.id)}`;
      ui.caseMeta.textContent=`Training evidence · revision ${selected.revision} · source comparison ${brief(selected.sourceExperimentId)}`;
      ui.draftStatus.textContent=dirty ? 'Unsaved edits. Save a draft before approving it.' : savedDraft ? `${approved ? 'Approved locally' : 'Saved draft'} · ${savedDraft.origin==='machine-drafted' ? 'prepared automatically from measured results' : 'submitted through the local correction form'} · ${date(savedDraft.createdAt)}` : 'No correction draft saved yet. You can write one or prepare a draft from the measured result.';
      ui.approvalStatus.textContent=approved ? `Approved locally on ${date(selected.approval.approvedAt)}. No signed-in reviewer identity was verified. This has not updated a model.` : 'Approval makes this saved revision eligible for local training export. It does not send it to Mubit or start training.';
    }
    publishCase();
  }
  function fillSelectors() {
    const currentImport=ui.importSelect.value, currentPlan=ui.planSelect.value;
    ui.importSelect.replaceChildren(option('Choose a completed comparison',''));
    for(const item of experiments) {
      if(item.proposalStatus!=='succeeded' || item.comparison?.status!=='complete')continue;
      ui.importSelect.append(option(`${brief(item.id)} · ${date(item.createdAt)}`,item.id));
    }
    if([...ui.importSelect.options].some(item=>item.value===currentImport))ui.importSelect.value=currentImport;
    else if(ui.importSelect.options.length===2)ui.importSelect.selectedIndex=1;
    ui.caseSelect.replaceChildren(option('Choose a saved correction case',''));
    for(const item of cases)ui.caseSelect.append(option(`${brief(item.id)} · ${item.latestRevisionStatus || 'no draft'} · ${date(item.createdAt)}`,item.id));
    if(selected)ui.caseSelect.value=selected.id;
    ui.planSelect.replaceChildren(option('Choose a reserved scenario',''));
    const plan=status?.plan;
    const scenarios=[...(plan?.trainingCases || []),...(plan?.evaluationCases || [])];
    for(const item of scenarios) {
      const split=item.split==='evaluation' ? 'Reserved test' : 'Training';
      ui.planSelect.append(option(`${split} · ${item.id}${item.status==='ready' ? ' · prepared' : item.status==='outcome_unknown' ? ' · needs review' : ''}`,item.id));
    }
    if([...ui.planSelect.options].some(item=>item.value===currentPlan))ui.planSelect.value=currentPlan;
    else if(scenarios.length)ui.planSelect.value=(scenarios.find(item=>item.split==='training' && item.status==='not_started') || scenarios.find(item=>item.split==='training') || scenarios[0]).id;
    ui.planSummary.textContent=plan ? `${plan.trainingTarget ?? 40} training scenarios and ${plan.evaluationTarget ?? 10} reserved test scenarios. Their courses are separated before collection. Test evidence is excluded from training imports and exports.` : 'The course split could not be read yet.';
    ui.planData.textContent=plan ? JSON.stringify(plan,null,2) : '';
    const unavailable=status?.unavailableCount;
    ui.recordsWarning.textContent=unavailable ? `${unavailable} local correction record(s) could not be read. They have been preserved.` : '';
  }
  function showOriginal() {
    const source=selected.source, original=source.originalAnswer;
    ui.original.replaceChildren(n('h3','Original Mubit answer'),n('p',`${names[original.parameter] || original.parameter}: ${source.baselineConfig?.[original.parameter]} → ${original.value} ms`),n('p',original.rationale),n('p',original.caveat,'learning-note'));
    const comparison=source.comparison, table=n('table',null,'learning-pairs'), head=n('thead'), header=n('tr');
    ['Course','Baseline','Tested advice'].forEach(text=>header.append(n('th',text)));head.append(header);table.append(head);const body=n('tbody');
    for(const pair of comparison?.pairs || []) {
      const row=n('tr');row.append(n('td',String(pair.course).toUpperCase()),n('td',`${pair.baselineScore} points · ${(pair.baselineTicks/60).toFixed(2)}s`),n('td',`${pair.candidateScore} points · ${(pair.candidateTicks/60).toFixed(2)}s`));body.append(row);
    }
    table.append(body);ui.original.append(n('h3','Observed result'),table);
    const conclusion={unchanged:'The tested advice did not change these results.',improved_on_both:'The tested setting survived longer on both courses.',worse_on_both:'The tested setting survived for less time on both courses.',mixed:'The results differed between the two courses.'};
    ui.original.append(n('p',conclusion[comparison?.aggregate?.conclusion] || comparison?.reason || 'Read the recorded comparison above.'),n('p','Two courses do not establish a general cause or a generally better player.','learning-note'));
    const provenance=n('details'), summary=n('summary','Source record');provenance.append(summary,n('p',`Model: ${source.model || 'Not recorded'}`,'learning-note'),n('p',`Request: ${source.requestId || 'Not recorded'}`,'learning-note'),n('p',`Original experiment: ${selected.sourceExperimentId}`,'learning-note'));
    const download=n('a','Export source comparison','learning-button');download.href=`/api/experiments/${encodeURIComponent(selected.sourceExperimentId)}/export`;download.setAttribute('download','');provenance.append(download);ui.original.append(provenance);
  }
  function addEvidenceCheck(item,parent) {
    const label=n('label',null,'learning-check'), input=n('input');input.type='checkbox';input.value=item.id;input.addEventListener('change',updateDirty);
    label.append(input,n('span',item.label || `${item.slot || ''} · ${(Number(item.tMs || 0)/1000).toFixed(2)}s · ${item.action || item.type}`));parent.append(label);
    return input;
  }
  function showEvidence() {
    ui.evidence.replaceChildren();
    const summary=n('summary','Evidence for this correction');ui.evidence.append(summary,n('p','Select one to twelve recorded items that support the correction. These references are saved with the draft.','learning-note'));
    const overview=n('div'), frames=n('div',null,'learning-frames'), actions=n('div',null,'learning-actions');
    for(const item of selected.evidence || []) {
      if(item.image) {
        const figure=n('figure'), image=n('img');image.src=item.image;image.alt=item.label || 'Saved game evidence';figure.append(image);addEvidenceCheck(item,figure);frames.append(figure);
      } else if(item.type==='action' || item.action) addEvidenceCheck(item,actions);
      else addEvidenceCheck(item,overview);
    }
    ui.evidence.append(overview,frames);
    if(actions.children.length) {const detail=n('details');detail.append(n('summary','Recorded actions'),actions);ui.evidence.append(detail);}
  }
  function populateForm() {
    const draft=selected.latestDraft, rec=draft?.recommendation;
    ui.kind.value=rec?.kind || 'insufficient_evidence';
    ui.parameter.value=rec?.parameter || selected.source.originalAnswer.parameter || 'jumpLeadMs';
    ui.value.value=String(rec?.value ?? selected.source.originalAnswer.value ?? 170);
    ui.rationale.value=rec?.rationale || '';ui.caveat.value=rec?.caveat || '';ui.reason.value=draft?.reason || '';
    const refs=new Set(rec?.evidenceRefs || []);
    for(const checkbox of ui.evidence.querySelectorAll('input'))checkbox.checked=refs.has(checkbox.value);
    timingFields();ui.checked.checked=false;dirty=false;formBase=formSnapshot();
    ui.history.replaceChildren();
    for(const item of selected.revisions || []) {
      ui.history.append(n('li',item.action==='approve' ? `Revision ${item.revision}: approved draft ${item.draftRevision} locally · ${date(item.createdAt)} · reviewer identity not verified.` : `Revision ${item.revision}: ${item.origin==='machine-drafted' ? 'automatically prepared draft' : 'submitted draft'} · ${date(item.createdAt)} · ${kinds[item.recommendation?.kind] || 'Correction'}. ${item.reason || ''}`));
    }
    if(!ui.history.children.length)ui.history.append(n('li','No saved correction revisions.'));
  }
  function acceptCase(value) {selected=value;showOriginal();showEvidence();populateForm();ui.caseSelect.value=value.id;paint();}
  async function loadCase(id) {
    if(busy || loading || dirty || answerReviewBlocked() || !id){if(selected)ui.caseSelect.value=selected.id;return;}
    const token=++version;loading=true;error='';message='Reading the original evidence and saved corrections…';paint();
    try {
      const data=await request(casePath(id));if(token!==version)return;
      acceptCase(data.case);message='Saved case loaded. Review the evidence before approving a correction.';
    } catch(failure) {if(token===version){selected=null;error=failure.message;message='The case is unavailable. Its record has not been changed.';}}
    finally {if(token===version){loading=false;paint();}}
  }
  async function refresh(selectedId) {
    if(busy || dirty || loading || answerReviewBlocked())return;
    const token=++version;loading=true;error='';paint();
    try {
      const all=await Promise.all([request('/api/learning/status'),request('/api/learning/cases'),request('/api/experiments')]);if(token!==version)return;
      status=all[0].status;cases=all[1].cases;experiments=all[2].experiments;fillSelectors();
      const id=selectedId || selected?.id || cases[0]?.id;
      if(id){const data=await request(casePath(id));if(token!==version)return;acceptCase(data.case);message='Saved corrections are ready to review.';}
      else {selected=null;message='Import a completed comparison to prepare its first correction.';}
    } catch(failure){if(token===version){status=null;error=failure.message;message='Some local records could not be read. Counts are unknown; refresh to try again.';}}
    finally{if(token===version){loading=false;paint();}}
  }
  async function updateCounts() {
    const [counts,listing]=await Promise.all([request('/api/learning/status'),request('/api/learning/cases')]);
    status=counts.status;cases=listing.cases;fillSelectors();
  }
  async function mutate(action,payload) {
    if(!selected || busy || loading || answerReviewBlocked())return;
    if(action!=='draft' && dirty)return;
    const id=selected.id, token=++version;busy=true;error='';message='Saving this local correction record…';paint();
    try {
      const data=await request(casePath(id,'/'+action),payload);if(token!==version)return;
      acceptCase(data.case);
      message=action==='approve' ? 'This revision is approved for local training export. No model update has been requested.' : action==='result-draft' ? 'A draft was prepared from the measured result. It needs review before approval.' : 'Draft saved. Review it and the selected evidence before approval.';
      try{await updateCounts();}catch(_){error='The correction was saved, but summary counts could not be refreshed. Use Refresh records.';}
    } catch(failure){if(token===version){error=failure.message;message='The correction could not be saved. Your form edits remain here.';if(action==='draft')dirty=true;}}
    finally{if(token===version){busy=false;paint();}}
  }
  function saveDraft() {
    const invalid=formError();if(invalid){error=invalid;paint();return;}
    mutate('draft',{expectedRevision:selected.revision,recommendation:recommendation(),reason:ui.reason.value});
  }
  function approve() {
    if(!ui.checked.checked || dirty || !selected?.latestDraft)return;
    mutate('approve',{expectedRevision:selected.revision});
  }
  async function importCase() {
    if(busy || loading || dirty || answerReviewBlocked() || !ui.importSelect.value)return;
    const token=++version,id=ui.importSelect.value;busy=true;error='';message='Copying the completed comparison into the local correction record…';paint();
    try {
      const data=await request(`/api/learning/experiments/${encodeURIComponent(id)}/import`,{});if(token!==version)return;
      acceptCase(data.case);message=data.alreadyImported ? 'This evidence was already imported. The existing correction case is open.' : 'Original evidence imported. No correction has been approved and nothing was sent to Mubit.';
      try{await updateCounts();}catch(_){error='The case was imported, but summary counts could not be refreshed.';}
    } catch(failure){if(token===version){error=failure.message;message='The comparison could not be imported.';}}
    finally{if(token===version){busy=false;paint();}}
  }
  async function prepareCourses() {
    if(busy || loading || dirty || answerReviewBlocked() || !ui.planSelect.value)return;
    const token=++version,id=ui.planSelect.value;busy=true;error='';message='Preparing the reserved courses locally…';paint();
    try {
      const data=await request(`/api/learning/plan/${encodeURIComponent(id)}/experiment`,{});if(token!==version)return;
      const experimentId=data.experiment?.id || data.experimentId;
      if(!experimentId)throw new Error('The prepared comparison could not be identified. Refresh before continuing.');
      let opened=false;
      if(window.DinoExperiments?.open)opened=await window.DinoExperiments.open(experimentId);
      else window.dispatchEvent(new CustomEvent('dino-experiment-prepared',{detail:{id:experimentId}}));
      message=opened ? 'Courses prepared. Use the comparison panel to run its baselines; no game or model call was started automatically.' : `Courses prepared as ${brief(experimentId)}. The comparison panel is busy; open this saved comparison when it finishes. No game or model call was started automatically.`;
      try{await updateCounts();}catch(_){error='The courses were prepared, but the plan status could not be refreshed.';}
    } catch(failure){if(token===version){error=failure.message;message='The reserved courses could not be prepared or opened. Existing evidence is preserved.';}}
    finally{if(token===version){busy=false;paint();}}
  }
  function downloadDraft() {
    if(!selected)return;
    const data={format:'dino-unsaved-correction-v1',caseId:selected.id,expectedRevision:selected.revision,recommendation:recommendation(),reason:ui.reason.value,approved:false};
    const url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json'}));const a=n('a');a.href=url;a.download=`dino-unsaved-correction-${selected.id}.json`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  }
  function discardAndReload() {if(!selected || busy || loading || answerReviewBlocked())return;const id=selected.id;dirty=false;loadCase(id);}
  function field(label,control) {const wrapper=n('label',null,'learning-field');wrapper.append(n('span',label),control);return wrapper;}
  function textarea(label) {const input=n('textarea');input.maxLength=600;input.setAttribute('aria-label',label);input.addEventListener('input',updateDirty);return input;}
  function init() {
    const panel=n('section',null,'learning-panel');panel.id='learning-panel';panel.setAttribute('aria-label','Track 1 corrections');
    const head=n('div',null,'learning-head');head.append(n('h2','Correct reviews of tested advice'),n('span','Track 1 preparation','learning-badge'));
    const counts=n('div',null,'learning-counts'), countCases=n('strong','—'),countApproved=n('strong','—'),countHeldOut=n('strong','—');
    for(const [amount,label] of [[countCases,'Imported correction cases'],[countApproved,'Approved for local export'],[countHeldOut,'Reserved test scenarios']]){const card=n('div',null,'learning-count');card.append(amount,n('span',label));counts.append(card);}
    const provider=n('div',null,'learning-status'),providerTitle=n('strong'),providerDetail=n('p',null,'learning-note');provider.append(providerTitle,providerDetail);
    const controls=n('div',null,'learning-controls'),importSelect=n('select');importSelect.setAttribute('aria-label','Completed comparison to import');importSelect.addEventListener('change',paint);
    const importButton=button('Import comparison evidence',importCase),caseSelect=n('select');caseSelect.setAttribute('aria-label','Saved correction cases');caseSelect.addEventListener('change',()=>loadCase(caseSelect.value));
    const refreshButton=button('Refresh records',()=>refresh());controls.append(importSelect,importButton,caseSelect,refreshButton);
    const phase=n('p',null,'learning-phase');phase.setAttribute('role','status');const errorNode=n('p',null,'learning-warning');errorNode.setAttribute('role','alert');const recordsWarning=n('p',null,'learning-warning');
    const editor=n('div',null,'learning-editor'),caseTitle=n('h3'),caseMeta=n('p',null,'learning-note'),columns=n('div',null,'learning-columns'),original=n('div',null,'learning-original'),form=n('div');
    const kind=n('select');kind.setAttribute('aria-label','Corrected recommendation');for(const [value,label]of Object.entries(kinds))kind.append(option(label,value));kind.addEventListener('change',()=>{timingFields();updateDirty();});
    const parameter=n('select');parameter.setAttribute('aria-label','Timing parameter');for(const [value,label]of Object.entries(names))parameter.append(option(label,value));parameter.addEventListener('change',()=>{timingFields();updateDirty();});
    const value=n('input');value.type='number';value.setAttribute('aria-label','Corrected timing in milliseconds');value.addEventListener('input',updateDirty);
    const timing=n('div',null,'learning-timing');timing.append(field('Timing parameter',parameter),field('Milliseconds',value));const range=n('p',null,'learning-note');timing.append(range);
    const rationale=textarea('Corrected review'),caveat=textarea('Uncertainty or limits'),reason=textarea('Why this correction is needed');
    const evidence=n('details',null,'learning-evidence');
    const suggest=button('Draft from measured result',()=>mutate('result-draft',{expectedRevision:selected.revision})),save=button('Save correction draft',saveDraft);
    const draftControls=n('div',null,'learning-controls');draftControls.append(suggest,save);const draftStatus=n('p',null,'learning-note');
    const approval=n('div',null,'learning-approval'),checked=n('input');checked.type='checkbox';checked.addEventListener('change',paint);const checkLabel=n('label',null,'learning-check');checkLabel.append(checked,n('span','I have checked this saved correction against the selected evidence.'));
    const approveButton=button('Approve this saved correction',approve),approvalStatus=n('p',null,'learning-note');approval.append(checkLabel,approveButton,approvalStatus);
    form.append(n('h3','Review the advice after its test'),n('p','Use the recorded outcomes to assess the original recommendation. This correction reviews advice that has already been tested.','learning-note'),field('Recommendation',kind),timing,field('Corrected review',rationale),field('Uncertainty or limits',caveat),field('Why this correction is needed',reason),evidence,draftControls,draftStatus,approval);
    columns.append(original,form);
    const historyDetails=n('details'),history=n('ol',null,'learning-history');historyDetails.append(n('summary','Saved correction history'),history);
    const recovery=n('div',null,'learning-recovery'),recoveryButtons=n('div',null,'learning-controls'),downloadDraftButton=button('Download unsaved draft',downloadDraft),discard=button('Discard edits and reload saved case',discardAndReload);recoveryButtons.append(downloadDraftButton,discard);recovery.append(n('p','Unsaved edits are kept in this tab. Save or download them before changing cases.'),recoveryButtons);
    editor.append(caseTitle,caseMeta,recovery,columns,historyDetails);
    const plan=n('details',null,'learning-plan'),planSummary=n('p',null,'learning-note'),planSelect=n('select');planSelect.setAttribute('aria-label','Reserved training or test scenario');planSelect.addEventListener('change',paint);const prepare=button('Prepare courses',prepareCourses),planControls=n('div',null,'learning-controls');planControls.append(planSelect,prepare);const rawPlan=n('details'),planData=n('pre');rawPlan.append(n('summary','Course split record'),planData);plan.append(n('summary','Collect separate training and test evidence'),planSummary,planControls,rawPlan);
    const model=n('details',null,'learning-plan'),modelComparison=n('p',null,'learning-note');model.append(n('summary','Model update and before-and-after evaluation'),modelComparison);
    const exportLink=n('a','Export approved training examples','learning-button');exportLink.href='/api/learning/export';exportLink.setAttribute('download','');exportLink.addEventListener('click',event=>{if(exportLink.getAttribute('aria-disabled')==='true')event.preventDefault();});const exportHint=n('p',null,'learning-note'),footer=n('div',null,'learning-controls');footer.append(exportLink);
    panel.append(head,n('p','Review Mubit’s advice alongside its measured results, record a correction and approve the exact revision you want to use. The local training example includes the original answer and observed outcomes. Saving and approval stay local; they do not train a model.'),counts,provider,controls,phase,errorNode,recordsWarning,editor,plan,model,footer,exportHint);
    const anchor=document.querySelector('.experiment-panel') || document.getElementById('game-stage');if(anchor)anchor.after(panel);else document.body.append(panel);
    ui={panel,countCases,countApproved,countHeldOut,providerTitle,providerDetail,importSelect,importButton,caseSelect,refresh:refreshButton,phase,error:errorNode,recordsWarning,editor,caseTitle,caseMeta,original,form,kind,parameter,value,timing,range,rationale,caveat,reason,evidence,suggest,save,draftStatus,checked,approve:approveButton,approvalStatus,history,recovery,downloadDraft:downloadDraftButton,discard,planSelect,prepare,planSummary,planData,modelComparison,export:exportLink,exportHint};
    for(const eventName of ['keydown','keyup','mousedown','mouseup','touchstart','touchend'])panel.addEventListener(eventName,event=>event.stopPropagation());
    window.addEventListener('beforeunload',event=>{if(dirty || busy){event.preventDefault();event.returnValue='';}});
    window.addEventListener('dino-controller-change',paint);
    window.addEventListener('dino-review-target-state',paint);
    window.addEventListener('dino-experiment-updated',()=>{if(!busy && !loading && !dirty)refresh();});
    window.DinoLearning=Object.freeze({refresh:()=>refresh(),get dirty(){return dirty;},get busy(){return busy || loading;},get selectedCase(){return selectedCase();}});
    paint();refresh();
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init,{once:true});else init();
})();
