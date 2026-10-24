const test = require('node:test');
const assert = require('node:assert/strict');
const policy = require('./local-policy.js');
function scene(night = false) {
  const width = 600, height = 150, data = new Uint8ClampedArray(width * height * 4);
  for (let i = 0; i < data.length; i += 4) {
    data[i] = data[i + 1] = data[i + 2] = night ? 15 : 255; data[i + 3] = 255;
  }
  const rect = (x, y, w, h, value = night ? 215 : 80) => {
    for (let py = y; py < y + h; py++) for (let px = x; px < x + w; px++) {
      const i = (py * width + px) * 4;
      data[i] = data[i + 1] = data[i + 2] = value;
    }
  };
  return { image: { data, width, height }, rect };
}
test('score, pale cloud, ground and dinosaur are not obstacles', () => {
  const s = scene(); s.rect(450, 2, 120, 12); s.rect(200, 70, 45, 8, 212);
  s.rect(0, 127, 600, 1); s.rect(50, 93, 44, 33);
  const view = policy.analyse(s.image);
  assert.equal(view.obstacles.length, 0); assert.equal(view.player.airborne, false);
});
test('detects a cactus group without reading game objects', () => {
  const s = scene(); s.rect(50, 93, 44, 33); s.rect(180, 105, 17, 21); s.rect(202, 105, 17, 21);
  const view = policy.analyse(s.image);
  assert.equal(view.obstacles.length, 1); assert.equal(view.obstacles[0].kind, 'ground');
  assert.equal(view.obstacles[0].width, 39);
});
test('distinguishes mid and harmless high birds', () => {
  const s = scene(); s.rect(150, 85, 35, 20); s.rect(250, 60, 35, 20);
  assert.deepEqual(policy.analyse(s.image).obstacles.map(x => x.kind), ['bird', 'overhead']);
});
test('inverted opaque scenes use bright foreground', () => {
  const s = scene(true); s.rect(50, 93, 44, 33); s.rect(150, 90, 25, 36); s.rect(300, 70, 40, 10, 50);
  const view = policy.analyse(s.image);
  assert.equal(view.darkBackground, true); assert.equal(view.obstacles.length, 1);
  assert.equal(policy.decide(view, policy.initialState(), 1000).action, 'jump');
});
test('transparent empty canvas does not become a solid black obstacle', () => {
  const image = { width: 600, height: 150, data: new Uint8ClampedArray(600 * 150 * 4) };
  assert.equal(policy.analyse(image).obstacles.length, 0);
});
test('motion estimate derives speed from successive visible positions', () => {
  const first = { player:{right:94,airborne:false},obstacles:[{left:300,width:25,kind:'ground'}] };
  const a = policy.decide(first, policy.initialState(), 100);
  const b = policy.decide({...first,obstacles:[{left:280,width:25,kind:'ground'}]}, a.state, 140);
  assert.ok(b.state.speed > 360 && b.state.speed < 500);
});
test('airborne and recent jump observations do not retrigger jump', () => {
  const view = { player:{right:94,airborne:true},obstacles:[{left:160,width:25,kind:'ground'}] };
  assert.equal(policy.decide(view,policy.initialState(),1000).action,'run');
  view.player.airborne = false;
  assert.equal(policy.decide(view,{...policy.initialState(),lastJumpAt:900},1000).action,'run');
});
test('duck duration covers the visible mid-height bird crossing', () => {
  const view={player:{right:94,airborne:false},obstacles:[{left:180,width:46,kind:'bird'}]};
  const a=policy.decide(view,policy.initialState(),1000);
  assert.equal(a.action,'duck');
  assert.equal(policy.decide({player:view.player,obstacles:[]},a.state,1200).action,'duck');
});
test('a new distant obstacle does not poison speed estimate', () => {
  const state={...policy.initialState(),last:{left:115,width:25,kind:'ground'},lastAt:100};
  const result=policy.decide({player:{right:94},obstacles:[{left:450,width:25,kind:'ground'}]},state,140);
  assert.equal(result.state.speed,360); assert.equal(result.action,'run');
});
test('pixel anchor separates a left-edge dinosaur from a nearby cactus', () => {
  const s = scene(); s.rect(2, 95, 40, 31); s.rect(108, 106, 17, 20);
  const view = policy.analyse(s.image, {playerAnchor:2});
  assert.equal(view.player.right,41);
  assert.equal(view.obstacles[0].left,108); assert.equal(view.obstacles[0].width,17);
});
test('wide cactus group uses a later jump window to clear its trailing edge', () => {
  const state = policy.initialState();
  const view = {player:{right:94,airborne:false},obstacles:[{left:150,width:75,kind:'ground'}]};
  assert.equal(policy.decide(view,state,1000).action,'run');
  view.obstacles[0].left = 140;
  assert.equal(policy.decide(view,state,1035).action,'jump');
  view.obstacles[0] = {left:150,width:25,kind:'ground'};
  assert.equal(policy.decide(view,state,1035).action,'jump');
});
test('explicit baseline settings preserve the existing policy exactly', () => {
  const view = {player:{right:94,airborne:false},obstacles:[{left:150,width:25,kind:'ground'}]};
  const baseline = {jumpLeadMs:170,duckLeadMs:330,duckExtraMs:0,jumpHoldMs:240};
  assert.deepEqual(policy.decide(view,policy.initialState(),1000,baseline),policy.decide(view,policy.initialState(),1000));
});
test('jump lead changes only the take-off threshold', () => {
  const view = {player:{right:94,airborne:false},obstacles:[{left:170,width:25,kind:'ground'}]};
  assert.equal(policy.decide(view,policy.initialState(),1000).action,'run');
  assert.equal(policy.decide(view,policy.initialState(),1000,{jumpLeadMs:240}).action,'jump');
});
test('duck lead changes approach threshold and extra time extends the hold', () => {
  const view = {player:{right:94,airborne:false},obstacles:[{left:250,width:46,kind:'bird'}]};
  assert.equal(policy.decide(view,policy.initialState(),1000).action,'run');
  const early = policy.decide(view,policy.initialState(),1000,{duckLeadMs:500});
  const held = policy.decide(view,policy.initialState(),1000,{duckLeadMs:500,duckExtraMs:120});
  assert.equal(early.action,'duck'); assert.equal(held.state.duckUntil,early.state.duckUntil+120);
});
