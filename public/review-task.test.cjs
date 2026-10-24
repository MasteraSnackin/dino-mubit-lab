/* Offline state tests. No real game, local case, approval or provider is changed. */
'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'review-task.js'),'utf8');
const clone=value=>JSON.parse(JSON.stringify(value));
const deferred=()=>{let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};};
const selected=(id='case_one',revision=3,approved=true)=>({id,revision,approved});
function savedTask(item=selected(),extra={}) {
  return {taskVersion:'tested-review-text-v1',caseId:item.id,caseRevision:item.revision,model:'fixture-model',
    input:{taskVersion:'tested-review-text-v1',originalProposal:{parameter:'duckExtraMs',value:80,rationale:'Try longer ducking.',caveat:'Only two courses.'},
      courses:[{course:'a',baseline:{score:539,ticks:2897,outcome:'crashed'},candidate:{score:539,ticks:2897,outcome:'crashed'}}]},
    expectedAssessment:'unchanged',attempt:null,connectionVerified:true,requestAllowed:item.approved,
    approvedCorrectionSent:false,modelUpdateVerified:false,message:'Inspect this text before sending.',...extra};
}
function receipt(status='succeeded') {
  return {id:'attempt_fixture',status,model:'fixture-model',inputHash:'a'.repeat(64),requestId:'mreq_fixture',latencyMs:240,
    usage:{totalTokens:180},assessmentMatchesMeasurements:status==='succeeded'?true:null,
    result:status==='succeeded'?{assessment:'unchanged',decision:'retain_baseline',rationale:'The scores and survival times are unchanged.',caveat:'One case does not establish general quality.'}:null,
    message:status==='failed'?'The request failed; its attempt remains saved.':''};
}
function targetRecord(item=selected(),extra={}) {
  const actual=receipt();
  return {id:'target_fixture',caseId:item.id,taskVersion:'tested-review-text-v1',revision:0,
    source:{taskVersion:'tested-review-text-v1',inputHash:actual.inputHash,requestId:actual.requestId,model:actual.model,originalAnswer:actual.result},
    latestDraft:null,approval:{status:'unapproved',identityVerified:false},history:[],providerCompatible:false,submitted:false,modelUpdateVerified:false,...extra};
}
function harness(options={}) {
  const panels=[],events={},requests=[];
  const targets=new Map();
  let choice=options.selection===undefined?selected():options.selection,dirty=false,editorBusy=false;
  let resolver=options.resolve || ((url,init)=>savedTask(choice,init.method==='POST'?{attempt:receipt(),requestAllowed:false}:options.task || {}));
  let targetResolver=options.targetResolve || ((url,init)=>{
    const id=decodeURIComponent(url.match(/\/cases\/([^/]+)/)[1]);
    let value=targets.get(id) || targetRecord(selected(id));
    if(init.method==='POST') {
      const body=JSON.parse(init.body);
      if(body.expectedRevision!==value.revision)return {ok:false,code:'revision_conflict',message:'Fixture revision changed.'};
      const revision=value.revision+1;
      if(url.endsWith('/draft'))value={...value,revision,latestDraft:{revision,answer:body.answer,reason:body.reason,changedFromModel:JSON.stringify(body.answer)!==JSON.stringify(value.source.originalAnswer),createdAt:'2026-10-24T12:00:00Z'},approval:{status:'unapproved',identityVerified:false}};
      else value={...value,revision,approval:{status:'approved',draftRevision:value.latestDraft.revision,approvedAt:'2026-10-24T12:01:00Z',identityVerified:false}};
      targets.set(id,value);
    }
    return value;
  });
  class Event {
    constructor(type,init={}){this.type=type;Object.assign(this,init);this.defaultPrevented=false;}
    preventDefault(){if(this.cancelable)this.defaultPrevented=true;}
    stopPropagation(){}
  }
  class Element {
    constructor(tag){this.tagName=tag.toUpperCase();this.children=[];this.listeners={};this.attributes={};this.disabled=false;this.hidden=false;this.textContent='';this.value='';this.checked=false;}
    append(...items){items.forEach(item=>{item.parent=this;this.children.push(item);});}
    replaceChildren(...items){this.children=[];this.append(...items);}
    setAttribute(name,value){this.attributes[name]=String(value);}
    getAttribute(name){return this.attributes[name]??null;}
    addEventListener(type,fn){(this.listeners[type]??=[]).push(fn);}
    click(){if(!this.disabled)(this.listeners.click || []).forEach(fn=>fn(new Event('click',{cancelable:true})));}
    dispatch(type){(this.listeners[type] || []).forEach(fn=>fn(new Event(type,{cancelable:true})));}
    after(element){panels.push(element);}
  }
  const anchor=new Element('section'),document={readyState:'complete',createElement:tag=>new Element(tag),getElementById:()=>anchor,body:new Element('body')};
  const window={document,DinoLearning:{get selectedCase(){return choice&&clone(choice);},get dirty(){return dirty;},get busy(){return editorBusy;}},
    DinoMubit:{status:{model:'fixture-model',ready:true,verified:true},busy:false,refreshStatus(){}},
    DinoExperiments:{busy:false},DinoLocalPlayer:{active:false},Runner:{instance_:{playing:false,crashed:false}},
    addEventListener(type,fn){(events[type]??=[]).push(fn);},
    dispatchEvent(event){(events[event.type] || []).forEach(fn=>fn(event));return !event.defaultPrevented;}};
  const fetch=async(url,init={})=>{
    requests.push({url,method:init.method || 'GET',payload:init.body?JSON.parse(init.body):undefined});
    const targetRoute=url.startsWith('/api/review-target/'),result=await (targetRoute?targetResolver:resolver)(url,init);
    return {ok:result.ok!==false,json:async()=>result.ok===false?result:{ok:true,[targetRoute?'target':'task']:clone(result)}};
  };
  vm.runInNewContext(source,{window,document,fetch,CustomEvent:Event,console});
  function all(){const found=[];function walk(el){found.push(el);el.children.forEach(walk);}panels.forEach(walk);return found;}
  const settle=async()=>{await new Promise(resolve=>setImmediate(resolve));await new Promise(resolve=>setImmediate(resolve));};
  const emit=(type,detail)=>window.dispatchEvent(new Event(type,{cancelable:true,detail}));
  return {window,requests,settle,emit,all,byText:text=>all().find(el=>el.textContent===text),
    byLabel:label=>all().find(el=>el.getAttribute('aria-label')===label),
    text:()=>all().map(el=>el.textContent).join('\n'),posts:()=>requests.filter(item=>item.method==='POST'),
    select(item){choice=item;emit('dino-learning-case-changed');},
    edit(value,busy=false){dirty=value;editorBusy=busy;emit('dino-learning-case-changed');},
    resolve(fn){resolver=fn;},targetResolve(fn){targetResolver=fn;}};
}

test('preview is read only; an explicit click sends only the selected revision and preserves the correction',async()=>{
  const app=harness();await app.settle();
  assert.equal(app.posts().length,0);
  assert.equal(app.requests.length,1);
  assert.equal(app.requests[0].url,'/api/review-task/cases/case_one');
  assert.match(app.all().find(el=>el.tagName==='PRE').textContent,/originalProposal/);
  assert.doesNotMatch(app.all().find(el=>el.tagName==='PRE').textContent,/expectedAssessment|approvedCorrection/);
  app.byText('Ask Mubit for one text review').click();await app.settle();
  assert.deepEqual(app.posts(),[{url:'/api/review-task/cases/case_one/review',method:'POST',payload:{expectedRevision:3}}]);
  assert(app.byText('Outcome label matches measured results: Yes.'));
  assert.match(app.text(),/does not establish general review quality or a model improvement/);
  assert.deepEqual(clone(app.window.DinoLearning.selectedCase),selected());
  assert.equal(app.byText('Ask Mubit for one text review').disabled,true);
  assert.equal(app.byText('Export review receipt').href,'/api/review-task/cases/case_one/export');
});

test('dirty or busy correction state blocks new reads and provider calls without replacing edits',async()=>{
  const app=harness();await app.settle();app.edit(true);
  app.byText('Ask Mubit for one text review').click();app.byText('Refresh saved review').click();await app.settle();
  assert.equal(app.requests.length,1);assert.equal(app.posts().length,0);
  assert.equal(app.window.DinoLearning.dirty,true);
  app.edit(false,true);app.byText('Ask Mubit for one text review').click();await app.settle();
  assert.equal(app.posts().length,0);
  app.edit(false);assert.equal(app.byText('Ask Mubit for one text review').disabled,false);
  app.select(selected('case_one',4,false));await app.settle();
  assert.equal(app.byText('Ask Mubit for one text review').disabled,true);
  assert.equal(app.requests.length,1);
});

test('active game, local player, Mubit and experiment work each block the review',async()=>{
  const app=harness();await app.settle();
  for(const [object,field] of [[app.window.Runner.instance_,'playing'],[app.window.DinoLocalPlayer,'active'],[app.window.DinoMubit,'busy'],[app.window.DinoExperiments,'busy']]) {
    object[field]=true;app.emit('dino-controller-change');
    assert.equal(app.byText('Ask Mubit for one text review').disabled,true);
    app.byText('Ask Mubit for one text review').click();await app.settle();
    object[field]=false;
  }
  assert.equal(app.posts().length,0);
  app.emit('dino-controller-change');assert.equal(app.byText('Ask Mubit for one text review').disabled,false);
});

for(const status of ['pending','failed','succeeded'])test(`${status} attempts stay visible and refresh never retries them`,async()=>{
  const app=harness({task:{attempt:receipt(status),requestAllowed:false}});await app.settle();
  assert.equal(app.byText('Ask Mubit for one text review').disabled,true);
  assert.equal(app.byText('Export review receipt').hidden,false);
  app.byText('Refresh saved review').click();await app.settle();
  app.byText('Ask Mubit for one text review').click();await app.settle();
  assert.equal(app.posts().length,0);assert.equal(app.requests.filter(item=>item.url.startsWith('/api/review-task/')).length,2);
  assert.match(app.text(),/This input has one saved attempt/);
});

test('an unknown POST outcome cannot be repeated even when a subsequent read has no attempt',async()=>{
  const app=harness();await app.settle();
  app.resolve((url,init)=>{if(init.method==='POST')throw new Error('Fixture disconnected.');return savedTask();});
  app.byText('Ask Mubit for one text review').click();await app.settle();
  assert.match(app.text(),/Request outcome unknown/);assert.equal(app.posts().length,1);
  app.byText('Refresh saved review').click();await app.settle();
  app.byText('Ask Mubit for one text review').click();await app.settle();
  assert.equal(app.posts().length,1);assert.equal(app.byText('Ask Mubit for one text review').disabled,true);
  app.resolve(()=>savedTask(selected(),{attempt:receipt('pending'),requestAllowed:false}));
  app.byText('Refresh saved review').click();await app.settle();
  assert(app.byText('Request pending · outcome not yet confirmed'));
  assert.equal(app.posts().length,1);
});

test('unreadable preview remains unknown and does not auto-repeat after an unchanged case event',async()=>{
  const app=harness({resolve:()=>{throw new Error('Fixture read unavailable.');}});await app.settle();
  assert.match(app.text(),/saved review state is unknown/);
  app.emit('dino-learning-case-changed');await app.settle();
  assert.equal(app.requests.length,1);assert.equal(app.posts().length,0);
  assert.equal(app.byText('Ask Mubit for one text review').disabled,true);
});

test('a late preview for the old selection cannot enable a request for the wrong case',async()=>{
  const old=deferred(),app=harness({resolve:url=>url.endsWith('case_one')?old.promise:savedTask(selected('case_two',7))});
  app.select(selected('case_two',7));await app.settle();
  old.resolve(savedTask());await app.settle();
  assert.match(app.text(),/case_two · revision 7/);assert.doesNotMatch(app.text(),/case_one · revision/);
  app.resolve((url,init)=>savedTask(selected('case_two',7),init.method==='POST'?{attempt:receipt(),requestAllowed:false}:{}));
  app.byText('Ask Mubit for one text review').click();await app.settle();
  assert.deepEqual(app.posts(),[{url:'/api/review-task/cases/case_two/review',method:'POST',payload:{expectedRevision:7}}]);
});

test('pending POST owns the controller and its late result never overwrites a newly selected case',async()=>{
  const pending=deferred(),app=harness();await app.settle();
  app.resolve((url,init)=>init.method==='POST'?pending.promise:savedTask(selected('case_two',8)));
  app.byText('Ask Mubit for one text review').click();await app.settle();
  assert.equal(app.window.DinoReviewTask.busy,true);
  assert.equal(app.emit('dino-controller-request',{owner:'local'}),false);
  app.select(selected('case_two',8));await app.settle();
  assert.equal(app.byText('Ask Mubit for one text review').disabled,true);
  pending.resolve(savedTask(selected(),{attempt:receipt(),requestAllowed:false}));await app.settle();
  assert.equal(app.window.DinoReviewTask.busy,false);
  assert.equal(app.emit('dino-controller-request',{owner:'local'}),true);
  assert.match(app.text(),/case_two · revision 8/);assert.doesNotMatch(app.text(),/Mubit reply saved/);
  assert.equal(app.posts().length,1);assert.equal(app.byText('Ask Mubit for one text review').disabled,false);
});

test('a revision mismatch disables sending until records are refreshed',async()=>{
  const app=harness({resolve:()=>savedTask(selected('case_one',4))});await app.settle();
  assert.match(app.text(),/saved correction changed/);
  assert.equal(app.byText('Ask Mubit for one text review').disabled,true);
  assert.equal(app.posts().length,0);
});

test('connection verification updates the read-only preview without requesting a review',async()=>{
  const app=harness({task:{connectionVerified:false,requestAllowed:false}});await app.settle();
  assert.equal(app.byText('Ask Mubit for one text review').disabled,true);
  app.resolve(()=>savedTask());app.emit('dino-mubit-status');await app.settle();
  assert.equal(app.byText('Ask Mubit for one text review').disabled,false);
  assert.equal(app.posts().length,0);
});

test('a matching saved answer starts unreviewed; confirmation requires a saved draft and separate checked approval',async()=>{
  const app=harness({task:{attempt:receipt(),requestAllowed:false}});await app.settle();
  assert.equal(app.posts().length,0);
  assert.equal(app.byLabel('Reviewed outcome').value,'unchanged');
  assert.equal(app.byLabel('Reviewed explanation').value,receipt().result.rationale);
  assert.equal(app.byText('Approve saved answer review').disabled,true);
  assert.match(app.text(),/No draft or approval exists for this answer/);
  const reason=app.byLabel('Review reason and reviewer');reason.value='Checked by Codex (AI assistant) against the saved measurements under delegated authorisation.';reason.dispatch('input');
  assert.equal(app.window.DinoReviewTask.dirty,true);
  assert.equal(app.byText('Save answer review draft').disabled,false);
  app.byText('Save answer review draft').click();await app.settle();
  assert.equal(app.posts().length,1);assert.equal(app.posts()[0].payload.expectedRevision,0);
  assert.deepEqual(app.posts()[0].payload.answer,receipt().result);
  assert.equal(app.window.DinoReviewTask.dirty,false);
  assert.equal(app.byText('Approve saved answer review').disabled,true);
  const checked=app.byLabel('I have checked this saved answer and review reason.');checked.checked=true;checked.dispatch('change');
  app.byText('Approve saved answer review').click();await app.settle();
  assert.deepEqual(app.posts()[1],{url:'/api/review-target/cases/case_one/approve',method:'POST',payload:{expectedRevision:1,confirm:true}});
  assert(app.byText('Confirmed correct reference · the model answer was not changed.'));
  assert.match(app.text(),/Reviewer identity was not verified/);
  assert.equal(app.byText('Export approved answer review').hidden,false);
  assert.equal(app.byText('Export approved answer review').href,'/api/review-target/cases/case_one/export');
  assert.equal(app.posts().some(item=>item.url.startsWith('/api/review-task/')),false);
});

test('answer edits preserve their case and block refresh, connection reads, gameplay and approval until saved or discarded',async()=>{
  const app=harness({task:{attempt:receipt(),requestAllowed:false}});await app.settle();
  const field=app.byLabel('Reviewed explanation');field.value='Unsaved explanation for the original case.';field.dispatch('input');
  const requestsBefore=app.requests.length;
  app.byText('Refresh saved review').click();app.emit('dino-mubit-status');await app.settle();
  assert.equal(app.requests.length,requestsBefore);
  assert.equal(app.emit('dino-controller-request',{owner:'local'}),false);
  const unload={type:'beforeunload',preventDefault(){this.prevented=true;}};app.window.dispatchEvent(unload);assert.equal(unload.prevented,true);
  app.select(selected('case_two',7));await app.settle();
  assert.equal(field.value,'Unsaved explanation for the original case.');assert.match(app.text(),/Selected correction case_one/);
  assert.equal(app.byText('Approve saved answer review').disabled,true);
  app.resolve(()=>savedTask(selected('case_two',7),{attempt:receipt(),requestAllowed:false}));
  app.byText('Discard answer edits and reload').click();await app.settle();
  assert.equal(app.window.DinoReviewTask.dirty,false);assert.match(app.text(),/Selected correction case_two/);
  assert.equal(app.byLabel('Reviewed explanation').value,receipt().result.rationale);
  assert.equal(app.posts().length,0);
});

test('saved target source must match the exact input, request, model and original reply',async()=>{
  for(const [name,value] of [['inputHash','b'.repeat(64)],['requestId','mreq_other'],['model','other-model'],['originalAnswer',{...receipt().result,assessment:'mixed'}]]) {
    const saved=targetRecord();saved.source[name]=value;
    const app=harness({task:{attempt:receipt(),requestAllowed:false},targetResolve:()=>saved});await app.settle();
    assert.match(app.text(),/does not match this saved Mubit request/);
    assert.equal(app.byText('Save answer review draft').disabled,true);
    assert.equal(app.byText('Approve saved answer review').disabled,true);
    assert.equal(app.posts().length,0);
  }
});

test('original answer key order is immaterial and a target from another case is rejected',async()=>{
  const saved=targetRecord(),answer=saved.source.originalAnswer;
  saved.source.originalAnswer={caveat:answer.caveat,rationale:answer.rationale,decision:answer.decision,assessment:answer.assessment};
  const app=harness({task:{attempt:receipt(),requestAllowed:false},targetResolve:()=>saved});await app.settle();
  assert(app.byText('Unchanged answer · it can be reviewed as a correct reference.'));
  saved.caseId='case_other';app.byText('Refresh saved review').click();await app.settle();
  assert.match(app.text(),/does not match this saved Mubit request/);
  assert.equal(app.byText('Save answer review draft').disabled,true);
});

test('an edited answer revokes export approval only after a new draft and uses the current target revision',async()=>{
  const original=receipt().result,saved=targetRecord(selected(),{revision:2,latestDraft:{revision:1,answer:original,reason:'Previously reviewed.',changedFromModel:false},approval:{status:'approved',draftRevision:1,approvedAt:'2026-10-24T12:00:00Z'}});
  const app=harness({task:{attempt:receipt(),requestAllowed:false},targetResolve:(url,init)=>init.method==='POST'?{...saved,revision:3,latestDraft:{revision:3,...JSON.parse(init.body)},approval:{status:'unapproved',identityVerified:false}}:saved});await app.settle();
  const field=app.byLabel('Reviewed limits');field.value='Only these two measured courses were checked; no cause of collision was established.';field.dispatch('input');
  assert.equal(app.byText('Export approved answer review').getAttribute('aria-disabled'),'true');
  assert.equal(app.byText('Approve saved answer review').disabled,true);
  app.byText('Save answer review draft').click();await app.settle();
  assert.equal(app.posts()[0].payload.expectedRevision,2);
  assert.equal(app.byText('Export approved answer review').hidden,true);
  assert(app.byText('Edited answer · review it against the saved evidence.'));
});

test('a failed target save preserves form edits and makes no automatic retry or approval',async()=>{
  const app=harness({task:{attempt:receipt(),requestAllowed:false},targetResolve:(url,init)=>{if(init.method==='POST')throw new Error('Fixture save unknown.');return targetRecord();}});await app.settle();
  const field=app.byLabel('Review reason and reviewer');field.value='Keep this review reason after the failed save.';field.dispatch('input');
  app.byText('Save answer review draft').click();await app.settle();app.emit('dino-mubit-status');await app.settle();
  assert.equal(field.value,'Keep this review reason after the failed save.');assert.equal(app.window.DinoReviewTask.dirty,true);
  assert.equal(app.posts().length,1);assert.equal(app.byText('Approve saved answer review').disabled,true);
  assert.match(app.text(),/save could not be confirmed/);
  assert.equal(app.byText('Download unsaved answer review').disabled,false);
});

test('late target reads cannot replace another selected case and local writes block controller ownership',async()=>{
  const late=deferred(),app=harness({task:{attempt:receipt(),requestAllowed:false},targetResolve:url=>url.endsWith('case_one')?late.promise:targetRecord(selected('case_two',7))});await app.settle();
  app.resolve(()=>savedTask(selected('case_two',7),{attempt:receipt(),requestAllowed:false}));app.select(selected('case_two',7));await app.settle();
  late.resolve(targetRecord());await app.settle();assert.match(app.text(),/Selected correction case_two/);
  const pending=deferred();app.targetResolve(()=>pending.promise);
  const reason=app.byLabel('Review reason and reviewer');reason.value='Review the selected case only.';reason.dispatch('input');app.byText('Save answer review draft').click();await app.settle();
  assert.equal(app.window.DinoReviewTask.targetBusy,true);assert.equal(app.emit('dino-controller-request',{owner:'experiment'}),false);
  pending.resolve(targetRecord(selected('case_two',7),{revision:1,latestDraft:{revision:1,answer:receipt().result,reason:reason.value,changedFromModel:false}}));await app.settle();
  assert.equal(app.window.DinoReviewTask.targetBusy,false);
  assert.equal(app.posts()[0].url,'/api/review-target/cases/case_two/draft');
});
