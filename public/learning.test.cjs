/* Offline UI state tests: no browser, local records, approval or provider call. */
'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'learning.js'),'utf8');
const clone=value=>JSON.parse(JSON.stringify(value));

function harness() {
  const panels=[],events={},requests=[];
  class Element {
    constructor(tag) {
      this.tagName=tag.toUpperCase();this.children=[];this.listeners={};this.attributes={};
      this._value='';this.disabled=false;this.checked=false;this.hidden=false;this.textContent='';this.style={};
    }
    append(...items) {
      for(const item of items) {
        item.parent=this;this.children.push(item);
        if(this.tagName==='SELECT' && this.children.length===1 && item.tagName==='OPTION')this._value=item.value;
      }
    }
    replaceChildren(...items) {this.children=[];if(this.tagName==='SELECT')this._value='';this.append(...items);}
    get firstChild() {return this.children[0];}
    set value(value) {this._value=String(value);}
    get value() {return this._value;}
    get options() {return this.children.filter(item=>item.tagName==='OPTION');}
    set selectedIndex(index) {this._value=this.options[index]?.value || '';}
    get selectedIndex() {return this.options.findIndex(item=>item.value===this._value);}
    setAttribute(key,value) {this.attributes[key]=String(value);}
    getAttribute(key) {return this.attributes[key]??null;}
    addEventListener(type,callback) {(this.listeners[type]??=[]).push(callback);}
    fire(type) {
      if(this.disabled)return;
      for(const callback of this.listeners[type] || [])callback({target:this,preventDefault(){},stopPropagation(){}});
    }
    click() {this.fire('click');}
    after(element) {panels.push(element);}
    querySelectorAll(selector) {
      const found=[];
      const visit=element=>{for(const child of element.children){found.push(child);visit(child);}};
      visit(this);
      return found.filter(element=>selector==='input' ? element.tagName==='INPUT' :
        selector==='input[type="checkbox"]:checked' ? element.tagName==='INPUT' && element.type==='checkbox' && element.checked :
        selector==='input:not([type="checkbox"]),select,textarea' ?
          (element.tagName==='INPUT' && element.type!=='checkbox') || ['SELECT','TEXTAREA'].includes(element.tagName) : false);
    }
  }
  const anchor=new Element('section');
  let record={id:'case_one',sourceExperimentId:'exp_one',createdAt:'2026-10-24T20:00:00Z',revision:0,
    source:{originalAnswer:{parameter:'duckExtraMs',value:80,rationale:'Maybe duck longer.',caveat:'Only two courses.'},
      model:'fixture-model',requestId:'mreq_fixture',baselineConfig:{jumpLeadMs:170,duckLeadMs:330,duckExtraMs:0,jumpHoldMs:240},
      comparison:{pairs:[{course:'a',baselineScore:539,candidateScore:539,baselineTicks:2897,candidateTicks:2897}],aggregate:{conclusion:'unchanged'}}},
    evidence:[{id:'comparison:a',type:'comparison',label:'Course A measured result'},
      {id:'baseline-a:frame:1',type:'frame',label:'Course A saved image',image:'data:image/png;base64,offline-fixture'}],
    revisions:[],latestDraft:null,approval:{status:'not_approved'}};
  let conflict=false,statusFailure=false;
  const measuredRecommendation={kind:'insufficient_evidence',rationale:'No improvement was measured.',
    caveat:'The cause is uncertain.',evidenceRefs:['comparison:a']};
  function saveDraft(origin,recommendation=measuredRecommendation,reason='Measured outcome.') {
    const draft={revision:record.revision+1,createdAt:'2026-10-24T20:01:00Z',action:'draft',origin,recommendation:clone(recommendation),reason};
    record={...record,revision:draft.revision,latestDraft:draft,revisions:[...record.revisions,draft],approval:{status:'not_approved'}};
  }
  const document={readyState:'complete',createElement:tag=>new Element(tag),querySelector:()=>anchor,
    getElementById:()=>anchor,body:new Element('body')};
  const window={document,DinoExperiments:{busy:false,open:async()=>true},
    addEventListener:(type,callback)=>(events[type]??=[]).push(callback),
    dispatchEvent:event=>(events[event.type] || []).forEach(callback=>callback(event))};
  const fetch=async(url,options={})=>{
    const payload=options.body && JSON.parse(options.body);requests.push({url,payload});let body;
    if(url==='/api/learning/status') {
      body=statusFailure ? {ok:false,message:'Fixture unreadable status.'} : {ok:true,status:{caseCount:1,
        draftCount:record.latestDraft?1:0,approvedCount:record.approval.status==='approved'?1:0,unavailableCount:0,
        provider:{message:'The training contract is unverified.'},plan:{trainingTarget:40,evaluationTarget:10,
          trainingCases:[{id:'train-01',split:'training',status:'not_started'}],
          evaluationCases:[{id:'test-01',split:'evaluation',status:'not_started'}]}}};
    } else if(url==='/api/learning/cases')body={ok:true,cases:[{id:record.id,sourceExperimentId:'exp_one',
      createdAt:record.createdAt,revision:record.revision,
      latestRevisionStatus:record.approval.status==='approved'?'approved':record.latestDraft?'draft':'none'}]};
    else if(url==='/api/experiments')body={ok:true,experiments:[{id:'exp_one',createdAt:record.createdAt,
      proposalStatus:'succeeded',comparison:{status:'complete'}}]};
    else if(url.endsWith('/result-draft')) {
      assert.equal(payload.expectedRevision,record.revision);saveDraft('machine-drafted');body={ok:true,case:clone(record)};
    } else if(url.endsWith('/draft')) {
      if(conflict)body={ok:false,code:'revision_conflict',message:'Another saved revision exists.'};
      else {assert.equal(payload.expectedRevision,record.revision);saveDraft('submitted',payload.recommendation,payload.reason);body={ok:true,case:clone(record)};}
    } else if(url.endsWith('/approve')) {
      assert.equal(payload.expectedRevision,record.revision);
      const approval={revision:record.revision+1,action:'approve',draftRevision:record.latestDraft.revision,
        createdAt:'2026-10-24T20:02:00Z',origin:'explicit-local-action',identityVerified:false};
      record={...record,revision:approval.revision,revisions:[...record.revisions,approval],
        approval:{status:'approved',draftRevision:record.latestDraft.revision,approvedAt:approval.createdAt}};
      body={ok:true,case:clone(record)};
    } else if(url==='/api/learning/cases/case_one')body={ok:true,case:clone(record)};
    else throw new Error('Unexpected offline-test endpoint: '+url);
    return {ok:true,json:async()=>body};
  };
  vm.runInNewContext(source,{window,document,fetch,console,
    CustomEvent:class{constructor(type,init){this.type=type;Object.assign(this,init);}},
    URL:{createObjectURL:()=>'',revokeObjectURL(){}},Blob:class{},setTimeout});
  function all() {const found=[];const visit=element=>{found.push(element);for(const child of element.children)visit(child);};panels.forEach(visit);return found;}
  const byText=text=>all().find(element=>element.textContent===text);
  const byLabel=label=>all().find(element=>element.attributes['aria-label']===label);
  const settle=async()=>{await new Promise(resolve=>setImmediate(resolve));await new Promise(resolve=>setImmediate(resolve));};
  const approvalCheckbox=()=>all().find(element=>element.tagName==='INPUT' && element.type==='checkbox' &&
    element.parent.children.some(child=>child.textContent==='I have checked this saved correction against the selected evidence.'));
  return {window,requests,byText,byLabel,all,settle,approvalCheckbox,
    record:()=>clone(record),conflict:()=>{conflict=true;},failStatus:()=>{statusFailure=true;}};
}

test('machine draft stays unapproved until a separate checked approval of the saved revision',async()=>{
  const app=harness();await app.settle();
  assert.equal(app.byText('Approve this saved correction').disabled,true);
  app.byText('Draft from measured result').click();await app.settle();
  assert.equal(app.record().revision,1);
  assert.equal(app.record().latestDraft.origin,'machine-drafted');
  assert.equal(app.record().approval.status,'not_approved');
  assert.equal(app.byText('Approve this saved correction').disabled,true);
  assert.equal(app.requests.filter(item=>item.url.endsWith('/approve')).length,0);
  const checkbox=app.approvalCheckbox();checkbox.checked=true;checkbox.fire('change');
  assert.equal(app.byText('Approve this saved correction').disabled,false);
  app.byText('Approve this saved correction').click();await app.settle();
  assert.equal(app.record().revision,2);
  assert.equal(app.record().approval.draftRevision,1);
  assert.equal(app.record().revisions[1].identityVerified,false);
  assert.equal(app.byText('Approve this saved correction').disabled,true);
  assert.equal(app.requests.filter(item=>item.url.endsWith('/approve')).length,1);
  assert(app.requests.every(item=>!item.url.startsWith('/api/mubit')));
});

test('dirty edits block case switching and approval; conflicts preserve the unsaved correction',async()=>{
  const app=harness();await app.settle();
  app.byText('Draft from measured result').click();await app.settle();
  const textarea=app.byLabel('Corrected review');textarea.value='The measured outcome did not improve; the cause remains uncertain.';textarea.fire('input');
  assert.equal(app.window.DinoLearning.dirty,true);
  assert.equal(app.byLabel('Saved correction cases').disabled,true);
  assert.equal(app.byText('Refresh records').disabled,true);
  assert.equal(app.byText('Approve this saved correction').disabled,true);
  app.byText('Save correction draft').click();await app.settle();
  assert.equal(app.record().revision,2);
  assert.equal(app.record().latestDraft.recommendation.rationale,textarea.value);
  assert.equal(app.window.DinoLearning.dirty,false);
  app.conflict();textarea.value='Preserve this alternative correction while resolving the conflict.';textarea.fire('input');
  app.byText('Save correction draft').click();await app.settle();
  assert.equal(app.record().revision,2);
  assert.equal(app.window.DinoLearning.dirty,true);
  assert.equal(app.byLabel('Corrected review').value,'Preserve this alternative correction while resolving the conflict.');
  assert.equal(app.byText('Download unsaved draft').disabled,false);
  assert.equal(app.byLabel('Saved correction cases').disabled,true);
  assert.equal(app.requests.filter(item=>item.url.endsWith('/approve')).length,0);
});

test('reserved test scenarios are distinct and training remains unverified with no automatic request',async()=>{
  const app=harness();await app.settle();
  const choices=app.byLabel('Reserved training or test scenario').options;
  assert.equal(choices.length,3);
  assert.equal(choices[1].value,'train-01');assert.match(choices[1].textContent,/Training/);
  assert.equal(choices[2].value,'test-01');assert.match(choices[2].textContent,/Reserved test/);
  assert(app.byText('Training connection not verified'));
  assert(app.all().some(element=>element.textContent.startsWith('No verified updated Mubit model yet.')));
  assert.equal(app.requests.some(item=>item.payload!==undefined),false);
  app.window.DinoExperiments.busy=true;app.window.dispatchEvent({type:'dino-controller-change'});
  assert.equal(app.byText('Prepare courses').disabled,true);
});

test('a failed refresh marks counts unknown rather than retaining confirmed-looking totals',async()=>{
  const app=harness();await app.settle();app.failStatus();await app.window.DinoLearning.refresh();await app.settle();
  assert(app.byText('Some local records could not be read. Counts are unknown; refresh to try again.'));
  assert.equal(app.all().filter(element=>element.tagName==='STRONG' && element.textContent==='—').length,3);
});

test('dirty or saving compact answer reviews block old-case selection, mutation and refresh without changing ownership state',async()=>{
  const app=harness();await app.settle();
  for(const state of [{dirty:true,targetBusy:false},{dirty:false,targetBusy:true}]){
    app.window.DinoReviewTask=state;app.window.dispatchEvent({type:'dino-review-target-state'});
    const before=app.requests.length;
    assert.equal(app.byLabel('Saved correction cases').disabled,true);
    assert.equal(app.byText('Refresh records').disabled,true);
    assert.equal(app.byText('Draft from measured result').disabled,true);
    assert.equal(app.byLabel('Corrected review').disabled,true);
    app.byText('Draft from measured result').click();await app.window.DinoLearning.refresh();await app.settle();
    assert.equal(app.requests.length,before);assert.equal(app.record().revision,0);
    assert.equal(app.window.DinoLearning.busy,false);
  }
  app.window.DinoReviewTask={dirty:false,targetBusy:false};app.window.dispatchEvent({type:'dino-review-target-state'});
  assert.equal(app.byLabel('Saved correction cases').disabled,false);
  assert.equal(app.byText('Refresh records').disabled,false);
});
