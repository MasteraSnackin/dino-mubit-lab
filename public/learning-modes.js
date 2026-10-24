/* Local model weights and retrieved review context are separate mechanisms. */
(() => {
  'use strict';
  const assessments={improved_on_both:'Improved on both courses',unchanged:'Unchanged',worse_on_both:'Worse on both courses',mixed:'Mixed results',inconclusive:'Inconclusive'};
  const decisions={retain_baseline:'Retain the baseline',test_more:'Gather more evidence',consider_candidate:'Consider the tested change'};
  let ui,selection=null,selectionKey='',memory=null,memoryReading=false,memoryRequesting=false,memoryVersion=0;
  let memoryMessage='Choose an approved correction case above.',memoryError='',memoryReadFailed=false,refreshNeeded=false,connectionKey='';
  let local=null,localReading=false,localVersion=0,localError='';
  const uncertain=new Set();
  const n=(tag,text,cls)=>{const el=document.createElement(tag);if(text!=null)el.textContent=text;if(cls)el.className=cls;return el;};
  const button=(label,action)=>{const el=n('button',label);el.type='button';el.addEventListener('click',action);return el;};
  const key=item=>item?`${item.id}:${item.revision}:${item.approved}`:'';
  const current=()=>window.DinoLearning?.selectedCase || null;
  const memoryPath=(id,suffix='')=>`/api/memory-review/cases/${encodeURIComponent(id)}${suffix}`;
  const editorBlocked=()=>Boolean(window.DinoLearning?.dirty || window.DinoLearning?.busy || window.DinoReviewTask?.dirty || window.DinoReviewTask?.targetBusy);
  const otherWork=()=>!memoryRequesting && Boolean(window.DinoLocalPlayer?.active || window.DinoExperiments?.busy || window.DinoMubit?.busy || window.DinoReviewTask?.busy || (window.Runner?.instance_?.playing && !window.Runner.instance_.crashed));
  const count=value=>Number.isInteger(value)&&value>=0?String(value):'Not recorded';
  const latency=value=>Number.isFinite(value)&&value>=0?`${value} ms`:'Not recorded';
  async function request(url,payload) {
    const response=await fetch(url,payload===undefined?{cache:'no-store'}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
    const data=await response.json();
    if(!response.ok || !data.ok){const error=new Error(data.message || 'The saved result could not be read.');error.code=data.code;throw error;}
    return data;
  }
  function answer(parent,result) {
    parent.append(n('p',`${assessments[result.assessment] || result.assessment} · ${decisions[result.decision] || result.decision}`,'learning-mode-answer'));
    if(result.rationale)parent.append(n('p',result.rationale));
    if(result.caveat)parent.append(n('p',result.caveat,'learning-mode-note'));
  }
  function acceptMemory(data,chosen) {
    const value=data.memoryReview;
    if(!value || value.caseId!==chosen.id || value.caseRevision!==chosen.revision || value.taskVersion!=='memory-assisted-review-v1' || value.weightUpdate!==false || !Array.isArray(value.memories) || (value.requestAllowed===true && (typeof value.inputHash!=='string' || !/^[a-f0-9]{64}$/.test(value.inputHash) || value.memories.length===0)))throw new Error('The memory review does not match the selected saved revision. Refresh the correction records above.');
    memory=value;memoryReadFailed=false;
    if(value.attempt)uncertain.delete(key(chosen));
  }
  function paintMemory() {
    if(!ui)return;
    const attempt=memory?.attempt,unknown=uncertain.has(selectionKey),blocked=memoryReading || memoryRequesting || editorBlocked();
    ui.memoryAsk.disabled=blocked || otherWork() || !selection?.approved || !memory || memory.requestAllowed!==true || Boolean(attempt) || unknown;
    ui.memoryRefresh.disabled=blocked || !selection;
    ui.memoryExport.hidden=!attempt;ui.memoryExport.setAttribute('aria-disabled',String(blocked || !attempt));
    if(selection)ui.memoryExport.href=memoryPath(selection.id,'/export');
    ui.memoryIdentity.textContent=selection?`Selected case ${selection.id.slice(0,12)} · revision ${selection.revision}`:'Choose a correction case in the panel above.';
    ui.memoryPhase.textContent=memoryMessage;ui.memoryError.textContent=memoryError;
    ui.memoryBlocker.textContent=editorBlocked()?'Finish saving or discarding your review edits before using memory.':otherWork()?'Wait for the current game or model action to finish.':'';
    ui.memoryPreview.hidden=!memory;ui.memoryPreviewText.textContent=memory?JSON.stringify(memory.input,null,2):'';
    ui.memories.replaceChildren();
    if(memory) {
      ui.memories.append(n('p',`${memory.memories.length} approved reference${memory.memories.length===1?'':'s'} selected.`,'learning-mode-note'));
      for(const item of memory.memories) {
        const description=item.reviewType==='corrected_answer'?'Edited answer':'Confirmed reference';
        ui.memories.append(n('p',`${description} · ${item.targetId || 'Unidentified reference'} · revision ${item.targetRevision ?? 'unknown'}${item.sameCourseFamily?' · same course family':''}`,'learning-mode-note'));
      }
    }
    ui.memoryResult.replaceChildren();
    if(!attempt) {
      ui.memoryResult.append(n('p',unknown?'Request outcome unknown. Refresh the saved receipt; no repeat request is offered.':memory?'No memory-assisted request is recorded for this input and reference set.':'The saved memory request state has not been read.','learning-mode-note'));
      return;
    }
    const labels={pending:'Memory request pending · outcome unconfirmed',failed:'Memory request failed · attempt preserved',succeeded:'Memory-assisted reply saved'};
    ui.memoryResult.append(n('h4',labels[attempt.status] || 'Unknown saved request state'));
    if(attempt.message)ui.memoryResult.append(n('p',attempt.message,'learning-mode-note'));
    if(attempt.status==='succeeded' && attempt.result) {
      answer(ui.memoryResult,attempt.result);
      ui.memoryResult.append(n('p',`Outcome label matches the measurements: ${attempt.assessmentMatchesMeasurements===true?'Yes':attempt.assessmentMatchesMeasurements===false?'No':'Not verified'}.`,'learning-mode-note'));
      if(memory.baseline?.sameModel===true) {
        const comparison=n('table'),head=n('tr');head.append(n('th','Same input'),n('th','Outcome label'),n('th','Response time'));comparison.append(head);
        for(const [label,value] of [['Without memory',memory.baseline],['With memory',attempt]]) {
          const row=n('tr');row.append(n('td',label),n('td',assessments[value.result?.assessment] || 'Not recorded'),n('td',latency(value.latencyMs)));comparison.append(row);
        }
        ui.memoryResult.append(comparison,n('p','This compares one saved input on the same model. It does not establish a general quality or speed improvement.','learning-mode-note'));
      } else if(memory.baseline)ui.memoryResult.append(n('p','The saved baseline used a different model, so a direct memory comparison is not shown.','learning-mode-note'));
    }
    const detail=n('details');detail.append(n('summary','Memory request receipt'));
    detail.append(n('p',`Model: ${attempt.model || memory.model || 'Not recorded'}`,'learning-mode-note'),n('p',`Request ID: ${attempt.requestId || 'Not confirmed'}`,'learning-mode-note'),n('p',`Response time: ${latency(attempt.latencyMs)}`,'learning-mode-note'));
    if(attempt.usage?.totalTokens!=null)detail.append(n('p',`Total tokens: ${attempt.usage.totalTokens}`,'learning-mode-note'));
    ui.memoryResult.append(detail,n('p','The saved attempt is not repeated when you refresh or reopen this page.','learning-mode-note'));
  }
  async function refreshMemory() {
    if(!selection || memoryReading || memoryRequesting || editorBlocked())return;
    const chosen={...selection},chosenKey=selectionKey,token=++memoryVersion;
    memoryReading=true;memoryError='';memoryMessage='Reading eligible references and the saved request…';paintMemory();
    try {
      const data=await request(memoryPath(chosen.id));
      if(token!==memoryVersion || chosenKey!==selectionKey)return;
      acceptMemory(data,chosen);memoryMessage=uncertain.has(chosenKey)&&!memory.attempt?'The earlier request outcome remains unknown. No repeat is offered.':memory.message || 'Inspect the references before requesting a review.';
    } catch(error) {
      if(token===memoryVersion && chosenKey===selectionKey){memory=null;memoryReadFailed=true;memoryError=error.message;memoryMessage='The saved memory-review state is unknown. Refresh to read it again.';}
    } finally {
      if(token===memoryVersion){memoryReading=false;paintMemory();if(refreshNeeded){refreshNeeded=false;refreshMemory();}}
    }
  }
  function synchronise() {
    const chosen=current(),nextKey=key(chosen),changed=nextKey!==selectionKey;
    if(changed){selection=chosen?{...chosen}:null;selectionKey=nextKey;memoryVersion++;memoryReading=false;memory=null;memoryReadFailed=false;memoryError='';refreshNeeded=false;}
    if(!selection?.approved){memoryMessage=selection?'Approve the selected correction case before requesting a memory-assisted review.':'Choose an approved correction case above.';paintMemory();return;}
    paintMemory();
    if(!editorBlocked() && !memoryRequesting && !memoryReading && !memoryReadFailed && (changed || !memory))refreshMemory();
  }
  async function askMemory() {
    synchronise();
    if(memoryReading || memoryRequesting || editorBlocked() || otherWork() || !selection?.approved || !memory || memory.requestAllowed!==true || memory.attempt || uncertain.has(selectionKey))return;
    const ownership=new CustomEvent('dino-controller-request',{cancelable:true,detail:{owner:'learning-memory'}});
    if(!window.dispatchEvent(ownership)){memoryMessage='Another game or model action is in progress.';paintMemory();return;}
    const chosen={...selection},chosenKey=selectionKey;
    memoryRequesting=true;uncertain.add(chosenKey);memoryError='';memoryMessage='Sending the selected input and approved reference answers to Mubit…';
    window.dispatchEvent(new CustomEvent('dino-controller-change',{detail:{owner:'learning-memory'}}));paintMemory();
    try {
      const data=await request(memoryPath(chosen.id,'/review'),{expectedRevision:chosen.revision,expectedInputHash:memory.inputHash});
      if(selectionKey===chosenKey){acceptMemory(data,chosen);uncertain.delete(chosenKey);memoryMessage=memory.message || 'The memory-assisted request is saved.';}
    } catch(error) {
      if(selectionKey===chosenKey){
        memoryError=error.message;
        if(error.code==='context_changed' || error.code==='revision_conflict') {
          uncertain.delete(chosenKey);memory=null;memoryReadFailed=true;memoryMessage='Nothing was sent. Refresh and inspect the current saved input before requesting a review.';
        } else memoryMessage='The request outcome could not be confirmed. Refresh its saved receipt before another action.';
      }
    } finally {
      memoryRequesting=false;window.dispatchEvent(new CustomEvent('dino-controller-change',{detail:{owner:null}}));window.DinoMubit?.refreshStatus();
      if(selectionKey!==chosenKey)synchronise();else paintMemory();
    }
  }
  function connectionChanged() {
    const status=window.DinoMubit?.status,value=JSON.stringify([status?.model,status?.ready,status?.verified]);
    paintMemory();if(value===connectionKey)return;connectionKey=value;
    if(memoryReading){refreshNeeded=true;return;}
    if(selection?.approved && !memoryRequesting && !editorBlocked() && !memoryReadFailed)refreshMemory();
  }
  function replay(parent,label,value) {
    if(!value)return;
    const detail=n('details');detail.append(n('summary',label));
    if(value.jsonValid===true && value.answer)answer(detail,value.answer);
    else {detail.append(n('p','The saved output did not validate as a review answer.','learning-mode-note'));if(typeof value.text==='string')detail.append(n('pre',value.text.slice(0,3000)));}
    if(Number.isFinite(value.elapsedSeconds))detail.append(n('p',`${value.timingIncludesModelLoad===true?'Time including model load':'Response time'}: ${value.elapsedSeconds.toFixed(2)} seconds`,'learning-mode-note'));
    parent.append(detail);
  }
  function paintLocal() {
    if(!ui)return;
    ui.localRefresh.disabled=localReading;ui.localError.textContent=localError;
    ui.localResult.replaceChildren();ui.localExport.hidden=local?.exportAvailable!==true;
    ui.localExport.setAttribute('aria-disabled',String(localReading || local?.exportAvailable!==true));
    if(!local){ui.localPhase.textContent=localReading?'Reading the local training record…':'Local training state is unknown.';return;}
    const labels={not_started:'Local training has not started',prepared:'Local training data prepared',running:'Local training is running',succeeded:'Local training run saved',failed:'Local training run failed',unknown:'Local training state is unknown'};
    ui.localPhase.textContent=labels[local.status] || labels.unknown;
    if(local.message)ui.localResult.append(n('p',local.message,'learning-mode-note'));
    const model=typeof local.model==='string'?local.model:local.model?.id;
    if(model)ui.localResult.append(n('p',`Open model: ${model}`,'learning-mode-note'));
    ui.localResult.append(n('p',`Approved training examples: ${count(local.trainingExamples)} · edited answers: ${count(local.correctedExamples)}`,'learning-mode-note'));
    if(local.weightsChanged===true && typeof local.adapterId==='string' && local.adapterId)ui.localResult.append(n('p','Local model weights changed in this run.','learning-mode-answer'));
    else ui.localResult.append(n('p','A local model-weight change has not been verified.','learning-mode-note'));
    if(local.trainingExamples===1 && local.correctedExamples===0)ui.localResult.append(n('p','This is a one-example pilot using a confirmed reference. It does not demonstrate learning from a corrected model error.','learning-mode-note'));
    replay(ui.localResult,'Before local training · training-example replay',local.baseline);
    replay(ui.localResult,'After local training · training-example replay',local.adapted);
    if(local.baseline || local.adapted)ui.localResult.append(n('p','These outputs replay a training example. They do not measure performance on independent test cases.','learning-mode-note'));
    const evaluation=local.independentEvaluation;
    ui.localResult.append(n('p',evaluation?.message || 'Independent test performance has not been established.','learning-mode-note'));
    if(local.adapterId){const detail=n('details');detail.append(n('summary','Local model receipt'),n('p',`Adapter ID: ${local.adapterId}`,'learning-mode-note'));if(local.model?.revision)detail.append(n('p',`Base model revision: ${local.model.revision}`,'learning-mode-note'));ui.localResult.append(detail);}
  }
  async function refreshLocal() {
    if(localReading)return;
    const token=++localVersion;localReading=true;localError='';paintLocal();
    try {
      const data=await request('/api/local-adapter/status');
      if(token!==localVersion)return;
      if(!data.localAdapter || typeof data.localAdapter.status!=='string')throw new Error('The local training record is incomplete.');
      local=data.localAdapter;
    } catch(error){if(token===localVersion){local=null;localError=error.message;}}
    finally{if(token===localVersion){localReading=false;paintLocal();}}
  }
  function init() {
    const panel=n('section',null,'learning-modes-panel');panel.id='learning-modes-panel';panel.setAttribute('aria-label','Two learning modes');
    panel.append(n('h2','Two ways to use approved reviews'),n('p','Keep remembered examples and locally trained model weights separate, with a saved result for each.'));
    const grid=n('div',null,'learning-modes-grid'),memoryCard=n('article'),localCard=n('article');
    memoryCard.setAttribute('aria-label','Mubit with review memory');localCard.setAttribute('aria-label','Local model training');
    const memoryIdentity=n('p',null,'learning-mode-note'),memoryPhase=n('p',null,'learning-mode-phase'),memoryErrorNode=n('p',null,'learning-mode-error'),memoryBlocker=n('p',null,'learning-mode-note');
    memoryPhase.setAttribute('role','status');memoryErrorNode.setAttribute('role','alert');
    const memoryAsk=button('Ask Mubit using memory',askMemory),memoryRefresh=button('Refresh memory review',refreshMemory),memoryExport=n('a','Export memory receipt','learning-mode-button');memoryExport.setAttribute('download','');
    memoryExport.addEventListener('click',event=>{if(memoryExport.getAttribute('aria-disabled')==='true')event.preventDefault();});
    const controls=n('div',null,'learning-mode-controls');controls.append(memoryAsk,memoryRefresh,memoryExport);
    const memories=n('div'),memoryPreview=n('details'),memoryPreviewText=n('pre'),memoryResult=n('div');memoryPreview.append(n('summary','Preview input and remembered answers'),memoryPreviewText);
    memoryCard.append(n('h3','Mubit with review memory'),n('p','Send eligible approved answers as context for another saved case. The current input is excluded from its own references. This does not change model weights.','learning-mode-note'),memoryIdentity,memories,memoryPreview,controls,memoryPhase,memoryErrorNode,memoryBlocker,memoryResult,n('p','This explicit request sends the displayed reference answers through Mubit and may use credit. Reference retrieval is local; no Mubit memory service or training update is implied.','learning-mode-note'));
    const localPhase=n('p',null,'learning-mode-phase'),localErrorNode=n('p',null,'learning-mode-error'),localResult=n('div');localPhase.setAttribute('role','status');localErrorNode.setAttribute('role','alert');
    const localRefresh=button('Refresh local training result',refreshLocal),localExport=n('a','Export local training receipt','learning-mode-button');localExport.href='/api/local-adapter/export';localExport.setAttribute('download','');localExport.addEventListener('click',event=>{if(localExport.getAttribute('aria-disabled')==='true')event.preventDefault();});
    const localControls=n('div',null,'learning-mode-controls');localControls.append(localRefresh,localExport);
    localCard.append(n('h3','Local model training'),n('p','A training run on this Mac can update an open model’s weights. This panel reads saved results; it does not start training.','learning-mode-note'),localControls,localPhase,localErrorNode,localResult);
    grid.append(memoryCard,localCard);panel.append(grid);const anchor=document.getElementById('review-task-panel');if(anchor)anchor.after(panel);else document.body.append(panel);
    ui={memoryAsk,memoryRefresh,memoryExport,memoryIdentity,memoryPhase,memoryError:memoryErrorNode,memoryBlocker,memories,memoryPreview,memoryPreviewText,memoryResult,localRefresh,localExport,localPhase,localError:localErrorNode,localResult};
    for(const event of ['keydown','keyup','mousedown','mouseup','touchstart','touchend'])panel.addEventListener(event,item=>item.stopPropagation());
    window.addEventListener('dino-learning-case-changed',synchronise);window.addEventListener('dino-review-target-state',synchronise);window.addEventListener('dino-controller-change',paintMemory);window.addEventListener('dino-mubit-status',connectionChanged);
    window.addEventListener('dino-controller-request',event=>{if(memoryRequesting && event.detail?.owner!=='learning-memory')event.preventDefault();});
    window.addEventListener('beforeunload',event=>{if(memoryRequesting){event.preventDefault();event.returnValue='';}});
    window.DinoLearningModes=Object.freeze({get busy(){return memoryRequesting;},refreshMemory,refreshLocal});
    synchronise();refreshLocal();
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init,{once:true});else init();
})();
