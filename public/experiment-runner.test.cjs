const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const makeExperimentRunner = require('./experiment-runner.js');
const nativeSource = fs.readFileSync(require.resolve('./index.js'), 'utf8');
const normal = { jumpLeadMs: 170, duckLeadMs: 330, duckExtraMs: 0, jumpHoldMs: 240 };
class CustomEvent extends Event {
  constructor(type, options = {}) { super(type, options); this.detail = options.detail; }
}
function classes() {
  const set = new Set();
  return { contains: name => set.has(name), add: name => set.add(name), remove: name => set.delete(name),
    toggle(name, value) { if (value === undefined) value = !set.has(name); value ? set.add(name) : set.delete(name); return value; } };
}
function canvas() {
  const result = { width: 600, height: 150, style: {}, toDataURL: () => 'data:image/png;base64,aGVsbG8=' };
  const context = { canvas: result, clearRect() {}, fillRect() {}, drawImage() {}, save() {}, restore() {}, translate() {}, scale() {},
    getImageData() { return { width: 600, height: 150, data: new Uint8ClampedArray(600 * 150 * 4) }; } };
  result.getContext = () => context;
  return result;
}
function environment(native = false) {
  let clock = 1000, nextId = 1;
  const callbacks = new Map();
  const win = new EventTarget(), doc = new EventTarget();
  Object.assign(doc, { hidden: false, visibilityState: 'visible', body: { classList: classes() },
    createElement: tag => tag === 'canvas' ? canvas() : { classList: classes(), style: {} } });
  Object.assign(win, { document: doc, CustomEvent, devicePixelRatio: 1, navigator: { platform: 'MacIntel', userAgent: '' },
    performance: { now: () => clock },
    requestAnimationFrame(callback) { const id = nextId++; callbacks.set(id, callback); return id; },
    cancelAnimationFrame(id) { callbacks.delete(id); },
    DinoLocalPolicy: { initialState: () => ({}), analyse: image => ({ image }), decide: () => ({ action: 'run', state: {}, reason: 'Keep running.' }) }
  });
  const input = [], restored = [];
  let runner;
  if (native) {
    const context = { window: win, document: doc, performance: win.performance,
      requestAnimationFrame: win.requestAnimationFrame, cancelAnimationFrame: win.cancelAnimationFrame,
      setInterval, clearInterval, setTimeout, clearTimeout, console, Date, Math };
    vm.runInNewContext(nativeSource, context);
    runner = Object.create(win.Runner.prototype);
    Object.assign(runner, { config: win.Runner.config, dimensions: { WIDTH: 600, HEIGHT: 150 },
      canvas: canvas(), containerEl: { classList: classes(), style: {} }, spriteDef: win.Runner.spriteDefinition.LDPI,
      highestScore: 12345, distanceRan: 0, currentSpeed: 6, msPerFrame: 1000 / 60,
      horizon: { existing: true }, tRex: { existing: true }, distanceMeter: { getActualDistance: value => value },
      playing: false, activated: false, paused: true, crashed: false, soundFx: {}, resizeTimerId_: null });
    runner.canvasCtx = runner.canvas.getContext('2d');
    win.Runner.instance_ = runner;
  } else {
    runner = { canvas: canvas(), dimensions: { WIDTH: 600 }, distanceRan: 0,
      distanceMeter: { getActualDistance: value => value }, crashed: false };
    win.Runner = { instance_: runner, beginExperiment() {
      runner.distanceRan = 0; runner.crashed = false;
      return { step() { runner.distanceRan++; }, input(key, pressed) { input.push([runner.distanceRan, key, pressed]); },
        course: () => [], restore() { restored.push(true); } };
    } };
  }
  const api = makeExperimentRunner(win);
  return { win, doc, api, runner, input, restored, callbacks,
    advance(ms) { clock += ms; const batch = [...callbacks.values()]; callbacks.clear(); batch.forEach(callback => callback(clock)); },
    start(extra = {}) { return api.run({ slot: 'baseline-a', runId: 'test-run', seed: 104729, maxTicks: 3600, config: normal, ...extra }); },
    drive(hz, max = 10000) { for (let i = 0; api.active && i < max; i++) this.advance(1000 / hz); assert.equal(api.active, false); }
  };
}
function comparable(record) {
  return { ticks: record.ticks, score: record.score, outcome: record.outcome, actions: record.actions, course: record.course };
}
test('native course, score, actions and collision tick repeat at 60 and 120 Hz', async () => {
  const a = environment(true), b = environment(true);
  const first = a.start(), second = b.start();
  a.drive(60); b.drive(120);
  const recordA = await first, recordB = await second;
  assert.equal(recordA.outcome, 'collision');
  assert.ok(recordA.course.length > 0);
  assert.equal(JSON.stringify(comparable(recordA)), JSON.stringify(comparable(recordB)));
  assert.ok(Math.abs(recordA.wallDurationMs - recordA.ticks * 1000 / 60) < 20);
  assert.equal(recordA.frames.at(-1).tick, recordA.ticks);
});
test('native visual RNG calls cannot change course choices', () => {
  const a = environment(true), b = environment(true);
  const first = a.win.Runner.beginExperiment(104729), second = b.win.Runner.beginExperiment(104729);
  for (let i = 0; i < 300; i++) {
    first.step();
    b.runner.horizon.nightMode.placeStars();
    b.runner.horizon.addCloud();
    second.step();
  }
  assert.equal(JSON.stringify(first.course()), JSON.stringify(second.course()));
  first.restore(); second.restore();
});
test('fresh objects and native key release are restored with normal high score', () => {
  const e = environment(true), original = { horizon: e.runner.horizon, tRex: e.runner.tRex, meter: e.runner.distanceMeter,
    down: e.runner.onKeyDown, up: e.runner.onKeyUp, update: e.runner.update, highest: e.runner.highestScore };
  const session = e.win.Runner.beginExperiment(104729);
  assert.notEqual(e.runner.horizon, original.horizon);
  assert.notEqual(e.runner.tRex, original.tRex);
  assert.notEqual(e.runner.distanceMeter, original.meter);
  assert.equal(e.runner.tRex.xPos, 50); assert.equal(e.runner.tRex.jumping, false);
  let released = 0;
  e.runner.tRex.endJump = () => released++;
  session.input(38, true); session.input(38, false);
  assert.equal(released, 1, 'native keyup reaches endJump without a native rAF ID');
  session.restore(); session.restore();
  assert.equal(e.runner.horizon, original.horizon); assert.equal(e.runner.tRex, original.tRex);
  assert.equal(e.runner.distanceMeter, original.meter); assert.equal(e.runner.onKeyDown, original.down);
  assert.equal(e.runner.onKeyUp, original.up); assert.equal(e.runner.update, original.update);
  assert.equal(e.runner.highestScore, original.highest); assert.equal(e.runner.playing, false);
  assert.equal(e.runner.updatePending, false);
});
test('native initialisation failure restores original objects and releases engine context', () => {
  const e = environment(true), old = e.runner.horizon;
  const draw = e.runner.canvasCtx.drawImage;
  e.runner.canvasCtx.drawImage = () => { throw new Error('Canvas failure'); };
  assert.throws(() => e.win.Runner.beginExperiment(104729), /Canvas failure/);
  assert.equal(e.runner.horizon, old); assert.equal(e.runner.highestScore, 12345);
  e.runner.canvasCtx.drawImage = draw;
  e.win.Runner.beginExperiment(104729).restore();
});
test('jump hold parameter releases the native key on deterministic ticks', async () => {
  const e = environment();
  e.win.DinoLocalPolicy.decide = (_view, state, now) => ({ action: now === 0 ? 'jump' : 'run', state, reason: 'Test jump.' });
  const promise = e.start({ maxTicks: 30, config: { ...normal, jumpHoldMs: 120 } });
  e.drive(120);
  const result = await promise;
  assert.equal(result.outcome, 'time_limit');
  assert.deepEqual(e.input, [[0, 38, true], [8, 38, false]]);
  assert.equal(result.actions.find(event => event.reason.startsWith('Release')).tick, 8);
  assert.equal(e.restored.length, 1);
});
test('long frame gap aborts before catching up and restores ownership', async () => {
  const e = environment(), owners = [];
  e.win.addEventListener('dino-controller-change', event => owners.push(event.detail.owner));
  const promise = e.start(); e.advance(300);
  const result = await promise;
  assert.equal(result.outcome, 'lagged'); assert.equal(result.ticks, 0);
  assert.equal(result.maxFrameGapMs, 300); assert.deepEqual(owners, ['experiment', null]);
  assert.equal(e.restored.length, 1);
});
for (const kind of ['hidden', 'resized', 'stopped', 'error']) {
  test(`${kind} ends and restores the trial`, async () => {
    const e = environment(), promise = e.start();
    if (kind === 'hidden') { e.doc.hidden = true; e.doc.dispatchEvent(new Event('visibilitychange')); }
    if (kind === 'resized') e.win.dispatchEvent(new Event('resize'));
    if (kind === 'stopped') e.api.stop();
    if (kind === 'error') { e.win.DinoLocalPolicy.analyse = () => { throw new Error('bad pixels'); }; e.advance(20); }
    const result = await promise;
    assert.equal(result.outcome, kind); assert.equal(e.api.active, false); assert.equal(e.restored.length, 1);
  });
}
test('ownership lock is symmetric and rejected starts never claim control', async () => {
  const e = environment();
  e.win.dispatchEvent(new CustomEvent('dino-controller-change', { detail: { owner: 'local' } }));
  await assert.rejects(e.start(), /Another controller/);
  e.win.dispatchEvent(new CustomEvent('dino-controller-change', { detail: { owner: null } }));
  const promise = e.start();
  const request = new CustomEvent('dino-controller-request', { cancelable: true, detail: { owner: 'mubit' } });
  assert.equal(e.win.dispatchEvent(request), false);
  e.api.stop(); await promise;
  assert.equal(e.win.dispatchEvent(new CustomEvent('dino-controller-request', { cancelable: true, detail: { owner: 'local' } })), true);
});
test('complete 60-second run preserves bounded frames and final tick', async () => {
  const e = environment(), promise = e.start(); e.drive(60);
  const result = await promise;
  assert.equal(result.ticks, 3600); assert.equal(result.wallDurationMs, 60000);
  assert.ok(result.frames.length <= 60); assert.equal(result.frames.at(-1).tick, 3600);
  assert.ok(result.frames.at(-2).tick >= 3570);
  assert.deepEqual(Object.keys(result).sort(), ['slot','runId','protocol','seed','maxTicks','config','startedAt','endedAt','wallDurationMs','ticks','score','outcome','canvasWidth','maxFrameGapMs','actions','frames','course'].sort());
});

test('restore does not retain a cancelled native resize debounce ID', () => {
  const e = environment(true);
  e.runner.resizeTimerId_ = setInterval(() => {}, 1000);
  const session = e.win.Runner.beginExperiment(104729);
  session.restore();
  assert.equal(e.runner.resizeTimerId_, null);
});

test('pending native intro is rejected before acquiring control or altering objects', async () => {
  const e = environment(true), original = e.runner.tRex;
  e.runner.playingIntro = true;
  const owners = [];
  e.win.addEventListener('dino-controller-change', event => owners.push(event.detail.owner));
  await assert.rejects(e.start(), /opening animation/);
  assert.throws(() => e.win.Runner.beginExperiment(104729), /opening animation/);
  assert.equal(e.runner.tRex, original); assert.equal(e.runner.highestScore,12345);
  assert.equal(e.api.active,false); assert.deepEqual(owners,[]);
});
