/* One explicit text review of saved outcomes. Corrections are never sent here. */
(() => {
  'use strict';
  const assessmentNames={improved_on_both:'Improved on both courses',unchanged:'Unchanged',worse_on_both:'Worse on both courses',mixed:'Mixed results',inconclusive:'Inconclusive'};
  const decisionNames={retain_baseline:'Retain the baseline',test_more:'Gather more evidence',consider_candidate:'Consider the tested change'};
  let ui, selection=null, selectionKey='', task=null, reading=false, requesting=false, readVersion=0;
  let message='Choose an approved correction above to inspect its review task.', error='', readFailed=false, refreshNeeded=false, connectionKey='';
  const uncertain=new Set();
  let target=null, targetReading=false, targetSaving=false, targetDirty=false, targetVersion=0, targetBase='', targetError='', targetMessage='';
  let publishedTarget='';
  const n=(tag,text,cls)=>{const el=document.createElement(tag);if(text!=null)el.textContent=text;if(cls)el.className=cls;return el;};
  const button=(label,fn)=>{const el=n('button',label);el.type='button';el.addEventListener('click',fn);return el;};
  const key=item=>item ? `${item.id}:${item.revision}:${item.approved}` : '';
  const path=(id,suffix='')=>`/api/review-task/cases/${encodeURIComponent(id)}${suffix}`;
  const targetPath=(id,suffix='')=>`/api/review-target/cases/${encodeURIComponent(id)}${suffix}`;
  const editorBlocked=()=>Boolean(window.DinoLearning?.dirty || window.DinoLearning?.busy);
  const otherWork=()=>!requesting && Boolean(window.DinoLocalPlayer?.active || window.DinoExperiments?.busy || window.DinoMubit?.busy || (window.Runner?.instance_?.playing && !window.Runner.instance_.crashed));
  const current=()=>window.DinoLearning?.selectedCase || null;
  async function request(url,payload) {
    const response=await fetch(url,payload===undefined ? {cache:'no-store'} : {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
    const data=await response.json();
    if(!response.ok || !data.ok){const failure=new Error(data.message || 'The saved review could not be read.');failure.code=data.code;throw failure;}
    return data;
  }
  function accept(data,expected) {
    const value=data.task;
    if(!value || value.caseId!==expected.id || value.caseRevision!==expected.revision || value.taskVersion!=='tested-review-text-v1') {
      throw new Error('The saved correction changed. Use Refresh records above before continuing.');
    }
    task=value;readFailed=false;
    if(value.attempt)uncertain.delete(key(expected));
  }
  const targetSourceKey=()=>task?.attempt?.status==='succeeded' ? JSON.stringify([task.taskVersion,task.attempt.inputHash,task.attempt.requestId,task.attempt.model || task.model]) : '';
  const answerSignature=value=>JSON.stringify(['assessment','decision','rationale','caveat'].map(field=>value?.[field]));
  function targetAnswer(){return {assessment:ui.targetAssessment.value,decision:ui.targetDecision.value,rationale:ui.targetRationale.value,caveat:ui.targetCaveat.value};}
  const targetSnapshot=()=>JSON.stringify({answer:targetAnswer(),reason:ui.targetReason.value});
  function targetChanged(){targetDirty=Boolean(target) && targetSnapshot()!==targetBase;ui.targetChecked.checked=false;paint();}
  function targetNotice(){
    const state=JSON.stringify([targetDirty,targetSaving,targetReading]);if(state===publishedTarget)return;
    publishedTarget=state;window.dispatchEvent(new CustomEvent('dino-review-target-state',{detail:{dirty:targetDirty,busy:targetSaving,reading:targetReading}}));
  }
  function populateTarget(){
    const answer=target.latestDraft?.answer || target.source.originalAnswer;
    ui.targetAssessment.value=answer.assessment;ui.targetDecision.value=answer.decision;
    ui.targetRationale.value=answer.rationale;ui.targetCaveat.value=answer.caveat;
    ui.targetReason.value=target.latestDraft?.reason || '';ui.targetChecked.checked=false;
    targetDirty=false;targetBase=targetSnapshot();
  }
  function acceptTarget(data,chosen,sourceKey){
    const value=data.target, source=value?.source, attempt=task?.attempt;
    if(!value || value.caseId!==chosen.id || value.taskVersion!=='tested-review-text-v1' || !Number.isInteger(value.revision) || value.revision<0 || !source || source.taskVersion!==value.taskVersion || typeof source.inputHash!=='string' || !/^[a-f0-9]{64}$/.test(source.inputHash) || source.inputHash!==attempt?.inputHash || !source.requestId || source.requestId!==attempt?.requestId || !source.model || source.model!==(attempt?.model || task?.model) || answerSignature(source.originalAnswer)!==answerSignature(attempt?.result) || sourceKey!==targetSourceKey())throw new Error('The answer review does not match this saved Mubit request. Reload its receipt before continuing.');
    target=value;populateTarget();targetError='';
  }
  async function loadTarget(){
    if(!selection || task?.attempt?.status!=='succeeded' || targetDirty || targetSaving)return;
    const chosen={...selection}, chosenKey=selectionKey, sourceKey=targetSourceKey(), token=++targetVersion;
    targetReading=true;targetError='';paint();
    try{
      const data=await request(targetPath(chosen.id));
      if(token!==targetVersion || chosenKey!==selectionKey || sourceKey!==targetSourceKey())return;
      acceptTarget(data,chosen,sourceKey);targetMessage=target.message || 'Review this exact saved answer, then save a local draft.';
    }catch(failure){if(token===targetVersion && chosenKey===selectionKey){target=null;targetError=failure.message;targetMessage='The answer review state is unknown. Refresh the saved review to read it again.';}}
    finally{if(token===targetVersion){targetReading=false;paint();}}
  }
  function validTarget(){return Object.hasOwn(assessmentNames,ui.targetAssessment.value) && Object.hasOwn(decisionNames,ui.targetDecision.value) && ui.targetRationale.value.trim() && ui.targetRationale.value.length<=600 && ui.targetCaveat.value.trim() && ui.targetCaveat.value.length<=600 && ui.targetReason.value.trim() && ui.targetReason.value.length<=1000;}
  async function mutateTarget(action){
    if(!target || targetReading || targetSaving || reading || requesting || editorBlocked() || otherWork())return;
    if(action==='draft' && (!targetDirty || !validTarget()))return;
    if(action==='approve' && (targetDirty || !target.latestDraft || target.approval?.status==='approved' || !ui.targetChecked.checked))return;
    const chosen={...selection}, chosenKey=selectionKey, sourceKey=targetSourceKey(), token=++targetVersion;
    const payload=action==='draft' ? {expectedRevision:target.revision,answer:targetAnswer(),reason:ui.targetReason.value} : {expectedRevision:target.revision,confirm:true};
    targetSaving=true;targetError='';targetMessage='Saving the local answer review…';paint();
    try{
      const data=await request(targetPath(chosen.id,'/'+action),payload);
      if(token!==targetVersion || chosenKey!==selectionKey || sourceKey!==targetSourceKey())return;
      acceptTarget(data,chosen,sourceKey);
      targetMessage=action==='approve' ? 'This saved answer is approved for local export. Nothing was submitted to Mubit.' : 'Draft saved. Check this exact revision before approving it.';
    }catch(failure){if(token===targetVersion && chosenKey===selectionKey){targetError=failure.message;targetMessage='The save could not be confirmed. Your edits remain here; reload the saved answer review to reconcile its state.';ui.targetChecked.checked=false;}}
    finally{if(token===targetVersion){targetSaving=false;paint();if(key(current())!==selectionKey && !targetDirty)synchronise();}}
  }
  function discardTarget(){if(targetReading || targetSaving || reading || requesting)return;targetDirty=false;target=null;targetBase='';ui.targetChecked.checked=false;if(key(current())!==selectionKey)synchronise();else loadTarget();}
  function downloadTarget(){
    if(!target)return;
    const data={format:'dino-unsaved-text-review-v1',caseId:selection.id,taskVersion:task.taskVersion,inputHash:task.attempt.inputHash,requestId:task.attempt.requestId,expectedRevision:target.revision,answer:targetAnswer(),reason:ui.targetReason.value,approved:false};
    const url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json'})),link=n('a');link.href=url;link.download='dino-unsaved-text-review.json';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  }
  function paintTarget(){
    const succeeded=task?.attempt?.status==='succeeded';ui.targetEditor.hidden=!succeeded;
    const blocked=targetReading || targetSaving || reading || requesting || editorBlocked() || otherWork(), approved=target?.approval?.status==='approved', draft=target?.latestDraft;
    ui.targetForm.hidden=!target;ui.targetPhase.textContent=targetMessage;ui.targetError.textContent=targetError;
    for(const element of [ui.targetAssessment,ui.targetDecision,ui.targetRationale,ui.targetCaveat,ui.targetReason])element.disabled=blocked || !target;
    ui.targetSave.disabled=blocked || !target || !targetDirty || !validTarget();
    ui.targetChecked.disabled=blocked || targetDirty || !draft || approved;
    ui.targetApprove.disabled=blocked || targetDirty || !draft || approved || !ui.targetChecked.checked;
    ui.targetDiscard.disabled=targetReading || targetSaving || reading || requesting;
    ui.targetRecovery.hidden=!targetDirty;ui.targetDownload.disabled=targetSaving;
    ui.targetExport.hidden=!approved;ui.targetExport.setAttribute('aria-disabled',String(blocked || targetDirty || !approved));
    if(selection)ui.targetExport.href=targetPath(selection.id,'/export');
    if(!target){targetNotice();return;}
    const changed=answerSignature(targetAnswer())!==answerSignature(target.source.originalAnswer);
    ui.targetKind.textContent=changed ? 'Edited answer · review it against the saved evidence.' : approved && !targetDirty ? 'Confirmed correct reference · the model answer was not changed.' : 'Unchanged answer · it can be reviewed as a correct reference.';
    ui.targetStatus.textContent=targetDirty ? 'Unsaved answer review. Save or discard it before changing cases or starting another action.' : draft ? `${approved ? 'Approved locally' : 'Saved draft'} · revision ${target.revision}.` : 'No draft or approval exists for this answer. The older correction’s approval does not apply here.';
    ui.targetApproval.textContent=approved && !targetDirty ? `Approved locally on ${target.approval.approvedAt}. Reviewer identity was not verified. This has not updated a model.` : 'Approval applies only to the saved answer and reason for this exact request. It does not submit a training example.';
    targetNotice();
  }
  function paint() {
    if(!ui)return;
    const blocked=reading || requesting || editorBlocked() || targetDirty || targetSaving || targetReading, attempt=task?.attempt, unknown=uncertain.has(selectionKey);
    const ready=Boolean(selection?.approved && task && task.requestAllowed===true && !attempt && !unknown);
    ui.ask.disabled=blocked || otherWork() || !ready;
    ui.refresh.disabled=blocked || !selection;
    ui.preview.hidden=!task;ui.previewText.textContent=task ? JSON.stringify(task.input,null,2) : '';
    ui.identity.textContent=selection ? `Selected correction ${selection.id.slice(0,12)} · revision ${selection.revision}${selection.approved ? ' · approved locally' : ' · approval required'}` : 'Choose a saved correction in the panel above.';
    ui.phase.textContent=message;ui.error.textContent=error;
    ui.blocker.textContent=targetDirty ? 'Save or discard the answer review before changing the selected evidence.' : editorBlocked() ? 'Finish saving or discarding your correction edits before requesting a model review.' : otherWork() ? 'Wait for the current game or model action to finish.' : task && !task.connectionVerified && !attempt ? 'Check the connection in Mubit controls, then refresh this saved review.' : '';
    ui.export.hidden=!attempt;
    if(selection)ui.export.href=path(selection.id,'/export');
    ui.export.setAttribute('aria-disabled',String(blocked || !attempt));
    ui.result.replaceChildren();
    paintTarget();
    if(!attempt) {
      ui.result.append(n('p',unknown ? 'Request outcome unknown. Refresh the saved result; no repeat request is offered.' : task ? 'No request is recorded for this input.' : 'The saved request state has not been read.','review-task-note'));
      return;
    }
    const titles={pending:'Request pending · outcome not yet confirmed',succeeded:'Mubit reply saved',failed:'Request failed · attempt preserved'};
    ui.result.append(n('h3',titles[attempt.status] || 'Unknown request state'));
    if(attempt.message)ui.result.append(n('p',attempt.message,'review-task-note'));
    const result=attempt.result;
    if(attempt.status==='succeeded' && result) {
      ui.result.append(n('p',`${assessmentNames[result.assessment] || result.assessment} · ${decisionNames[result.decision] || result.decision}`,'review-task-answer'),n('p',result.rationale),n('p',result.caveat,'review-task-note'));
      ui.result.append(n('p',`Outcome label matches measured results: ${attempt.assessmentMatchesMeasurements===true ? 'Yes' : attempt.assessmentMatchesMeasurements===false ? 'No' : 'Not verified'}.`,'review-task-check'));
      ui.result.append(n('p','This checks one answer against the saved measurements. It does not establish general review quality or a model improvement.','review-task-note'));
    }
    const detail=n('details'),summary=n('summary','Request receipt');detail.append(summary);
    const metadata=[['Model',attempt.model || task.model || 'Not recorded'],['Request ID',attempt.requestId || 'Not confirmed'],['Response time',Number.isFinite(attempt.latencyMs) ? `${attempt.latencyMs} ms` : 'Not recorded']];
    if(attempt.usage?.totalTokens!=null)metadata.push(['Total tokens',String(attempt.usage.totalTokens)]);
    metadata.forEach(([label,value])=>detail.append(n('p',`${label}: ${value}`,'review-task-note')));
    ui.result.append(detail,n('p','This input has one saved attempt. Refreshing or reopening it does not send another request.','review-task-note'));
  }
  async function refresh() {
    if(!selection || reading || requesting || editorBlocked() || targetDirty || targetSaving || targetReading)return;
    const chosen={...selection}, chosenKey=selectionKey, token=++readVersion;
    reading=true;error='';message='Reading the compact input and saved request…';paint();
    try {
      const data=await request(path(chosen.id));if(token!==readVersion || selectionKey!==chosenKey)return;
      accept(data,chosen);
      message=uncertain.has(chosenKey) && !task.attempt ? 'The earlier request outcome is still unconfirmed. No repeat request is offered.' : task.message || 'The compact review task is ready to inspect.';
      if(task.attempt?.status==='succeeded')await loadTarget();
    } catch(failure) {
      if(token===readVersion && selectionKey===chosenKey){task=null;readFailed=true;error=failure.message;message='The saved review state is unknown. Refresh to read it again.';}
    } finally {
      if(token===readVersion){reading=false;paint();if(refreshNeeded){refreshNeeded=false;refresh();}}
    }
  }
  function synchronise() {
    const chosen=current(), nextKey=key(chosen), changed=nextKey!==selectionKey;
    if(changed && (targetDirty || targetSaving)){message='Finish the answer review for the previous case before changing the selected evidence.';paint();return;}
    if(changed){selection=chosen ? {...chosen} : null;selectionKey=nextKey;readVersion++;reading=false;task=null;readFailed=false;error='';refreshNeeded=false;targetVersion++;target=null;targetReading=false;targetMessage='';targetError='';targetBase='';}
    if(!selection?.approved) {
      message=selection ? 'Approve the saved correction above before requesting this review.' : 'Choose an approved correction above to inspect its review task.';paint();return;
    }
    paint();
    if(!editorBlocked() && !requesting && !reading && !readFailed && (changed || !task))refresh();
  }
  async function ask() {
    synchronise();
    if(reading || requesting || editorBlocked() || targetDirty || targetSaving || targetReading || otherWork() || !selection?.approved || !task || task.requestAllowed!==true || task.attempt || uncertain.has(selectionKey))return;
    const ownership=new CustomEvent('dino-controller-request',{cancelable:true,detail:{owner:'review-task'}});
    if(!window.dispatchEvent(ownership)){message='Another game or model action is in progress.';paint();return;}
    const chosen={...selection}, chosenKey=selectionKey;
    requesting=true;uncertain.add(chosenKey);error='';message='Sending the original suggestion and measured results to Mubit…';
    window.dispatchEvent(new CustomEvent('dino-controller-change',{detail:{owner:'review-task'}}));paint();
    try {
      const data=await request(path(chosen.id,'/review'),{expectedRevision:chosen.revision});
      if(selectionKey===chosenKey){accept(data,chosen);uncertain.delete(chosenKey);message=task.attempt?.status==='succeeded' ? 'Mubit’s text review is saved. Your approved correction was not sent or replaced.' : task.message || 'The request outcome is recorded below.';if(task.attempt?.status==='succeeded')await loadTarget();}
    } catch(failure) {
      if(selectionKey===chosenKey){error=failure.message;message='The request outcome could not be confirmed. Refresh the saved result before taking another action.';}
    } finally {
      requesting=false;window.dispatchEvent(new CustomEvent('dino-controller-change',{detail:{owner:null}}));
      window.DinoMubit?.refreshStatus();
      if(selectionKey!==chosenKey)synchronise();else paint();
    }
  }
  function connectionChanged() {
    const status=window.DinoMubit?.status, value=JSON.stringify([status?.model,status?.ready,status?.verified]);
    paint();if(value===connectionKey)return;connectionKey=value;
    if(reading){refreshNeeded=true;return;}
    if(selection?.approved && !requesting && !editorBlocked() && !targetDirty && !targetSaving)refresh();
  }
  function init() {
    const panel=n('section',null,'review-task-panel');panel.setAttribute('aria-label','Recurring text review');panel.id='review-task-panel';
    panel.append(n('h2','Check the recurring review task'),n('p','Ask Mubit to assess the original suggestion using the measured results from the selected correction case. This request contains text only. Your approved correction stays local.'));
    const identity=n('p',null,'review-task-note'),phase=n('p',null,'review-task-phase'),errorNode=n('p',null,'review-task-warning'),blocker=n('p',null,'review-task-note');phase.setAttribute('role','status');errorNode.setAttribute('role','alert');
    const preview=n('details',null,'review-task-preview'),previewText=n('pre');preview.append(n('summary','Preview the text sent to Mubit'),previewText);
    const controls=n('div',null,'review-task-controls'),askButton=button('Ask Mubit for one text review',ask),refreshButton=button('Refresh saved review',refresh),exportLink=n('a','Export review receipt','review-task-button');exportLink.setAttribute('download','');
    exportLink.addEventListener('click',event=>{if(exportLink.getAttribute('aria-disabled')==='true')event.preventDefault();});controls.append(askButton,refreshButton,exportLink);
    const result=n('div',null,'review-task-result');
    const targetEditor=n('details',null,'review-target-editor');targetEditor.append(n('summary','Review this saved answer'));
    const targetPhase=n('p',null,'review-task-note'),targetErrorNode=n('p',null,'review-task-warning'),targetForm=n('div',null,'review-target-form');targetPhase.setAttribute('role','status');targetErrorNode.setAttribute('role','alert');
    const field=(label,element)=>{const wrapper=n('label',null,'review-target-field');wrapper.append(n('span',label),element);element.setAttribute('aria-label',label);targetForm.append(wrapper);return element;};
    const targetAssessment=field('Reviewed outcome',n('select')),targetDecision=field('Reviewed decision',n('select'));
    for(const [value,label] of Object.entries(assessmentNames)){const option=n('option',label);option.value=value;targetAssessment.append(option);}
    for(const [value,label] of Object.entries(decisionNames)){const option=n('option',label);option.value=value;targetDecision.append(option);}
    const targetRationale=field('Reviewed explanation',n('textarea')),targetCaveat=field('Reviewed limits',n('textarea')),targetReason=field('Review reason and reviewer',n('textarea'));
    targetRationale.maxLength=600;targetCaveat.maxLength=600;targetReason.maxLength=1000;targetReason.placeholder='Explain what you checked and who performed the review.';
    for(const element of [targetAssessment,targetDecision,targetRationale,targetCaveat,targetReason])for(const event of ['input','change'])element.addEventListener(event,targetChanged);
    const targetKind=n('p',null,'review-task-check'),targetStatus=n('p',null,'review-task-note'),targetApproval=n('p',null,'review-task-note');
    const targetSave=button('Save answer review draft',()=>mutateTarget('draft')),targetApprove=button('Approve saved answer review',()=>mutateTarget('approve'));
    const targetChecked=n('input');targetChecked.type='checkbox';targetChecked.setAttribute('aria-label','I have checked this saved answer and review reason.');targetChecked.addEventListener('change',paint);
    const targetCheckLabel=n('label',null,'review-target-check');targetCheckLabel.append(targetChecked,n('span','I have checked this saved answer and review reason.'));
    const targetExport=n('a','Export approved answer review','review-task-button');targetExport.setAttribute('download','');targetExport.addEventListener('click',event=>{if(targetExport.getAttribute('aria-disabled')==='true')event.preventDefault();});
    const targetControls=n('div',null,'review-task-controls');targetControls.append(targetSave,targetExport);
    const targetRecovery=n('div',null,'review-target-recovery'),targetDiscard=button('Discard answer edits and reload',discardTarget),targetDownload=button('Download unsaved answer review',downloadTarget),targetRecoveryControls=n('div',null,'review-task-controls');targetRecoveryControls.append(targetDownload,targetDiscard);targetRecovery.append(n('p','Your unsaved answer review stays in this tab. Save or download it before changing cases.'),targetRecoveryControls);
    targetForm.append(targetKind,targetStatus,targetControls,targetCheckLabel,targetApprove,targetApproval,targetRecovery);
    targetEditor.append(n('p','Check the answer against this request’s saved measurements. Keep a correct answer as a reference, or edit it and explain why. This approval is separate from the older correction above.','review-task-note'),targetPhase,targetErrorNode,targetForm,n('p','This local export matches the text-review answer format. Its Mubit submission format and training acceptance remain unverified.','review-task-note'));
    panel.append(identity,preview,controls,phase,errorNode,blocker,result,targetEditor,n('p','Mubit may record the request and model reply and use them in its training workflow. Calls may use credit and share the existing request limit. The number of examples Mubit has accepted and any updated model remain unverified.','review-task-note'),n('p','This request does not submit your approved answer as a training target or start a verified model update.','review-task-boundary'));
    const anchor=document.getElementById('learning-panel');if(anchor)anchor.after(panel);else document.body.append(panel);
    ui={panel,identity,phase,error:errorNode,blocker,preview,previewText,ask:askButton,refresh:refreshButton,export:exportLink,result,targetEditor,targetPhase,targetError:targetErrorNode,targetForm,targetAssessment,targetDecision,targetRationale,targetCaveat,targetReason,targetKind,targetStatus,targetApproval,targetSave,targetApprove,targetChecked,targetExport,targetRecovery,targetDiscard,targetDownload};
    for(const name of ['keydown','keyup','mousedown','mouseup','touchstart','touchend'])panel.addEventListener(name,event=>event.stopPropagation());
    window.addEventListener('dino-learning-case-changed',synchronise);
    window.addEventListener('dino-controller-change',paint);
    window.addEventListener('dino-controller-request',event=>{if((requesting || targetDirty || targetSaving) && event.detail?.owner!=='review-task')event.preventDefault();});
    window.addEventListener('dino-mubit-status',connectionChanged);
    window.addEventListener('beforeunload',event=>{if(requesting || targetDirty || targetSaving){event.preventDefault();event.returnValue='';}});
    window.DinoReviewTask=Object.freeze({get busy(){return requesting;},get dirty(){return targetDirty;},get targetBusy(){return targetSaving;},refresh});
    synchronise();
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init,{once:true});else init();
})();
