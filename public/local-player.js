/* Normal-speed local controls and sampled evidence; no provider requests. */
(() => {
  'use strict';
  const MAX_MS = 60000, SAMPLE_MS = 1000, READ_MS = 32;
  const state = { active: null, saving: false, pending: null, lastSaved: null,
    externalOwner: null, phase: 'Ready for local play', detail: '' };
  let ui;
  const node = (tag, text, className) => {
    const el = document.createElement(tag);
    if (text !== undefined) el.textContent = text;
    if (className) el.className = className;
    return el;
  };
  const control = (label, action) => {
    const el = node('button', label); el.type = 'button';
    el.addEventListener('click', action); return el;
  };
  function score(runner) {
    return Math.max(0, Math.floor(runner.distanceMeter.getActualDistance(Math.ceil(runner.distanceRan))));
  }
  function elapsed(run) { return Math.min(MAX_MS + 1000, Math.max(0, Math.round(performance.now() - run.startedClock))); }
  function paint() {
    if (!ui) return;
    ui.phase.textContent = state.phase;
    ui.detail.textContent = state.detail;
    ui.start.disabled = Boolean(state.active || state.saving || state.pending || state.externalOwner);
    ui.stop.disabled = !state.active;
    ui.retry.hidden = !state.pending || state.saving;
    ui.download.hidden = !state.pending;
    ui.retry.disabled = state.saving;
    if (state.active) {
      ui.metrics.textContent = `Score ${score(state.active.runner)} · ${Math.floor(elapsed(state.active) / 1000)} / 60 seconds · ${state.active.record.actions.filter(a => a.action === 'jump').length} jumps`;
    } else if (state.lastSaved) {
      ui.metrics.textContent = `Saved score ${state.lastSaved.score} · ${state.lastSaved.frameCount ?? state.lastSaved.frames?.length ?? 0} replay samples`;
    } else if (state.pending) {
      ui.metrics.textContent = `Unsaved score ${state.pending.score} · ${state.pending.frames.length} replay samples`;
    } else ui.metrics.textContent = 'One run at a time · normal speed · maximum 60 seconds';
  }
  function gameEvent(keyCode, type) {
    return { keyCode, type, target: document.body, preventDefault() {} };
  }
  function recordAction(run, action, reason, tMs = elapsed(run)) {
    if (run.record.actions.length >= 1200) return;
    run.record.actions.push({ tMs, action, score: score(run.runner), reason: reason.slice(0, 200) });
    state.detail = reason;
    capture(run, false, tMs);
  }
  function image(run) {
    const ratio = run.runner.canvas.height / 150;
    const width = Math.min(600, Math.max(160, Math.round(run.runner.canvas.width / ratio)));
    if (run.buffer.width !== width) run.buffer.width = width;
    run.context.fillStyle = '#fff';
    run.context.fillRect(0, 0, run.buffer.width, 150);
    run.context.drawImage(run.runner.canvas, 0, 0, run.buffer.width, 150);
    return run.context;
  }
  function capture(run, force = false, tMs = elapsed(run)) {
    if (!force && (tMs - run.lastCapture < 450 || run.record.frames.length >= 59)) return;
    const ctx = image(run);
    const png = ctx.canvas.toDataURL('image/png');
    if (png.length > 250000) throw new Error('A replay image exceeded the local storage limit.');
    if (run.record.frames.length >= 60) run.record.frames.pop();
    run.record.frames.push({ tMs, score: score(run.runner), image: png });
    run.lastCapture = tMs;
  }
  function saveMethods(runner) {
    const saved = {};
    for (const name of ['onKeyDown', 'onKeyUp', 'play']) {
      saved[name] = { own: Object.prototype.hasOwnProperty.call(runner, name), value: runner[name] };
    }
    return saved;
  }
  function restore(run) {
    cancelAnimationFrame(run.frame);
    run.runner.stop();
    // The standard restart resets a held key and restores the user's normal score.
    run.runner.highestScore = run.normalHighScore;
    run.runner.restart();
    run.runner.stop();
    run.runner.updatePending = false;
    for (const [name, entry] of Object.entries(run.saved)) {
      if (entry.own) run.runner[name] = entry.value;
      else delete run.runner[name];
    }
    window.dispatchEvent(new CustomEvent('dino-controller-change', { detail: { owner: null } }));
  }
  async function savePending() {
    if (!state.pending || state.saving) return;
    const record = state.pending;
    state.saving = true; state.phase = 'Saving this run locally…'; paint();
    try {
      const body = JSON.stringify(record);
      if (body.length > 12 * 1024 * 1024) throw new Error('The run is too large to save automatically. Download the recovery file.');
      const response = await fetch('/api/runs', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body,
      });
      const value = await response.json();
      if (!response.ok || !value.ok) throw new Error(value.message || 'The local run could not be saved.');
      state.lastSaved = value.run || { id: record.id, score: record.score, frameCount: record.frames.length };
      state.pending = null;
      state.phase = `Run saved locally — ${record.outcome.replaceAll('_', ' ')}`;
      state.detail = 'No game data was sent to Mubit. The replay contains sampled frames.';
      window.dispatchEvent(new CustomEvent('dino-run-saved', { detail: { id: record.id, summary: state.lastSaved } }));
    } catch (error) {
      state.phase = 'Run not saved — recovery is available';
      state.detail = error.message;
    } finally { state.saving = false; paint(); }
  }
  function downloadPending() {
    if (!state.pending) return;
    const blob = new Blob([JSON.stringify(state.pending, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob), link = node('a');
    link.href = url; link.download = `dino-run-${state.pending.id}.json`;
    link.click(); setTimeout(() => URL.revokeObjectURL(url), 2000);
  }
  function finish(outcome, message) {
    const run = state.active;
    if (!run || run.ending) return;
    run.ending = true;
    const duration = elapsed(run);
    run.record.durationMs = duration;
    run.record.endedAt = new Date(run.startedDate + duration).toISOString();
    run.record.score = score(run.runner);
    run.record.outcome = outcome;
    try { capture(run, true, duration); }
    catch (_) { message += ' The final image could not be captured.'; }
    state.active = null;
    state.pending = run.record;
    state.phase = message;
    restore(run); paint();
    savePending();
  }
  function releaseJump(run, now) {
    if (run.jumpReleaseAt && now >= run.jumpReleaseAt) {
      run.nativeUp(gameEvent(38, 'keyup'));
      run.jumpReleaseAt = 0; run.held = 'run';
      recordAction(run, 'run', 'Release the jump key; continue at normal speed.');
    }
  }
  function move(run, choice, now) {
    if (choice.action === 'jump') {
      if (run.held === 'duck') run.nativeUp(gameEvent(40, 'keyup'));
      run.nativeDown(gameEvent(38, 'keydown'));
      run.held = 'jump'; run.jumpReleaseAt = now + 240;
      recordAction(run, 'jump', choice.reason);
    } else if (choice.action === 'duck' && run.held !== 'duck') {
      run.nativeDown(gameEvent(40, 'keydown'));
      run.held = 'duck';
      recordAction(run, 'duck', choice.reason);
    } else if (choice.action === 'run' && run.held === 'duck') {
      run.nativeUp(gameEvent(40, 'keyup'));
      run.held = 'run'; recordAction(run, 'run', choice.reason);
    }
  }
  function frame(run, now) {
    if (state.active !== run) return;
    try {
      if (document.hidden) return finish('hidden', 'Stopped because the game tab was hidden.');
      if (run.runner.crashed) return finish('collision', 'Run ended in a collision.');
      if (elapsed(run) >= MAX_MS) return finish('time_limit', 'The 60-second local run is complete.');
      releaseJump(run, now);
      if (now - run.lastRead >= READ_MS) {
        const ctx = image(run);
        const observation = window.DinoLocalPolicy.analyse(ctx.getImageData(0, 0, run.buffer.width, 150), run.policy);
        const choice = window.DinoLocalPolicy.decide(observation, run.policy, now);
        run.policy = choice.state; run.lastRead = now;
        move(run, choice, now);
      }
      if (elapsed(run) - run.lastCapture >= SAMPLE_MS) capture(run);
      if (now - run.lastPaint >= 150) { paint(); run.lastPaint = now; }
      run.frame = requestAnimationFrame(time => frame(run, time));
    } catch (error) { finish('error', `Local player stopped: ${error.message}`); }
  }
  function start() {
    if (state.active || state.saving || state.pending || state.externalOwner) return;
    const request = new CustomEvent('dino-controller-request', { cancelable: true, detail: { owner: 'local' } });
    if (!window.dispatchEvent(request)) {
      state.phase = 'Another controller is using the game.'; paint(); return;
    }
    const runner = window.Runner?.instance_;
    if (!runner?.canvas || !runner.tRex || !window.DinoLocalPolicy) {
      state.phase = 'The game is still loading.'; paint(); return;
    }
    const startedDate = Date.now(), startedClock = performance.now(), saved = saveMethods(runner);
    const buffer = document.createElement('canvas'); buffer.width = 600; buffer.height = 150;
    const run = {
      runner, saved, normalHighScore: runner.highestScore,
      nativeDown: saved.onKeyDown.value.bind(runner), nativeUp: saved.onKeyUp.value.bind(runner),
      buffer, context: buffer.getContext('2d', { willReadFrequently: true }),
      startedDate, startedClock, policy: window.DinoLocalPolicy.initialState(),
      frame: 0, lastRead: 0, lastPaint: 0, lastCapture: -1000, held: 'run', jumpReleaseAt: 0,
      record: { schemaVersion: 1, id: crypto.randomUUID(), controller: 'local-vision-rules', policyVersion: 'vision-v2',
        mode: 'realtime', startedAt: new Date(startedDate).toISOString(), endedAt: new Date(startedDate).toISOString(),
        score: 0, durationMs: 0, outcome: 'stopped', actions: [], frames: [] },
    };
    state.active = run;
    state.phase = 'Local player running — normal speed';
    state.detail = 'Reading game pixels locally. Mubit is not involved.';
    window.dispatchEvent(new CustomEvent('dino-controller-change', { detail: { owner: 'local' } }));
    try {
      runner.stop();
      runner.highestScore = 0;
      runner.onKeyDown = () => {};
      runner.onKeyUp = () => {};
      runner.play = () => {};
      // Use the normal configured starting position. The native intro rounds
      // its per-frame movement to zero on some high-refresh displays.
      runner.tRex.xPos = runner.tRex.config.START_X_POS;
      runner.restart();
      const prompt = document.getElementById('messageBox');
      if (prompt) prompt.style.visibility = 'hidden';
      capture(run, true, 0);
      // Start with the game's usual first jump/intro; all later moves use pixels.
      run.nativeDown(gameEvent(38, 'keydown'));
      run.held = 'jump'; run.jumpReleaseAt = performance.now() + 240;
      run.policy.lastJumpAt = performance.now();
      recordAction(run, 'jump', 'Start the run using the ordinary jump control.', 0);
      run.frame = requestAnimationFrame(time => frame(run, time));
      paint();
    } catch (error) { finish('error', `Local player could not start: ${error.message}`); }
  }
  function init() {
    const panel = node('section', undefined, 'local-player-panel'); panel.id = 'local-player-panel';
    const title = node('h2', 'Local player');
    const badge = node('p', 'Rule-based · game pixels only · no Mubit calls', 'local-player-label');
    const intro = node('p', 'Runs for up to 60 seconds at normal speed and saves screenshots and move timings for sampled replay.', 'local-player-copy');
    const buttons = node('div', undefined, 'local-player-buttons');
    const startButton = control('Start local run', start);
    const stop = control('Stop local run', () => finish('stopped', 'You stopped the local run.'));
    const retry = control('Retry save', savePending);
    const download = control('Download unsaved run', downloadPending);
    buttons.append(startButton, stop, retry, download);
    const phase = node('p', '', 'local-player-phase'); phase.setAttribute('role', 'status');
    const metrics = node('p', '', 'local-player-metrics');
    const detail = node('p', '', 'local-player-copy');
    panel.append(title, badge, intro, buttons, phase, metrics, detail);
    document.body.append(panel);
    ui = { panel, start: startButton, stop, retry, download, phase, metrics, detail };
    for (const type of ['keydown', 'keyup', 'mousedown', 'mouseup', 'touchstart', 'touchend']) {
      panel.addEventListener(type, event => event.stopPropagation());
    }
    window.addEventListener('dino-controller-request', event => {
      if (state.active && event.detail?.owner !== 'local') event.preventDefault();
    });
    window.addEventListener('dino-controller-change', event => {
      state.externalOwner = event.detail?.owner && event.detail.owner !== 'local' ? event.detail.owner : null;
      paint();
    });
    const visibility = event => {
      if (!state.active) return;
      event.stopImmediatePropagation();
      if (document.hidden) finish('hidden', 'Stopped because the game tab was hidden.');
    };
    document.addEventListener('visibilitychange', visibility, true);
    window.addEventListener('blur', visibility, true);
    window.addEventListener('focus', visibility, true);
    window.addEventListener('pagehide', () => {
      if (state.active) finish('hidden', 'The page was closed before this run finished.');
    });
    window.DinoLocalPlayer = Object.freeze({ get active() { return Boolean(state.active); },
      stop() { finish('stopped', 'The local run was stopped.'); } });
    paint();
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init, { once: true });
  else init();
})();
