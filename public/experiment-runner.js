/* Fixed-course, normal-speed comparisons. The controller reads canvas pixels only. */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory;
  else root.DinoExperimentRunner = factory(root);
})(typeof globalThis === 'object' ? globalThis : this, function (root) {
  'use strict';
  const STEP_MS = 1000 / 60;
  const DEFAULTS = Object.freeze({ jumpLeadMs: 170, duckLeadMs: 330, duckExtraMs: 0, jumpHoldMs: 240 });
  const LIMITS = { jumpLeadMs: [100, 240], duckLeadMs: [200, 500], duckExtraMs: [0, 250], jumpHoldMs: [120, 350] };
  const doc = root.document;
  let active = null;
  let externalOwner = null;
  function emit(type, detail) { root.dispatchEvent(new root.CustomEvent(type, { detail })); }
  function score(run) {
    return Math.max(0, Math.floor(run.runner.distanceMeter.getActualDistance(Math.ceil(run.runner.distanceRan))));
  }
  function pixels(run) {
    run.context.fillStyle = '#fff';
    run.context.fillRect(0, 0, 600, 150);
    run.context.drawImage(run.runner.canvas, 0, 0, 600, 150);
    return run.context;
  }
  function image(run) {
    pixels(run);
    const png = run.buffer.toDataURL('image/png');
    if (png.length > 250000) throw new Error('The replay image is too large.');
    return { tick: run.record.ticks, tMs: Math.round(run.record.ticks * STEP_MS), score: score(run), image: png };
  }
  function sample(run) {
    const tick = run.record.ticks;
    if (tick % 6 !== 0) return;
    const frame = image(run);
    run.endingFrames.push(frame);
    if (run.endingFrames.length > 6) run.endingFrames.shift();
    if (tick % 60 === 0 && run.regularFrames.length < 54) run.regularFrames.push(frame);
  }
  function action(run, move, reason) {
    if (run.record.actions.length >= 1200) return;
    run.record.actions.push({ tick: run.record.ticks, tMs: Math.round(run.record.ticks * STEP_MS),
      action: move, score: score(run), reason: String(reason).slice(0, 200) });
  }
  function move(run, choice) {
    const now = run.record.ticks * STEP_MS;
    if (choice.action === 'jump' && run.held !== 'jump') {
      if (run.held === 'duck') run.engine.input(40, false);
      run.engine.input(38, true);
      run.held = 'jump';
      run.jumpReleaseAt = now + run.record.config.jumpHoldMs;
      action(run, 'jump', choice.reason);
    } else if (choice.action === 'duck' && run.held !== 'duck') {
      if (run.held === 'jump') run.engine.input(38, false);
      run.engine.input(40, true);
      run.held = 'duck'; run.jumpReleaseAt = null;
      action(run, 'duck', choice.reason);
    } else if (choice.action === 'run' && run.held === 'duck') {
      run.engine.input(40, false); run.held = 'run';
      action(run, 'run', choice.reason);
    }
  }
  function finish(outcome) {
    const run = active;
    if (!run || run.ending) return;
    run.ending = true;
    root.cancelAnimationFrame(run.raf);
    run.record.wallDurationMs = Math.max(0, Math.round(root.performance.now() - run.startedClock));
    run.record.endedAt = new Date(run.startedDate + run.record.wallDurationMs).toISOString();
    run.record.score = score(run);
    run.record.outcome = outcome;
    try {
      const final = image(run);
      const recent = run.endingFrames.filter(frame => frame.tick !== final.tick).slice(-5);
      const byTick = new Map([...run.regularFrames, ...recent, final].map(frame => [frame.tick, frame]));
      run.record.frames = [...byTick.values()].sort((a, b) => a.tick - b.tick).slice(-60);
      run.record.course = run.engine ? run.engine.course() : [];
    } catch (_) {
      run.record.outcome = 'error';
      run.record.frames = run.regularFrames.slice(0, 60);
      if (run.engine) run.record.course = run.engine.course();
    } finally {
      try { if (run.engine) run.engine.restore(); }
      catch (_) { run.record.outcome = 'error'; }
      active = null;
      emit('dino-controller-change', { owner: null });
      emit('dino-experiment-progress', { slot: run.record.slot, ticks: run.record.ticks, score: run.record.score });
      run.resolve(run.record);
    }
  }
  function wrongSize(run) {
    return run.runner.dimensions.WIDTH !== 600 || run.runner.canvas.width !== run.canvasWidth ||
      run.runner.canvas.height !== run.canvasHeight || root.devicePixelRatio !== run.pixelRatio;
  }
  function frame(run, now) {
    if (active !== run) return;
    try {
      if (doc.hidden || doc.visibilityState === 'hidden') return finish('hidden');
      if (wrongSize(run)) return finish('resized');
      const gap = Math.max(0, now - run.lastFrame);
      run.lastFrame = now;
      run.record.maxFrameGapMs = Math.max(run.record.maxFrameGapMs, gap);
      if (gap > 250) return finish('lagged');
      // Wall time sets the number of elapsed native 60 Hz ticks. A 120 Hz display
      // cannot accelerate physics, and short stalls cannot silently slow it down.
      const due = Math.min(run.record.maxTicks, Math.floor((now - run.startedClock) / STEP_MS + 1e-7));
      while (run.record.ticks < due) {
        const clock = run.record.ticks * STEP_MS;
        if (run.jumpReleaseAt !== null && clock + 1e-7 >= run.jumpReleaseAt) {
          run.engine.input(38, false); run.jumpReleaseAt = null; run.held = 'run';
          action(run, 'run', 'Release the jump key; continue at normal speed.');
        }
        if (run.record.ticks % 2 === 0) {
          const view = root.DinoLocalPolicy.analyse(pixels(run).getImageData(0, 0, 600, 150), run.policy);
          const choice = root.DinoLocalPolicy.decide(view, run.policy, clock, run.record.config);
          run.policy = choice.state;
          move(run, choice);
        }
        run.engine.step();
        run.record.ticks++;
        if (run.runner.crashed) return finish('collision');
        sample(run);
        if (run.record.ticks % 60 === 0) emit('dino-experiment-progress', {
          slot: run.record.slot, ticks: run.record.ticks, score: score(run)
        });
      }
      if (run.record.ticks >= run.record.maxTicks) return finish('time_limit');
      run.raf = root.requestAnimationFrame(time => frame(run, time));
    } catch (_) { finish('error'); }
  }
  function validate(spec) {
    if (!spec || typeof spec.slot !== 'string' || typeof spec.runId !== 'string' || !spec.slot || !spec.runId) {
      throw new Error('The comparison needs a slot and run ID.');
    }
    if (!Number.isInteger(spec.seed) || spec.seed < 0 || spec.seed > 0xffffffff) throw new Error('Invalid course seed.');
    if (!Number.isInteger(spec.maxTicks) || spec.maxTicks < 1 || spec.maxTicks > 3600) throw new Error('Invalid comparison duration.');
    const config = { ...DEFAULTS, ...spec.config };
    if (Object.keys(config).some(key => !Object.hasOwn(LIMITS, key))) throw new Error('Unknown timing setting.');
    for (const [key, [min, max]] of Object.entries(LIMITS)) {
      if (!Number.isInteger(config[key]) || config[key] < min || config[key] > max || config[key] % 10 !== 0) {
        throw new Error(`Invalid ${key} setting.`);
      }
    }
    return config;
  }
  async function run(spec) {
    const config = validate(spec);
    if (active || externalOwner) throw new Error('Another controller is using the game.');
    const runner = root.Runner?.instance_;
    if (!runner?.canvas || !root.Runner.beginExperiment || !root.DinoLocalPolicy) throw new Error('The game is still loading.');
    if (runner.playingIntro || runner.tRex?.playingIntro) throw new Error('Let the opening animation finish, then start the comparison.');
    const request = new root.CustomEvent('dino-controller-request', { cancelable: true, detail: { owner: 'experiment' } });
    if (!root.dispatchEvent(request)) throw new Error('Another controller is using the game.');
    const buffer = doc.createElement('canvas'); buffer.width = 600; buffer.height = 150;
    const startedDate = Date.now(), startedClock = root.performance.now();
    return new Promise(resolve => {
      const trial = {
        runner, engine: null, buffer, context: buffer.getContext('2d', { willReadFrequently: true }),
        policy: root.DinoLocalPolicy.initialState(), held: 'run', jumpReleaseAt: null,
        regularFrames: [], endingFrames: [], raf: 0, resolve, startedDate, startedClock, lastFrame: startedClock,
        canvasWidth: runner.canvas.width, canvasHeight: runner.canvas.height, pixelRatio: root.devicePixelRatio,
        record: { slot: spec.slot, runId: spec.runId, protocol: 'native60-v1', seed: spec.seed,
          maxTicks: spec.maxTicks, config, startedAt: new Date(startedDate).toISOString(),
          endedAt: new Date(startedDate).toISOString(), wallDurationMs: 0, ticks: 0, score: 0,
          outcome: 'stopped', canvasWidth: 600, maxFrameGapMs: 0, actions: [], frames: [], course: [] }
      };
      active = trial;
      emit('dino-controller-change', { owner: 'experiment' });
      try {
        if (doc.hidden || doc.visibilityState === 'hidden') return finish('hidden');
        if (runner.dimensions.WIDTH !== 600) return finish('resized');
        trial.engine = root.Runner.beginExperiment(spec.seed);
        action(trial, 'run', 'Begin on the ground at the normal starting position.');
        sample(trial);
        // Initialisation and the first image are outside the timed gameplay.
        trial.startedClock = root.performance.now(); trial.lastFrame = trial.startedClock;
        trial.startedDate = Date.now(); trial.record.startedAt = new Date(trial.startedDate).toISOString();
        trial.raf = root.requestAnimationFrame(time => frame(trial, time));
      } catch (_) { finish('error'); }
    });
  }
  root.addEventListener('dino-controller-request', event => {
    if (active && event.detail?.owner !== 'experiment') event.preventDefault();
  });
  root.addEventListener('dino-controller-change', event => {
    externalOwner = event.detail?.owner && event.detail.owner !== 'experiment' ? event.detail.owner : null;
  });
  const visibility = event => {
    if (!active) return;
    event.stopImmediatePropagation();
    if (doc.hidden || doc.visibilityState === 'hidden') finish('hidden');
  };
  doc.addEventListener('visibilitychange', visibility, true);
  root.addEventListener('blur', visibility, true);
  root.addEventListener('focus', visibility, true);
  root.addEventListener('pagehide', () => { if (active) finish('hidden'); });
  root.addEventListener('resize', () => { if (active) finish('resized'); }, true);
  return Object.freeze({ get active() { return Boolean(active); }, run, stop() { finish('stopped'); } });
});
