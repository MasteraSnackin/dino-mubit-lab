/* Local Mubit controls. Provider responses are data, never executable code. */
(() => {
  'use strict';
  const ACTIONS = new Set(['jump', 'duck', 'wait']);
  const STEP_MS = 1000 / 60;
  const state = { status: null, generation: 0, active: null, lastRun: null,
    pending: null, reviewing: false, checking: false, externalOwner: null, phase: 'Ready for normal play' };
  let ui;

  function externalControllerActive() {
    return Boolean(state.externalOwner || window.DinoLocalPlayer?.active);
  }
  function busy() {
    return Boolean(state.active || state.reviewing || state.checking || externalControllerActive());
  }
  function statusSnapshot() {
    return state.status ? { ...state.status, models: [...(state.status.models || [])] } : null;
  }

  function element(tag, text, className) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
  }
  function button(label, handler) {
    const node = element('button', label);
    node.type = 'button';
    node.addEventListener('click', handler);
    return node;
  }
  function paint() {
    const status = state.status;
    ui.connection.textContent = status?.message || 'Reading local connection settings…';
    ui.model.textContent = status?.model ? `Model: ${status.model}` : 'No model selected';
    ui.phase.textContent = state.phase;
    const remaining = status?.callsRemaining;
    ui.budget.textContent = Number.isInteger(remaining)
      ? `${remaining} model calls left in this server session.` : '';
    const isBusy = busy();
    ui.check.disabled = isBusy || !status?.configured;
    ui.start.disabled = isBusy || !status?.ready || !status?.verified || (Number.isInteger(remaining) && remaining <= 1);
    ui.stop.disabled = !state.active && !state.reviewing;
    ui.review.disabled = isBusy || !state.lastRun || !status?.ready || !status?.verified || remaining === 0;
    ui.mode.disabled = isBusy;
    ui.key.disabled = isBusy;
    ui.saveKey.disabled = isBusy || !ui.key.value.trim();
    ui.modeNote.textContent = ui.mode.value === 'paused'
      ? 'Paused AI mode: the game waits for each model reply. Its score is separate from normal play.'
      : 'Normal-speed AI mode: the game keeps moving during requests. Late replies may miss obstacles. AI scores stay separate.';
    ui.setup.hidden = Boolean(status?.configured);
    if (state.active) {
      ui.score.textContent = `AI score ${displayScore(state.active.runner)} · ${state.active.steps} decisions`;
    } else if (state.lastRun) {
      ui.score.textContent = `Last AI score ${state.lastRun.score} · ${state.lastRun.steps} decisions · ${state.lastRun.mode === 'paused' ? 'paused mode' : 'normal speed'} · ${state.lastRun.outcome}`;
    }
    window.dispatchEvent(new CustomEvent('dino-mubit-status', {
      detail: { status: statusSnapshot(), busy: isBusy },
    }));
  }
  async function request(path, payload, signal) {
    const options = payload === undefined ? { signal } : {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload), signal,
    };
    const response = await fetch(path, options);
    const value = await response.json();
    if (!response.ok) throw new Error(value.message || 'The local connection request failed.');
    return value;
  }
  async function refreshStatus(token = state.generation) {
    try {
      const status = await request('/api/mubit/status');
      if (token !== state.generation) return;
      state.status = status;
    } catch (_) {
      if (token !== state.generation) return;
      state.status = null;
      state.phase = 'Connection unavailable';
    }
    paint();
    if (!state.status) ui.connection.textContent = 'The local connection service is unavailable. Restart the Dino server.';
  }
  async function reconcileStatus(token, requireRecheck = false) {
    // Disable actions immediately; a previous successful check may no longer be valid.
    if (state.status) state.status = { ...state.status, verified: false };
    state.checking = true;
    paint();
    try {
      await refreshStatus(token);
      if (token !== state.generation) return;
      if (requireRecheck && state.status) {
        // An aborted request may still be running, or its result was unusable.
        // A status read cannot resolve that uncertainty; require a fresh check.
        state.status = { ...state.status, verified: false,
          message: 'Check the connection again before another model request.' };
      }
    } finally {
      if (token === state.generation) { state.checking = false; paint(); }
    }
  }
  async function checkConnection() {
    if (busy()) return;
    state.checking = true;
    if (state.status) state.status = { ...state.status, verified: false };
    state.phase = 'Checking Mubit connection…';
    paint();
    try {
      state.status = await request('/api/mubit/check', {});
      state.phase = state.status.verified ? 'Connection verified' : 'Connection not verified';
    } catch (error) {
      state.phase = error.message;
    } finally {
      state.checking = false;
      paint();
    }
  }
  async function saveLocalKey(event) {
    event.preventDefault();
    if (busy() || !ui.key.value.trim()) return;
    const payload = { key: ui.key.value.trim() };
    ui.key.value = '';
    state.checking = true;
    state.phase = 'Saving the key in macOS Keychain…';
    ui.keyNotice.textContent = '';
    paint();
    try {
      const result = await request('/api/mubit/key', payload);
      if (!result.ok) throw new Error(result.message || 'The key could not be saved.');
      await refreshStatus();
      state.phase = 'Key saved locally. Check the connection next.';
      ui.keyNotice.textContent = 'Saved in macOS Keychain. The input has been cleared. No model request was sent.';
      ui.keySetup.open = false;
    } catch (error) {
      state.phase = 'Key not saved';
      ui.keyNotice.textContent = error.message;
    } finally {
      payload.key = '';
      state.checking = false;
      paint();
    }
  }
  function displayScore(runner) {
    return runner.distanceMeter.getActualDistance(Math.ceil(runner.distanceRan));
  }
  function releaseControls(runner) {
    runner.tRex.speedDrop = false;
    if (runner.tRex.ducking) runner.tRex.setDuck(false);
  }
  function clock() {
    return /iPad|iPhone|iPod/.test(navigator.platform) ? Date.now() : performance.now();
  }
  function acquireRunner(runner, mode) {
    const saved = {};
    for (const name of ['scheduleNextUpdate', 'onKeyDown', 'onKeyUp', 'play', 'playIntro']) {
      saved[name] = { own: Object.prototype.hasOwnProperty.call(runner, name), value: runner[name] };
    }
    const highScore = runner.highestScore;
    runner.stop();
    runner.scheduleNextUpdate = () => {};
    runner.onKeyDown = () => {};
    runner.onKeyUp = () => {};
    runner.play = () => {};
    // AI runs start on the normal full-width course without the one-off intro.
    runner.playIntro = () => {};
    runner.activated = true;
    runner.playingIntro = false;
    runner.tRex.playingIntro = false;
    runner.containerEl.style.webkitAnimation = '';
    runner.containerEl.style.width = `${runner.dimensions.WIDTH}px`;
    runner.highestScore = 0;
    // Establish the normal starting position before restart renders its first frame.
    runner.tRex.xPos = runner.tRex.config.START_X_POS;
    runner.restart();
    runner.stop();
    runner.setArcadeMode();
    const prompt = document.getElementById('messageBox');
    if (prompt) prompt.style.visibility = 'hidden';
    // Render the initial screenshot without advancing course time.
    runner.playing = true;
    runner.time = clock();
    runner.update();
    runner.stop();
    return {
      runner, mode, saved, highScore, animation: 0, frameWaiter: null,
      framesRemaining: 0, heldAction: 'wait', previousAction: undefined,
      runId: `dino_${crypto.randomUUID().replaceAll('-', '')}`,
      generation: ++state.generation, steps: 0, actions: [],
    };
  }
  function restoreRunner(run) {
    cancelAnimationFrame(run.animation);
    run.runner.stop();
    releaseControls(run.runner);
    run.runner.highestScore = run.highScore;
    // Return normal play to a fresh round; never carry an AI score into its high score.
    run.runner.restart();
    run.runner.stop();
    for (const [name, entry] of Object.entries(run.saved)) {
      if (entry.own) run.runner[name] = entry.value;
      else delete run.runner[name];
    }
    run.runner.updatePending = false;
  }
  async function finishRun(outcome, message) {
    const run = state.active;
    if (!run) return;
    const token = ++state.generation;
    const cancelledRequest = Boolean(state.pending);
    state.pending?.abort();
    state.pending = null;
    state.lastRun = {
      runId: run.runId, mode: run.mode, score: displayScore(run.runner),
      steps: run.steps, actions: run.actions.slice(-40), outcome,
    };
    state.active = null;
    if (run.frameWaiter) { run.frameWaiter(false); run.frameWaiter = null; }
    restoreRunner(run);
    window.dispatchEvent(new CustomEvent('dino-controller-change', { detail: { owner: null } }));
    state.phase = message;
    await reconcileStatus(token, cancelledRequest || outcome === 'failed');
  }
  function engineFrame(run) {
    if (state.active !== run || run.generation !== state.generation) return;
    if (document.hidden) {
      finishRun('stopped', 'Stopped because the game tab was hidden.');
      return;
    }
    const runner = run.runner;
    runner.playing = true;
    runner.paused = false;
    // Advance one original simulation frame. Waiting time is never inserted into physics.
    if (run.mode === 'paused') runner.time = clock() - STEP_MS;
    runner.update();
    if (runner.crashed) {
      finishRun('crashed', 'AI run ended in a collision. Review is available.');
      return;
    }
    if (run.framesRemaining > 0) {
      --run.framesRemaining;
      if (run.framesRemaining === 0) {
        releaseControls(runner);
        if (run.heldAction === 'jump') runner.tRex.endJump();
        run.heldAction = 'wait';
        if (run.frameWaiter) { run.frameWaiter(true); run.frameWaiter = null; }
      }
    }
    if (run.mode === 'realtime' || run.framesRemaining > 0) {
      run.animation = requestAnimationFrame(() => engineFrame(run));
    } else {
      runner.stop();
      run.animation = 0;
    }
  }
  function applyMove(run, action, frames) {
    releaseControls(run.runner);
    if (action === 'jump' && !run.runner.tRex.jumping) {
      run.runner.tRex.startJump(run.runner.currentSpeed);
    } else if (action === 'duck') {
      if (run.runner.tRex.jumping) run.runner.tRex.setSpeedDrop();
      else run.runner.tRex.setDuck(true);
    }
    run.heldAction = action;
    run.framesRemaining = frames;
    return new Promise(resolve => {
      run.frameWaiter = resolve;
      if (!run.animation) run.animation = requestAnimationFrame(() => engineFrame(run));
    });
  }
  function callEvidence(response) {
    const parts = [];
    if (Number.isFinite(response.latencyMs)) parts.push(`${(response.latencyMs / 1000).toFixed(2)} s`);
    if (response.requestId) parts.push(`Request ${response.requestId}`);
    ui.evidence.textContent = parts.join(' · ');
    if (state.status && Number.isInteger(response.callsRemaining)) {
      state.status.callsRemaining = response.callsRemaining;
    }
    if (state.status && response.ok && response.requestId) {
      state.status.message = 'Live response received through Mubit.';
      state.status.requestId = response.requestId;
    }
  }
  async function startRun() {
    if (busy() || !state.status?.ready || !state.status?.verified || state.status.callsRemaining <= 1) return;
    const runner = window.Runner?.instance_;
    if (!runner?.canvas || !runner.tRex) {
      state.phase = 'The game is not ready yet.';
      paint();
      return;
    }
    const ownershipRequest = new CustomEvent('dino-controller-request', {
      cancelable: true, detail: { owner: 'mubit' },
    });
    window.dispatchEvent(ownershipRequest);
    if (ownershipRequest.defaultPrevented || externalControllerActive()) {
      state.phase = 'Stop the local player before starting a Mubit run.';
      paint();
      return;
    }
    ui.reviewText.replaceChildren();
    let run;
    try { run = acquireRunner(runner, ui.mode.value); }
    catch (_) { state.phase = 'Could not start the AI run. Reload the game before trying again.'; paint(); return; }
    state.active = run;
    window.dispatchEvent(new CustomEvent('dino-controller-change', { detail: { owner: 'mubit' } }));
    if (run.mode === 'realtime') run.animation = requestAnimationFrame(() => engineFrame(run));
    paint();
    while (state.active === run && run.generation === state.generation) {
      if (Number.isInteger(state.status?.callsRemaining) && state.status.callsRemaining <= 1) {
        await finishRun('stopped', 'Request limit reached; one request reserved for review.');
        break;
      }
      state.phase = run.mode === 'paused' ? 'Paused — waiting for Mubit’s move…' : 'Running — waiting for Mubit’s move…';
      paint();
      const controller = new AbortController();
      state.pending = controller;
      try {
        const screenshot = runner.canvas.toDataURL('image/png');
        if (screenshot.length > 1048576) throw new Error('The game screenshot is too large to send.');
        const payload = { screenshot, runId: run.runId, step: run.steps, score: displayScore(runner), mode: run.mode };
        if (run.previousAction) payload.previousAction = run.previousAction;
        const result = await request('/api/mubit/decide', payload, controller.signal);
        if (state.active !== run || run.generation !== state.generation) return;
        state.pending = null;
        callEvidence(result);
        if (!result.ok) throw new Error(result.message || 'Mubit could not choose a move.');
        if (!ACTIONS.has(result.action) || !Number.isInteger(result.frames) || result.frames < 1 || result.frames > 30) {
          throw new Error('Mubit returned an invalid move. The run has stopped.');
        }
        run.steps++;
        run.previousAction = result.action;
        run.actions.push({ action: result.action, frames: result.frames, score: displayScore(runner) });
        ui.decision.textContent = `${result.action} for ${result.frames} frames${result.reason ? ` — ${result.reason}` : ''}`;
        state.phase = 'Applying Mubit’s move';
        paint();
        await applyMove(run, result.action, result.frames);
      } catch (error) {
        if (state.active !== run || run.generation !== state.generation) return;
        await finishRun('failed', error.name === 'AbortError' ? 'The request was cancelled.' : error.message);
      }
    }
  }
  function stop() {
    if (state.active) return finishRun('stopped', 'Stopped. An already-sent request may still finish and incur usage.');
    else if (state.reviewing) {
      const token = ++state.generation;
      state.pending?.abort();
      state.pending = null;
      state.reviewing = false;
      state.phase = 'Review cancelled. An already-sent request may still incur usage.';
      return reconcileStatus(token, true);
    }
  }
  function canReview() {
    return Boolean(ui && !busy() && state.status?.ready && state.status?.verified && state.status.callsRemaining !== 0);
  }
  function reviewRun() {
    if (!state.lastRun || !canReview()) return;
    const run = state.lastRun;
    return performReview('/api/mubit/review', run, run.runId, false);
  }
  function reviewSavedRun(id) {
    if (typeof id !== 'string' || !/^[A-Za-z0-9_-]{1,80}$/.test(id) || !canReview()) return;
    return performReview(`/api/runs/${encodeURIComponent(id)}/review`, {}, id, true);
  }
  async function performReview(path, payload, runId, savedLocal) {
    const token = ++state.generation;
    const controller = new AbortController();
    state.pending = controller;
    state.reviewing = true;
    const label = savedLocal
      ? `Saved local run ${runId} · Summary and actions only; no screenshots are sent.`
      : `AI run ${runId} · Summary and recorded actions.`;
    ui.reviewText.replaceChildren(element('p', label, 'mubit-meta'));
    state.phase = savedLocal ? 'Asking Mubit to review the saved local run…' : 'Asking Mubit to review the completed AI run…';
    paint();
    try {
      const result = await request(path, payload, controller.signal);
      if (token !== state.generation) return;
      if (!result || typeof result.ok !== 'boolean'
          || (result.ok && (typeof result.summary !== 'string' || result.summary.length > 1200
            || !Array.isArray(result.suggestions) || result.suggestions.length > 3
            || result.suggestions.some(text => typeof text !== 'string' || text.length > 400)))) {
        throw new Error('The local service returned an invalid review.');
      }
      callEvidence(result);
      window.dispatchEvent(new CustomEvent('dino-review-result', {
        detail: { runId, ok: result.ok, result: { ...result,
          ...(result.ok ? { suggestions: [...result.suggestions] } : {}) } },
      }));
      if (!result.ok) throw new Error(result.message || 'Mubit could not review this run.');
      ui.reviewText.replaceChildren(element('p', label, 'mubit-meta'), element('p', result.summary || 'No review summary was returned.'));
      const suggestions = Array.isArray(result.suggestions) ? result.suggestions.slice(0, 3) : [];
      if (suggestions.length) {
        const list = element('ul');
        suggestions.forEach(text => list.append(element('li', String(text))));
        ui.reviewText.append(list);
      }
      state.phase = 'Review received. This app has not applied the suggestions or requested training.';
      return result;
    } catch (error) {
      if (token !== state.generation) return;
      state.phase = error.name === 'AbortError' ? 'Review cancelled.' : error.message;
      await reconcileStatus(token, true);
    } finally {
      if (token === state.generation) {
        state.pending = null;
        state.reviewing = false;
        paint();
      }
    }
  }
  function init() {
    const panel = element('details', undefined, 'mubit-panel');
    panel.open = true;
    const summary = element('summary', 'Mubit controls');
    panel.append(summary);
    const body = element('div', undefined, 'mubit-body');
    const connection = element('p', 'Reading local connection settings…', 'mubit-connection');
    connection.setAttribute('role', 'status');
    const model = element('p', '', 'mubit-meta');
    const setup = element('p', 'Save your project inference key below, then check the connection.', 'mubit-meta');
    const keySetup = element('details', undefined, 'mubit-key-setup');
    keySetup.append(element('summary', 'Save or replace Mubit key'));
    const keyForm = element('form'); keyForm.autocomplete = 'off';
    const keyLabel = element('label', 'Mubit inference key');
    const key = element('input'); key.id = 'mubit-private-key'; key.type = 'password';
    key.autocomplete = 'off'; key.spellcheck = false; key.maxLength = 2050;
    key.placeholder = 'Paste the full secret here'; keyLabel.htmlFor = key.id;
    const keyButtons = element('div', undefined, 'mubit-buttons');
    const saveKey = element('button', 'Save key locally'); saveKey.type = 'submit';
    keyButtons.append(saveKey);
    const keyNotice = element('p', '', 'mubit-meta'); keyNotice.setAttribute('role', 'status');
    keyForm.append(keyLabel, key, element('p', 'Stored only in macOS Keychain on this Mac. Saving does not make a model call. The input clears when submitted.', 'mubit-meta'), keyButtons);
    keyForm.addEventListener('submit', saveLocalKey); key.addEventListener('input', paint);
    keySetup.append(keyForm);
    const modeLabel = element('label', 'Play mode');
    const mode = element('select');
    mode.id = 'mubit-play-mode';
    modeLabel.htmlFor = mode.id;
    const paused = element('option', 'Pause while deciding'); paused.value = 'paused';
    const realtime = element('option', 'Normal speed'); realtime.value = 'realtime';
    mode.append(realtime, paused);
    mode.value = 'realtime';
    const modeNote = element('p', '', 'mubit-meta');
    mode.addEventListener('change', paint);
    const privacy = element('p', 'Play and Review send only game screenshots and run details through Mubit to the selected model. Calls may use credit. Up to 20 requests per server session. Mubit may use recorded calls for training. This app has not verified a trained model version.', 'mubit-meta');
    const controls = element('div', undefined, 'mubit-buttons');
    const check = button('Check connection', checkConnection);
    const start = button('Play with Mubit', startRun);
    const stopButton = button('Stop', stop);
    const review = button('Review last AI run', reviewRun);
    controls.append(check, start, stopButton, review);
    const phase = element('p', '', 'mubit-phase');
    phase.setAttribute('role', 'status');
    const score = element('p', '', 'mubit-score');
    const decision = element('p', '', 'mubit-decision');
    const evidence = element('p', '', 'mubit-evidence');
    const budget = element('p', '', 'mubit-meta');
    const reviewText = element('div', undefined, 'mubit-review');
    body.append(connection, model, setup, keySetup, keyNotice, modeLabel, mode, modeNote, privacy, controls, phase, score, decision, evidence, budget, reviewText);
    panel.append(body);
    document.body.append(panel);
    ui = { panel, connection, model, setup, keySetup, key, saveKey, keyNotice, mode, modeNote, check, start, stop: stopButton, review, phase, score, decision, evidence, budget, reviewText };
    // Panel input must not reach the original game's document-level controls.
    for (const eventName of ['keydown', 'keyup', 'mousedown', 'mouseup', 'touchstart', 'touchend']) {
      panel.addEventListener(eventName, event => event.stopPropagation());
    }
    const visibility = event => {
      if (!state.active) return;
      event.stopImmediatePropagation();
      if (document.hidden) finishRun('stopped', 'Stopped because the game tab was hidden.');
    };
    document.addEventListener('visibilitychange', visibility, true);
    window.addEventListener('blur', visibility, true);
    window.addEventListener('focus', visibility, true);
    window.addEventListener('pagehide', stop);
    window.addEventListener('dino-controller-request', event => {
      if (event.detail?.owner !== 'mubit' && state.active) event.preventDefault();
    });
    window.addEventListener('dino-controller-change', event => {
      state.externalOwner = event.detail?.owner && event.detail.owner !== 'mubit' ? event.detail.owner : null;
      paint();
    });
    paint();
    refreshStatus();
  }
  window.DinoMubit = Object.freeze({
    get status() { return statusSnapshot(); },
    get busy() { return busy(); },
    refreshStatus: () => refreshStatus(),
    reviewSavedRun,
  });
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init, { once: true });
  else init();
})();
