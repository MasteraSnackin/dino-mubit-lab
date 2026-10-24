/* Offline UI checks. Fixtures never access provider keys, game records or a network. */
'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'provider-comparison.js'),'utf8');
const copy=value=>JSON.parse(JSON.stringify(value));
const deferred=()=>{let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};};
const fixtureKey='sk-offline-fixture-not-a-real-key';
const preview=()=>({caseId:'case_fixture',caseRevision:3,inputHash:'a'.repeat(64),bodyHash:'b'.repeat(64),requestedModel:'fixture-model',pairCount:3,requestCount:6,mubitRequestCount:3,body:{model:'fixture-model',messages:[{role:'user',content:'Fixture only.'}]}});
const arm=(status='succeeded')=>({status,latencyMs:150,returnedModel:'fixture-model',requestId:'fixture_request',usage:{promptTokens:600,completionTokens:100,totalTokens:700,cachedPromptTokens:0,reasoningTokens:50}});
const stats=()=>({meanPromptTokens:600,meanCompletionTokens:100,meanTotalTokens:700,meanLatencyMs:150,jsonValidCount:3,assessmentCorrectCount:3,decisionCorrectCount:3,measurementMentionCount:2});
function receipt(status='succeeded') {return {id:'comparison_fixture',status,pairs:[1,2,3].map(index=>({index,mubit:arm(status==='pending'?'pending':'succeeded'),direct:status==='pending'?null:arm()})),summary:status==='succeeded'?{pairedSamples:3,allPairsValid:true,modelConsistencyVerified:true,mubit:stats(),direct:{...stats(),meanTotalTokens:720},caveat:'Three repeats of one fixture do not establish general performance.'}:null};}
function record(extra={}){return {status:'not_started',message:'Ready for one explicit comparison.',ready:true,preview:preview(),latest:null,exportAvailable:false,...extra};}
function saved(status='succeeded',extra={}){return record({status,ready:false,latest:receipt(status),exportAvailable:status!=='pending',...extra});}
function harness(options={}) {
  const panels=[],events={},requests=[],timers=new Map();let timerId=0;
  let resolver=options.resolve || ((url,init)=>init.method==='POST'?saved():record(options.record));
  class Event {
    constructor(type,init={}){this.type=type;Object.assign(this,init);this.defaultPrevented=false;}
    preventDefault(){if(this.cancelable)this.defaultPrevented=true;}
    stopPropagation(){}
  }
  class Element {
    constructor(tag){this.tagName=tag.toUpperCase();this.children=[];this.listeners={};this.attributes={};this.disabled=false;this.hidden=false;this.value='';this.textContent='';}
    append(...items){items.forEach(item=>{item.parent=this;this.children.push(item);});}
    replaceChildren(...items){this.children=[];this.append(...items);}
    setAttribute(name,value){this.attributes[name]=String(value);}
    getAttribute(name){return this.attributes[name]??null;}
    addEventListener(type,fn){(this.listeners[type]??=[]).push(fn);}
    dispatch(type){for(const fn of this.listeners[type] || [])fn(new Event(type,{cancelable:true}));}
    click(){if(!this.disabled)this.dispatch('click');}
    after(element){panels.push(element);}
  }
  const anchor=new Element('section'),document={readyState:'complete',createElement:tag=>new Element(tag),getElementById:()=>anchor,body:new Element('body')};
  const window={document,DinoLearning:{dirty:false,busy:false},DinoReviewTask:{dirty:false,targetBusy:false,busy:false},DinoLearningModes:{busy:false},DinoMubit:{busy:false,refreshStatus(){}},DinoExperiments:{busy:false},DinoLocalPlayer:{active:false},Runner:{instance_:{playing:false,crashed:false}},
    addEventListener(type,fn){(events[type]??=[]).push(fn);},dispatchEvent(event){for(const fn of events[event.type] || [])fn(event);return !event.defaultPrevented;}};
  const fetch=async(url,init={})=>{requests.push({url,method:init.method || 'GET',payload:init.body?JSON.parse(init.body):undefined});const value=await resolver(url,init);return {ok:value.ok!==false,json:async()=>value.ok===false?copy(value):{ok:true,comparison:copy(value)}};};
  vm.runInNewContext(source,{window,document,fetch,CustomEvent:Event,setTimeout(fn){const id=++timerId;timers.set(id,fn);return id;},clearTimeout(id){timers.delete(id);},console});
  const all=()=>{const found=[];function walk(el){found.push(el);el.children.forEach(walk);}panels.forEach(walk);return found;};
  const emit=(type,detail)=>window.dispatchEvent(new Event(type,{cancelable:true,detail}));
  return {window,requests,events,timers,emit,all,byText:text=>all().find(el=>el.textContent===text),field:()=>all().find(el=>el.id==='provider-comparison-key'),text:()=>all().map(el=>el.textContent).join('\n'),posts:()=>requests.filter(item=>item.method==='POST'),resolve(fn){resolver=fn;},
    async settle(){await new Promise(resolve=>setImmediate(resolve));await new Promise(resolve=>setImmediate(resolve));},
    enter(){const field=this.field();field.value=fixtureKey;field.dispatch('input');},
    tick(){const entry=timers.entries().next().value;if(entry){timers.delete(entry[0]);entry[1]();}}};
}

test('opening the comparison reads only status and does not start a request or keep a key',async()=>{
  const app=harness();await app.settle();
  assert.deepEqual(app.requests,[{url:'/api/provider-comparison',method:'GET',payload:undefined}]);
  assert.equal(app.posts().length,0);assert.equal(app.timers.size,0);
  assert.equal(app.field().type,'password');assert.equal(app.field().getAttribute('autocomplete'),'off');assert.equal(app.field().value,'');
  assert.equal(app.byText('Run six-request comparison').disabled,true);
  assert.match(app.text(),/six requests and may use credit/);assert.match(app.text(),/No remembered answers are added/);
});

test('one explicit click clears the key immediately, holds ownership and polls only GET while running',async()=>{
  const pending=deferred(),app=harness();await app.settle();app.enter();
  app.resolve((url,init)=>init.method==='POST'?pending.promise:saved('pending'));
  app.byText('Run six-request comparison').click();
  assert.equal(app.field().value,'');assert.equal(app.window.DinoProviderComparison.busy,true);
  assert.deepEqual(app.posts()[0],{url:'/api/provider-comparison',method:'POST',payload:{openaiKey:fixtureKey,expectedInputHash:'a'.repeat(64)}});
  assert.equal(app.emit('dino-controller-request',{owner:'local'}),false);
  assert.equal(app.emit('beforeunload'),false);
  app.tick();await app.settle();assert.equal(app.requests.at(-1).method,'GET');assert.equal(app.posts().length,1);
  assert.match(app.text(),/0 of 6 request outcomes recorded/);
  pending.resolve(saved());await app.settle();
  assert(app.byText('Comparison results saved'));assert.equal(app.byText('Run six-request comparison').disabled,true);assert.equal(app.window.DinoProviderComparison.busy,false);assert.equal(app.timers.size,0);
  assert.doesNotMatch(app.text(),new RegExp(fixtureKey));assert.equal(app.field().value,'');
});

test('dirty edits, saving and every existing controller prevent an explicit comparison',async()=>{
  const app=harness();await app.settle();app.enter();
  for(const [object,field,event] of [[app.window.DinoLearning,'dirty','dino-learning-case-changed'],[app.window.DinoLearning,'busy','dino-learning-case-changed'],[app.window.DinoReviewTask,'dirty','dino-review-target-state'],[app.window.DinoReviewTask,'targetBusy','dino-review-target-state'],[app.window.DinoReviewTask,'busy','dino-controller-change'],[app.window.DinoLearningModes,'busy','dino-controller-change'],[app.window.DinoMubit,'busy','dino-controller-change'],[app.window.DinoExperiments,'busy','dino-controller-change'],[app.window.DinoLocalPlayer,'active','dino-controller-change'],[app.window.Runner.instance_,'playing','dino-controller-change']]) {
    object[field]=true;app.emit(event);assert.equal(app.byText('Run six-request comparison').disabled,true);app.byText('Run six-request comparison').click();await app.settle();object[field]=false;app.emit(event);
  }
  assert.equal(app.posts().length,0);
});

for(const status of ['pending','succeeded','failed'])test(`a saved ${status} attempt never starts another comparison`,async()=>{
  const app=harness({record:saved(status)});await app.settle();app.enter();app.byText('Run six-request comparison').click();
  app.byText('Refresh comparison result').click();await app.settle();
  assert.equal(app.posts().length,0);assert.equal(app.field().value,'');assert.equal(app.byText('Run six-request comparison').disabled,true);assert.equal(app.timers.size,0);
  assert.equal(app.byText('Export comparison receipt').hidden,status==='pending');
});

test('unknown POST outcome stays locked even if refresh returns no saved receipt',async()=>{
  const app=harness();await app.settle();app.enter();app.resolve((url,init)=>{if(init.method==='POST')throw new Error('Fixture lost connection.');return record();});
  app.byText('Run six-request comparison').click();await app.settle();
  assert.match(app.text(),/request outcome is unknown/);app.byText('Refresh comparison result').click();await app.settle();app.enter();app.byText('Run six-request comparison').click();await app.settle();
  assert.equal(app.posts().length,1);assert.equal(app.field().value,'');assert.equal(app.byText('Run six-request comparison').disabled,true);
  assert.equal(app.window.DinoProviderComparison.busy,true);
});

test('pending evidence followed by read and POST failures retains controller ownership until terminal proof',async()=>{
  const pending=deferred(),app=harness();await app.settle();app.enter();
  app.resolve((url,init)=>init.method==='POST'?pending.promise:saved('pending'));
  app.byText('Run six-request comparison').click();app.tick();await app.settle();
  assert.equal(app.window.DinoProviderComparison.busy,true);
  app.resolve(()=>{throw new Error('Fixture status unavailable.');});app.tick();await app.settle();pending.reject(new Error('Fixture response interrupted.'));await app.settle();
  assert.equal(app.window.DinoProviderComparison.busy,true);assert.equal(app.emit('dino-controller-request',{owner:'local'}),false);assert.equal(app.timers.size,0);
  app.resolve(()=>saved('failed'));app.byText('Refresh comparison result').click();await app.settle();
  assert.equal(app.window.DinoProviderComparison.busy,false);assert.equal(app.posts().length,1);assert.equal(app.byText('Run six-request comparison').disabled,true);
});

test('an explicit no-inference preflight failure permits manual key re-entry without automatic retry',async()=>{
  const app=harness();await app.settle();app.enter();
  app.resolve((url,init)=>init.method==='POST'?{ok:false,code:'direct_preflight_failed',noInferenceSent:true,comparison:record({message:'OpenAI model access was not confirmed.'})}:record());
  app.byText('Run six-request comparison').click();await app.settle();
  assert.equal(app.window.DinoProviderComparison.busy,false);assert.equal(app.field().value,'');assert.equal(app.field().disabled,false);assert.equal(app.posts().length,1);assert.equal(app.timers.size,0);
  assert.match(app.text(),/No model request was sent/);assert.equal(app.byText('Run six-request comparison').disabled,true);
  app.enter();assert.equal(app.byText('Run six-request comparison').disabled,false);
  app.resolve((url,init)=>init.method==='POST'?saved():record());app.byText('Run six-request comparison').click();await app.settle();assert.equal(app.posts().length,2);assert.equal(app.field().value,'');
});

test('an unreadable refresh removes stale proof and export without adding any request',async()=>{
  const app=harness({record:saved()});await app.settle();assert.equal(app.byText('Export comparison receipt').hidden,false);
  app.resolve(()=>{throw new Error('Fixture read failed.');});app.byText('Refresh comparison result').click();await app.settle();
  assert.match(app.text(),/saved comparison state is unknown/);assert.equal(app.byText('Export comparison receipt').hidden,true);assert.equal(app.byText('Run six-request comparison').disabled,true);assert.equal(app.posts().length,0);
});

test('results show recorded averages and limited quality counts, with no cost or model-weight claim',async()=>{
  const value=saved();value.latest.summary.mubit.meanCompletionTokens=null;
  const app=harness({record:value});await app.settle();
  assert(app.byText('Observed averages across three matched pairs'));assert(app.byText('700.0'));assert(app.byText('720.0'));assert(app.byText('Not recorded'));
  assert.match(app.text(),/correct outcome 3\/3; expected decision 3\/3; all recorded measurements mentioned 2\/3/);
  assert.match(app.text(),/does not verify identical model weights/);assert.match(app.text(),/does not establish general quality, speed or cost savings/);
  assert.equal(app.byText('Export comparison receipt').href,'/api/provider-comparison/export');
});

test('missing model consistency is explicitly unverified and incomplete pairs have no averages',async()=>{
  const value=saved();value.latest.summary.modelConsistencyVerified=false;
  const app=harness({record:value});await app.settle();assert.match(app.text(),/model IDs have not been verified as matching/);
  value.latest.summary.allPairsValid=false;app.resolve(()=>value);app.byText('Refresh comparison result').click();await app.settle();
  assert.equal(app.byText('Observed averages across three matched pairs'),undefined);assert.match(app.text(),/Missing or failed requests are not counted as zero tokens/);
});

test('a late polling response cannot replace the completed POST receipt',async()=>{
  const pending=deferred(),lateRead=deferred(),app=harness();await app.settle();app.enter();
  app.resolve((url,init)=>init.method==='POST'?pending.promise:lateRead.promise);
  app.byText('Run six-request comparison').click();app.tick();await app.settle();pending.resolve(saved());await app.settle();lateRead.resolve(saved('pending'));await app.settle();
  assert(app.byText('Comparison results saved'));assert.equal(app.byText('Comparison in progress'),undefined);assert.equal(app.window.DinoProviderComparison.busy,false);assert.equal(app.timers.size,0);
});

test('page exit clears the field and polling; reopening can only read an existing attempt',async()=>{
  const pending=deferred(),app=harness();await app.settle();app.enter();app.resolve((url,init)=>init.method==='POST'?pending.promise:saved('pending'));
  app.byText('Run six-request comparison').click();assert.equal(app.timers.size,1);app.emit('pagehide');assert.equal(app.field().value,'');assert.equal(app.timers.size,0);
  pending.resolve(saved());await app.settle();assert.equal(app.posts().length,1);
});

test('reflected credential or transport error text is never rendered',async()=>{
  const app=harness();await app.settle();app.enter();app.resolve((url,init)=>init.method==='POST'?{ok:false,message:fixtureKey,comparison:record()}:record());
  app.byText('Run six-request comparison').click();await app.settle();assert.doesNotMatch(app.text(),new RegExp(fixtureKey));assert.equal(app.field().value,'');assert.match(app.text(),/request outcome is unknown/);
});

test('invalid preview cannot enable a paid comparison',async()=>{
  const bad=record();bad.preview.requestCount=8;
  const app=harness({record:bad});await app.settle();app.enter();app.byText('Run six-request comparison').click();await app.settle();assert.equal(app.posts().length,0);assert.equal(app.byText('Run six-request comparison').disabled,true);
});
