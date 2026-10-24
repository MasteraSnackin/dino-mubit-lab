/* One explicit, bounded provider comparison. Keys are never retained by the UI. */
(() => {
  'use strict';
  const endpoint='/api/provider-comparison';
  const states=new Set(['not_started','pending','succeeded','failed','unknown']);
  let ui,record=null,reading=false,requesting=false,uncertain=false,readVersion=0,poll=null,closed=false;
  let phase='Reading the comparison setup…',error='';
  const n=(tag,text,cls)=>{const element=document.createElement(tag);if(text!=null)element.textContent=text;if(cls)element.className=cls;return element;};
  const button=(text,action)=>{const element=n('button',text);element.type='button';element.addEventListener('click',action);return element;};
  const editorBlocked=()=>Boolean(window.DinoLearning?.dirty || window.DinoLearning?.busy || window.DinoReviewTask?.dirty || window.DinoReviewTask?.targetBusy);
  const otherWork=()=>Boolean(window.DinoLocalPlayer?.active || window.DinoExperiments?.busy || window.DinoMubit?.busy || window.DinoReviewTask?.busy || window.DinoLearningModes?.busy || (window.Runner?.instance_?.playing && !window.Runner.instance_.crashed));
  const active=()=>requesting || uncertain || record?.latest?.status==='pending';
  const count=value=>Number.isInteger(value)&&value>=0?String(value):'Not recorded';
  const number=value=>Number.isFinite(value)&&value>=0?value.toFixed(1):'Not recorded';
  const hasKey=()=>typeof ui?.key.value==='string' && /^sk-[\x21-\x7e]{8,4092}$/.test(ui.key.value.trim());
  const completed=()=>record?.latest?.pairs?.flatMap(pair=>[pair.mubit,pair.direct]).filter(arm=>arm && arm.status!=='pending').length || 0;
  function cancelPoll(){if(poll!==null){clearTimeout(poll);poll=null;}}
  function schedulePoll(){cancelPoll();if(!closed && requesting)poll=setTimeout(()=>{poll=null;refresh();},2000);}
  function accept(data) {
    const value=data?.comparison;
    if(!value || !states.has(value.status) || typeof value.ready!=='boolean' || (value.latest && (typeof value.latest.id!=='string' || !states.has(value.latest.status) || !Array.isArray(value.latest.pairs))))throw new Error('Invalid comparison response.');
    if(value.ready && (!value.preview || !/^[a-f0-9]{64}$/.test(value.preview.inputHash || '') || value.preview.pairCount!==3 || value.preview.requestCount!==6 || value.preview.mubitRequestCount!==3 || typeof value.preview.requestedModel!=='string'))throw new Error('Invalid comparison preview.');
    record=value;
    if(value.latest)uncertain=!['succeeded','failed'].includes(value.latest.status);
    phase=value.message || 'The saved comparison state is available.';
  }
  function paint() {
    if(!ui)return;
    const saved=Boolean(record?.latest),blocked=reading || requesting || uncertain || editorBlocked() || otherWork();
    ui.run.disabled=blocked || !record?.ready || saved || !hasKey();
    ui.key.disabled=requesting || saved || uncertain;
    if(saved || uncertain)ui.key.value='';
    ui.refresh.disabled=reading;
    ui.export.hidden=record?.exportAvailable!==true;
    ui.export.setAttribute('aria-disabled',String(reading || requesting || record?.exportAvailable!==true));
    ui.phase.textContent=uncertain&&!requesting&&record?.latest?.status!=='pending'?'The request outcome is unknown. Refresh the saved result; no repeat request is offered.':phase;
    ui.error.textContent=error;
    ui.blocker.textContent=editorBlocked()?'Finish saving or discarding your review edits before starting this comparison.':otherWork()?'Wait for the current game or model action to finish.':'';
    const preview=record?.preview;
    ui.preview.hidden=!preview;
    ui.previewText.textContent=preview?JSON.stringify(preview.body,null,2):'';
    ui.identity.textContent=preview?`Model: ${preview.requestedModel} · saved case ${preview.caseId} · three matched pairs`:'The saved input and model have not been verified.';
    ui.results.replaceChildren();
    if(!record?.latest){ui.results.append(n('p',record?'No comparison attempt is saved.':'The saved comparison state is unknown.','provider-comparison-note'));return;}
    const latest=record.latest;
    const labels={pending:'Comparison in progress',succeeded:'Comparison results saved',failed:'Comparison stopped · attempt preserved',unknown:'Comparison outcome unknown'};
    ui.results.append(n('h3',labels[latest.status] || 'Saved comparison'),n('p',`${completed()} of 6 request outcomes recorded.`,'provider-comparison-note'));
    const summary=latest.summary;
    if(summary?.allPairsValid===true && summary.mubit && summary.direct) {
      const wrap=n('div',null,'provider-comparison-table'),table=n('table'),head=n('tr');
      table.append(n('caption','Observed averages across three matched pairs'));
      for(const label of ['Route','Input tokens','Output tokens','Total tokens','Time (ms)'])head.append(n('th',label));
      table.append(head);
      for(const [label,value] of [['Through Mubit',summary.mubit],['OpenAI directly',summary.direct]]) {
        const row=n('tr');row.append(n('th',label),n('td',number(value.meanPromptTokens)),n('td',number(value.meanCompletionTokens)),n('td',number(value.meanTotalTokens)),n('td',number(value.meanLatencyMs)));table.append(row);
      }
      wrap.append(table);ui.results.append(wrap);
      for(const [label,value] of [['Through Mubit',summary.mubit],['OpenAI directly',summary.direct]])ui.results.append(n('p',`${label}: valid review format ${count(value.jsonValidCount)}/3; correct outcome ${count(value.assessmentCorrectCount)}/3; expected decision ${count(value.decisionCorrectCount)}/3; all recorded measurements mentioned ${count(value.measurementMentionCount)}/3.`,'provider-comparison-note'));
      ui.results.append(n('p',summary.modelConsistencyVerified===true?'Both routes reported the requested model ID. This does not verify identical model weights or infrastructure.':'The returned model IDs have not been verified as matching. Treat these results as an unverified model comparison.','provider-comparison-note'));
    } else ui.results.append(n('p','A complete set of three valid pairs is required before averages are compared. Missing or failed requests are not counted as zero tokens.','provider-comparison-note'));
    if(summary?.caveat)ui.results.append(n('p',summary.caveat,'provider-comparison-note'));
    const detail=n('details');detail.append(n('summary','Individual requests and receipt'));
    detail.append(n('p',`Comparison ID: ${latest.id}`,'provider-comparison-note'));
    for(const pair of latest.pairs) {
      for(const route of ['mubit','direct']) {
        const arm=pair[route];if(!arm)continue;
        detail.append(n('p',`Pair ${pair.index} · ${route==='mubit'?'Mubit':'OpenAI directly'} · ${arm.status} · total tokens ${count(arm.usage?.totalTokens)} · time ${number(arm.latencyMs)} ms`,'provider-comparison-note'));
        if(arm.requestId)detail.append(n('p',`Request ID: ${arm.requestId}`,'provider-comparison-note'));
        if(arm.returnedModel)detail.append(n('p',`Returned model: ${arm.returnedModel}`,'provider-comparison-note'));
        if(arm.usage?.cachedPromptTokens!=null || arm.usage?.reasoningTokens!=null)detail.append(n('p',`Reported cached input tokens: ${count(arm.usage?.cachedPromptTokens)} · reasoning tokens: ${count(arm.usage?.reasoningTokens)}`,'provider-comparison-note'));
      }
    }
    ui.results.append(detail,n('p','This is one saved example repeated three times. It does not establish general quality, speed or cost savings. The checks compare format, the measured outcome and simple measurement mentions; they do not grade every part of the explanation.','provider-comparison-note'),n('p','Refreshing or reopening this page does not repeat the six requests.','provider-comparison-note'));
  }
  async function refresh() {
    if(reading || closed)return;
    const version=++readVersion;reading=true;error='';paint();
    try {
      const response=await fetch(endpoint,{cache:'no-store'}),data=await response.json();
      if(version!==readVersion || closed)return;
      if(!response.ok || !data.ok)throw new Error('Read failed.');
      accept(data);
    } catch(unused) {
      if(version===readVersion && !closed){record=null;phase='The saved comparison state is unknown.';error='The comparison record could not be read. Refresh to check it again.';}
    } finally {
      if(version===readVersion){reading=false;paint();schedulePoll();window.dispatchEvent(new CustomEvent('dino-controller-change',{detail:{owner:active()?'provider-comparison':null}}));}
    }
  }
  async function run() {
    if(reading || requesting || uncertain || editorBlocked() || otherWork() || !record?.ready || record.latest || !hasKey())return;
    const ownership=new CustomEvent('dino-controller-request',{cancelable:true,detail:{owner:'provider-comparison'}});
    if(!window.dispatchEvent(ownership)){phase='Another game or model action is in progress.';paint();return;}
    let key=ui.key.value.trim();ui.key.value='';
    const expectedInputHash=record.preview.inputHash;
    requesting=true;uncertain=true;error='';phase='Running the six matched requests…';readVersion++;reading=false;
    window.dispatchEvent(new CustomEvent('dino-controller-change',{detail:{owner:'provider-comparison'}}));paint();schedulePoll();
    try {
      const response=await fetch(endpoint,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({openaiKey:key,expectedInputHash})});
      const data=await response.json();
      if(closed)return;
      readVersion++;reading=false;
      if(JSON.stringify(data).includes(key))throw new Error('Unsafe response.');
      if(!response.ok || !data.ok) {
        if(data.noInferenceSent===true && data.comparison?.latest===null) {
          accept(data);uncertain=false;
          error='No model request was sent. Check the setup and enter the key again when ready.';
        } else if(data.comparison?.latest)accept(data);
        else throw new Error('Request not confirmed.');
      } else accept(data);
    } catch(unused) {
      if(!closed){phase='The comparison outcome could not be confirmed. Refresh its saved receipt.';error='No request will be repeated automatically.';}
    } finally {
      key='';requesting=false;cancelPoll();
      if(!closed){paint();window.dispatchEvent(new CustomEvent('dino-controller-change',{detail:{owner:active()?'provider-comparison':null}}));window.DinoMubit?.refreshStatus();}
    }
  }
  function init() {
    const panel=n('section',null,'provider-comparison-panel');panel.id='provider-comparison-panel';panel.setAttribute('aria-label','Mubit versus OpenAI directly');
    panel.append(n('h2','Mubit versus OpenAI directly'),n('p','Compare the same saved review with the same model and request settings: three calls through Mubit and three directly to OpenAI.'));
    const identity=n('p',null,'provider-comparison-note'),preview=n('details'),previewText=n('pre');preview.append(n('summary','Preview the identical request'),previewText);
    const label=n('label','OpenAI API key for this test');label.setAttribute('for','provider-comparison-key');
    const key=n('input');key.id='provider-comparison-key';key.type='password';key.autocomplete='off';key.setAttribute('autocomplete','off');key.setAttribute('spellcheck','false');key.setAttribute('aria-describedby','provider-comparison-key-note');key.maxLength=4096;
    const keyNote=n('p','Used in memory for this test. Not saved. Sent only to OpenAI; the Mubit key stays separate.','provider-comparison-note');keyNote.id='provider-comparison-key-note';
    const runButton=button('Run six-request comparison',run),refreshButton=button('Refresh comparison result',refresh),exportLink=n('a','Export comparison receipt','provider-comparison-button');exportLink.href=endpoint+'/export';exportLink.setAttribute('download','');exportLink.addEventListener('click',event=>{if(exportLink.getAttribute('aria-disabled')==='true')event.preventDefault();});
    const controls=n('div',null,'provider-comparison-controls');controls.append(runButton,refreshButton,exportLink);
    const phaseNode=n('p',null,'provider-comparison-phase'),errorNode=n('p',null,'provider-comparison-error'),blocker=n('p',null,'provider-comparison-note'),results=n('div');phaseNode.setAttribute('role','status');errorNode.setAttribute('role','alert');
    panel.append(identity,preview,label,key,keyNote,n('p','Starting this test sends six requests and may use credit on both routes. No remembered answers are added. This test does not train a model.','provider-comparison-note'),controls,phaseNode,errorNode,blocker,results);
    const anchor=document.getElementById('learning-modes-panel');if(anchor)anchor.after(panel);else document.body.append(panel);
    ui={key,run:runButton,refresh:refreshButton,export:exportLink,identity,preview,previewText,phase:phaseNode,error:errorNode,blocker,results};
    key.addEventListener('input',paint);
    for(const event of ['keydown','keyup','mousedown','mouseup','touchstart','touchend'])panel.addEventListener(event,item=>item.stopPropagation());
    for(const event of ['dino-learning-case-changed','dino-review-target-state','dino-controller-change','dino-mubit-status'])window.addEventListener(event,paint);
    window.addEventListener('dino-controller-request',event=>{if(active() && event.detail?.owner!=='provider-comparison')event.preventDefault();});
    window.addEventListener('beforeunload',event=>{if(active()){event.preventDefault();event.returnValue='';}});
    window.addEventListener('pagehide',()=>{closed=true;ui.key.value='';cancelPoll();readVersion++;});
    window.DinoProviderComparison=Object.freeze({get busy(){return active();},refresh});
    refresh();
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init,{once:true});else init();
})();
