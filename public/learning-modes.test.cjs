/* Offline UI tests: no real provider, local training, source record or key access. */
'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'learning-modes.js'),'utf8');
const clone=value=>JSON.parse(JSON.stringify(value));
const deferred=()=>{let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};};
const selected=(id='case_two',revision=3,approved=true)=>({id,revision,approved});
const answer={assessment:'unchanged',decision:'retain_baseline',rationale:'Both saved courses have unchanged survival times.',caveat:'These courses do not establish general performance.'};
const attempt=(status='succeeded')=>({id:'memory_attempt_fixture',status,model:'fixture-model',requestId:'mreq_memory_fixture',latencyMs:190,usage:{totalTokens:900},result:status==='succeeded'?clone(answer):null,assessmentMatchesMeasurements:status==='succeeded'?true:null,message:'The saved request is preserved.'});
function memoryRecord(item=selected(),extra={}) {
  return {caseId:item.id,caseRevision:item.revision,taskVersion:'memory-assisted-review-v1',model:'fixture-model',inputHash:'c'.repeat(64),
    input:{current:{runId:'review_current'},memories:[{input:{runId:'review_prior'},approvedAnswer:clone(answer),reviewType:'confirmed_reference'}]},
    memories:[{targetId:'target_prior',targetRevision:2,sourceHash:'a'.repeat(64),inputHash:'b'.repeat(64),reviewType:'confirmed_reference',sameCourseFamily:true,sourceCaseId:'case_one',approvedAt:'2026-10-24T10:00:00Z',model:'fixture-model',requestId:'mreq_prior'}],
    attempt:null,baseline:null,requestAllowed:item.approved,connectionVerified:true,weightUpdate:false,modelUpdateVerified:false,message:'One approved reference is eligible.',...extra};
}
function localRecord(extra={}) {
  return {status:'not_started',message:'Local training has not started.',model:{id:'fixture-open-model',revision:'commit-fixture',source:'fixture'},trainingExamples:1,correctedExamples:0,confirmedReferences:1,adapterId:null,weightsChanged:null,baseline:null,adapted:null,independentEvaluation:{status:'not_available',examples:0,message:'Independent test performance has not been established.'},exportAvailable:false,...extra};
}
function harness(options={}) {
  const panels=[],events={},requests=[];
  let choice=options.selection===undefined?selected():options.selection;
  let memoryResolver=options.memoryResolve || ((url,init)=>memoryRecord(choice,init.method==='POST'?{attempt:attempt(),requestAllowed:false}:options.memory || {}));
  let localResolver=options.localResolve || (()=>localRecord(options.local || {}));
  class Event {
    constructor(type,init={}){this.type=type;Object.assign(this,init);this.defaultPrevented=false;}
    preventDefault(){if(this.cancelable)this.defaultPrevented=true;}
    stopPropagation(){}
  }
  class Element {
    constructor(tag){this.tagName=tag.toUpperCase();this.children=[];this.listeners={};this.attributes={};this.disabled=false;this.hidden=false;this.textContent='';}
    append(...items){items.forEach(item=>{item.parent=this;this.children.push(item);});}
    replaceChildren(...items){this.children=[];this.append(...items);}
    setAttribute(name,value){this.attributes[name]=String(value);}
    getAttribute(name){return this.attributes[name]??null;}
    addEventListener(type,fn){(this.listeners[type]??=[]).push(fn);}
    click(){if(!this.disabled)(this.listeners.click || []).forEach(fn=>fn(new Event('click',{cancelable:true})));}
    after(element){panels.push(element);}
  }
  const anchor=new Element('section'),document={readyState:'complete',createElement:tag=>new Element(tag),getElementById:()=>anchor,body:new Element('body')};
  const window={document,DinoLearning:{get selectedCase(){return choice&&clone(choice);},dirty:false,busy:false},DinoReviewTask:{dirty:false,targetBusy:false,busy:false},
    DinoMubit:{status:{model:'fixture-model',ready:true,verified:true},busy:false,refreshStatus(){}},DinoExperiments:{busy:false},DinoLocalPlayer:{active:false},Runner:{instance_:{playing:false,crashed:false}},
    addEventListener(type,fn){(events[type]??=[]).push(fn);},dispatchEvent(event){(events[event.type] || []).forEach(fn=>fn(event));return !event.defaultPrevented;}};
  const fetch=async(url,init={})=>{
    requests.push({url,method:init.method || 'GET',payload:init.body?JSON.parse(init.body):undefined});
    const isLocal=url==='/api/local-adapter/status',value=await (isLocal?localResolver:memoryResolver)(url,init);
    return {ok:value.ok!==false,json:async()=>value.ok===false?value:{ok:true,[isLocal?'localAdapter':'memoryReview']:clone(value)}};
  };
  vm.runInNewContext(source,{window,document,fetch,CustomEvent:Event,console});
  const all=()=>{const result=[];function walk(el){result.push(el);el.children.forEach(walk);}panels.forEach(walk);return result;};
  const emit=(type,detail)=>window.dispatchEvent(new Event(type,{cancelable:true,detail}));
  return {window,requests,emit,all,byText:text=>all().find(el=>el.textContent===text),text:()=>all().map(el=>el.textContent).join('\n'),posts:()=>requests.filter(item=>item.method==='POST'),
    async settle(){await new Promise(resolve=>setImmediate(resolve));await new Promise(resolve=>setImmediate(resolve));},
    select(value){choice=value;emit('dino-learning-case-changed');},memoryResolve(fn){memoryResolver=fn;},localResolve(fn){localResolver=fn;}};
}

test('initial mode reads never invoke a model or start training; memory call requires an explicit click',async()=>{
  const app=harness();await app.settle();
  assert.deepEqual(app.requests.map(item=>[item.url,item.method]),[['/api/memory-review/cases/case_two','GET'],['/api/local-adapter/status','GET']]);
  assert.equal(app.posts().length,0);
  assert.match(app.text(),/Confirmed reference · target_prior · revision 2 · same course family/);
  assert.match(app.text(),/This does not change model weights/);
  assert.equal(app.byText('Export local training receipt').hidden,true);
  app.byText('Ask Mubit using memory').click();await app.settle();
  assert.deepEqual(app.posts(),[{url:'/api/memory-review/cases/case_two/review',method:'POST',payload:{expectedRevision:3,expectedInputHash:'c'.repeat(64)}}]);
  assert(app.byText('Memory-assisted reply saved'));
  assert.equal(app.byText('Ask Mubit using memory').disabled,true);
  assert.equal(app.byText('Export memory receipt').href,'/api/memory-review/cases/case_two/export');
  assert.equal(app.posts().some(item=>item.url.startsWith('/api/local-adapter/')),false);
});

test('dirty or saving reviews block memory reads and requests while local status remains read only',async()=>{
  const app=harness();await app.settle();
  for(const [object,field,event] of [[app.window.DinoLearning,'dirty','dino-learning-case-changed'],[app.window.DinoLearning,'busy','dino-learning-case-changed'],[app.window.DinoReviewTask,'dirty','dino-review-target-state'],[app.window.DinoReviewTask,'targetBusy','dino-review-target-state']]) {
    const before=app.requests.length;object[field]=true;app.emit(event);
    app.byText('Ask Mubit using memory').click();app.byText('Refresh memory review').click();await app.settle();
    assert.equal(app.requests.length,before);assert.equal(app.byText('Ask Mubit using memory').disabled,true);
    object[field]=false;app.emit(event);
  }
  assert.equal(app.posts().length,0);
  app.byText('Refresh local training result').click();await app.settle();
  assert.equal(app.requests.at(-1).method,'GET');
});

test('gameplay and every model controller block the explicit memory request',async()=>{
  const app=harness();await app.settle();
  for(const [object,field] of [[app.window.Runner.instance_,'playing'],[app.window.DinoLocalPlayer,'active'],[app.window.DinoExperiments,'busy'],[app.window.DinoMubit,'busy'],[app.window.DinoReviewTask,'busy']]) {
    object[field]=true;app.emit('dino-controller-change');assert.equal(app.byText('Ask Mubit using memory').disabled,true);
    app.byText('Ask Mubit using memory').click();await app.settle();object[field]=false;
  }
  assert.equal(app.posts().length,0);
});

for(const status of ['pending','failed','succeeded'])test(`saved ${status} memory attempts cannot be resent`,async()=>{
  const app=harness({memory:{attempt:attempt(status),requestAllowed:false}});await app.settle();
  app.byText('Ask Mubit using memory').click();app.byText('Refresh memory review').click();await app.settle();
  assert.equal(app.posts().length,0);assert.equal(app.byText('Ask Mubit using memory').disabled,true);
  assert.equal(app.byText('Export memory receipt').hidden,false);
});

test('uncertain POST outcome stays blocked even if a later read has no attempt',async()=>{
  const app=harness();await app.settle();
  app.memoryResolve((url,init)=>{if(init.method==='POST')throw new Error('Fixture interrupted.');return memoryRecord();});
  app.byText('Ask Mubit using memory').click();await app.settle();
  assert.match(app.text(),/Request outcome unknown/);
  app.byText('Refresh memory review').click();await app.settle();app.byText('Ask Mubit using memory').click();await app.settle();
  assert.equal(app.posts().length,1);assert.equal(app.byText('Ask Mubit using memory').disabled,true);
});

test('changed memory context is a definite no-send rejection and needs a fresh inspected preview',async()=>{
  const app=harness();await app.settle();
  app.memoryResolve((url,init)=>init.method==='POST'?{ok:false,code:'context_changed',message:'The approved memory set changed.'}:memoryRecord(selected(),{inputHash:'d'.repeat(64)}));
  app.byText('Ask Mubit using memory').click();await app.settle();
  assert.match(app.text(),/Nothing was sent\. Refresh and inspect/);assert.equal(app.byText('Ask Mubit using memory').disabled,true);
  const before=app.requests.length;app.emit('dino-mubit-status');await app.settle();assert.equal(app.requests.length,before);
  app.byText('Refresh memory review').click();await app.settle();
  assert.equal(app.byText('Ask Mubit using memory').disabled,false);
  app.memoryResolve((url,init)=>memoryRecord(selected(),{inputHash:'d'.repeat(64),...(init.method==='POST'?{attempt:attempt(),requestAllowed:false}:{})}));
  app.byText('Ask Mubit using memory').click();await app.settle();
  assert.equal(app.posts().length,2);assert.equal(app.posts()[1].payload.expectedInputHash,'d'.repeat(64));
});

test('late preview cannot enable a request for a previously selected case',async()=>{
  const old=deferred(),app=harness({memoryResolve:url=>url.endsWith('case_two')?old.promise:memoryRecord(selected('case_three',7))});
  app.select(selected('case_three',7));await app.settle();old.resolve(memoryRecord());await app.settle();
  assert.match(app.text(),/Selected case case_three · revision 7/);assert.doesNotMatch(app.text(),/Selected case case_two/);
  app.memoryResolve((url,init)=>memoryRecord(selected('case_three',7),init.method==='POST'?{attempt:attempt(),requestAllowed:false}:{}));
  app.byText('Ask Mubit using memory').click();await app.settle();
  assert.deepEqual(app.posts()[0].payload,{expectedRevision:7,expectedInputHash:'c'.repeat(64)});assert.match(app.posts()[0].url,/case_three/);
});

test('pending memory request owns the controller and cannot replace a new case with its late reply',async()=>{
  const pending=deferred(),app=harness();await app.settle();
  app.memoryResolve((url,init)=>init.method==='POST'?pending.promise:memoryRecord(selected('case_three',6)));
  app.byText('Ask Mubit using memory').click();await app.settle();
  assert.equal(app.window.DinoLearningModes.busy,true);assert.equal(app.emit('dino-controller-request',{owner:'local'}),false);
  const unload={type:'beforeunload',preventDefault(){this.prevented=true;}};app.window.dispatchEvent(unload);assert.equal(unload.prevented,true);
  app.select(selected('case_three',6));await app.settle();pending.resolve(memoryRecord(selected(),{attempt:attempt(),requestAllowed:false}));await app.settle();
  assert.match(app.text(),/Selected case case_three · revision 6/);assert.doesNotMatch(app.text(),/Memory-assisted reply saved/);
  assert.equal(app.window.DinoLearningModes.busy,false);assert.equal(app.posts().length,1);
});

test('wrong revision and an unreadable record stay unknown without automatic repeats',async()=>{
  for(const resolve of [()=>memoryRecord(selected('case_two',4)),()=>{throw new Error('Fixture read unavailable.');}]) {
    const app=harness({memoryResolve:resolve});await app.settle();const before=app.requests.length;
    app.emit('dino-learning-case-changed');await app.settle();
    assert.equal(app.requests.length,before);assert.equal(app.byText('Ask Mubit using memory').disabled,true);assert.equal(app.posts().length,0);
    assert.match(app.text(),/memory-review state is unknown/);
  }
});

test('no eligible other reference is shown honestly and cannot invoke memory',async()=>{
  const app=harness({memory:{memories:[],requestAllowed:false,message:'No eligible approved reference from another input is available.'}});await app.settle();
  assert.match(app.text(),/0 approved references selected/);assert.match(app.text(),/No eligible approved reference/);
  assert.equal(app.byText('Ask Mubit using memory').disabled,true);assert.equal(app.posts().length,0);
});

test('same-input memory comparison is shown only for the same model',async()=>{
  const baseline={...attempt(),latencyMs:120,sameModel:true};
  const app=harness({memory:{baseline,attempt:attempt(),requestAllowed:false}});await app.settle();
  assert(app.byText('Without memory'));assert(app.byText('With memory'));assert(app.byText('120 ms'));assert(app.byText('190 ms'));
  assert.match(app.text(),/does not establish a general quality or speed improvement/);
  app.memoryResolve(()=>memoryRecord(selected(),{baseline:{...baseline,sameModel:false},attempt:attempt(),requestAllowed:false}));
  app.byText('Refresh memory review').click();await app.settle();
  assert.equal(app.byText('Without memory'),undefined);assert.match(app.text(),/baseline used a different model/);
});

test('real local weight evidence stays distinct from one-reference and independent-evaluation limits',async()=>{
  const replay={text:JSON.stringify(answer),answer:clone(answer),jsonValid:true,elapsedSeconds:1.25,label:'in_sample_training_replay'};
  const app=harness({local:{status:'succeeded',message:'Real adapter training completed.',adapterId:'adapter_sha_fixture',weightsChanged:true,baseline:replay,adapted:{...replay,timingIncludesModelLoad:true},exportAvailable:true}});await app.settle();
  assert(app.byText('Local model weights changed in this run.'));
  assert.match(app.text(),/one-example pilot using a confirmed reference/);
  assert.match(app.text(),/does not demonstrate learning from a corrected model error/);
  assert.match(app.text(),/do not measure performance on independent test cases/);
  assert(app.byText('Response time: 1.25 seconds'));
  assert(app.byText('Time including model load: 1.25 seconds'));
  assert.equal(app.byText('Export local training receipt').hidden,false);
  assert.equal(app.byText('Export local training receipt').href,'/api/local-adapter/export');
  app.byText('Refresh local training result').click();await app.settle();assert.equal(app.posts().length,0);
});

test('missing local proof is unknown and a failed refresh removes stale success and export',async()=>{
  const app=harness({local:{status:'succeeded',weightsChanged:true,adapterId:null,exportAvailable:false}});await app.settle();
  assert.equal(app.byText('Local model weights changed in this run.'),undefined);
  assert(app.byText('A local model-weight change has not been verified.'));
  app.localResolve(()=>{throw new Error('Fixture record unreadable.');});app.byText('Refresh local training result').click();await app.settle();
  assert(app.byText('Local training state is unknown.'));assert.equal(app.byText('Export local training receipt').hidden,true);
  assert.equal(app.posts().length,0);
});
