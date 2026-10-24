/* Saved evidence stays local; only an explicit review button may send a summary. */
(() => {
  'use strict';
  let ui, selected = null, selectionRevision = 0, listingRevision = 0, replay = 0;
  const reviews = new Map();
  const el = (tag, text, cls) => { const n = document.createElement(tag); if (text != null) n.textContent = text; if (cls) n.className = cls; return n; };
  const button = (label, fn) => { const n = el('button', label); n.type = 'button'; n.addEventListener('click', fn); return n; };
  const seconds = ms => `${(ms / 1000).toFixed(1)}s`;
  function stopReplay() { cancelAnimationFrame(replay); replay = 0; if (ui) ui.play.textContent = 'Play sampled replay'; }
  function renderFrame(index) {
    if (!selected?.frames.length) return;
    const frame = selected.frames[index];
    ui.image.src = frame.image;
    ui.image.alt = `Saved local run at ${seconds(frame.tMs)}, score ${frame.score}`;
    ui.seek.value = String(index);
    ui.frameLabel.textContent = `${seconds(frame.tMs)} / ${seconds(selected.durationMs)} · score ${frame.score} · image ${index + 1}/${selected.frames.length}`;
  }
  function toggleReplay() {
    if (replay) { stopReplay(); return; }
    if (!selected?.frames.length) return;
    let index = Number(ui.seek.value);
    if (index === selected.frames.length - 1) index = 0;
    const frames = selected.frames, base = frames[index].tMs, started = performance.now(), revision = selectionRevision;
    ui.play.textContent = 'Pause replay';
    const tick = now => {
      if (revision !== selectionRevision) { stopReplay(); return; }
      while (index + 1 < frames.length && frames[index + 1].tMs <= base + now - started) index++;
      renderFrame(index);
      if (index === frames.length - 1) { stopReplay(); return; }
      replay = requestAnimationFrame(tick);
    };
    replay = requestAnimationFrame(tick);
  }
  function updateReviewButton() {
    if (!ui) return;
    const bridge = window.DinoMubit, status = bridge?.status;
    const ready = status?.ready && status?.verified, exhausted = ready && status.callsRemaining <= 0;
    ui.review.disabled = !selected || !ready || exhausted || bridge?.busy || window.DinoLocalPlayer?.active;
    ui.reviewHint.textContent = exhausted
      ? 'This server session has reached its Mubit request limit. Your saved runs remain available locally.'
      : ready
      ? 'Review sends this saved run’s summary and last 40 action events. Screenshots stay local. No review is sent until you click.'
      : 'Mubit review is unavailable until a key and model are connected. Saving, replay and export work locally.';
  }
  function renderReview() {
    ui.reviewOutput.replaceChildren();
    const reply = selected && reviews.get(selected.id);
    if (!reply) return;
    if (!reply.ok) { ui.reviewOutput.append(el('p', reply.result?.message || 'Review did not complete.', 'lab-error')); return; }
    ui.reviewOutput.append(el('p', reply.result.summary));
    const list = el('ul');
    (reply.result.suggestions || []).forEach(item => list.append(el('li', item)));
    ui.reviewOutput.append(list, el('p', `Review for ${selected.id}. Suggestions have not changed the player.`, 'lab-muted'));
  }
  async function selectRun(id) {
    stopReplay();
    const revision = ++selectionRevision;
    selected = null;
    ui.detail.hidden = true;
    ui.message.classList.remove('lab-error');
    ui.message.textContent = 'Loading saved run…';
    updateReviewButton();
    try {
      const response = await fetch(`/api/runs/${encodeURIComponent(id)}`);
      const data = await response.json();
      if (revision !== selectionRevision) return;
      if (!data.ok) throw new Error(data.message || 'Saved run unavailable.');
      selected = data.run;
      ui.message.textContent = '';
      ui.detail.hidden = false;
      ui.detailTitle.textContent = `Score ${selected.score} · ${seconds(selected.durationMs)} · ${selected.outcome.replaceAll('_', ' ')}`;
      ui.source.textContent = `${selected.policyVersion} · local rules · normal speed · ${selected.actions.length} actions · ${selected.frames.length} sampled images`;
      ui.seek.max = String(Math.max(0, selected.frames.length - 1));
      ui.seek.disabled = !selected.frames.length;
      ui.play.disabled = !selected.frames.length;
      ui.image.hidden = !selected.frames.length;
      if (!selected.frames.length) ui.image.removeAttribute('src');
      ui.frameLabel.textContent = selected.frames.length ? '' : 'No sampled images were captured.';
      if (selected.frames.length) renderFrame(0);
      ui.download.href = `/api/runs/${encodeURIComponent(id)}/export`;
      ui.actions.replaceChildren();
      for (const action of selected.actions.slice(-60)) ui.actions.append(el('li', `${seconds(action.tMs)} · ${action.action} · score ${action.score} — ${action.reason}`));
      for (const row of ui.tbody.children) row.setAttribute('aria-selected', String(row.dataset.id === id));
      renderReview(); updateReviewButton();
    } catch (error) {
      if (revision !== selectionRevision) return;
      ui.message.textContent = error.message;
      ui.message.classList.add('lab-error');
    }
  }
  async function loadRuns(selectId) {
    const revision = ++listingRevision;
    ui.refresh.disabled = true;
    try {
      const response = await fetch('/api/runs');
      const data = await response.json();
      if (revision !== listingRevision) return;
      if (!data.ok) throw new Error(data.message || 'Run history could not be read.');
      ui.tbody.replaceChildren();
      for (const run of data.runs) {
        const row = el('tr'); row.dataset.id = run.id;
        const first = el('td'); first.append(button(`Open ${run.score}`, () => selectRun(run.id)));
        const when = new Date(run.startedAt).toLocaleTimeString('en-GB', {hour:'2-digit',minute:'2-digit'});
        row.append(first, el('td', seconds(run.durationMs)), el('td', run.outcome.replaceAll('_', ' ')), el('td', when));
        row.setAttribute('aria-selected', String(selected?.id === run.id));
        ui.tbody.append(row);
      }
      const best = data.runs.reduce((value, run) => Math.max(value, run.score), 0);
      ui.stats.textContent = data.runs.length ? `${data.runs.length} saved runs · highest recorded score ${best}` : 'No saved runs yet. Start a local run to collect one.';
      ui.warning.textContent = data.unavailable?.length ? `${data.unavailable.length} saved record(s) could not be read. They have been preserved.` : '';
      if (selectId) await selectRun(selectId);
      else if (!selected && data.runs.length) await selectRun(data.runs[0].id);
    } catch (error) {
      if (revision === listingRevision) ui.warning.textContent = error.message;
    } finally { if (revision === listingRevision) ui.refresh.disabled = false; }
  }
  function init() {
    const header = el('header', null, 'lab-header');
    const title = el('div'); title.append(el('h1', 'Dino · Local lab'), el('p', 'Play at normal speed. Keep the evidence.'));
    header.append(title, el('span', 'Local player available', 'lab-badge'));
    const stage = el('section'); stage.id = 'game-stage'; stage.setAttribute('aria-label', 'Dinosaur game');
    stage.append(document.getElementById('messageBox'), document.getElementById('main-frame-error'), document.getElementById('keyboard-help'));
    const controls = el('div', null, 'lab-controls'), sidebar = el('div', null, 'lab-sidebar');
    const localPanel = document.getElementById('local-player-panel'), mubit = document.querySelector('.mubit-panel');
    if (localPanel) sidebar.append(localPanel);
    if (mubit) { mubit.open = false; sidebar.append(mubit); }
    const panel = el('section', null, 'runs-panel'); panel.setAttribute('aria-label','Saved local runs');
    const head = el('div', null, 'runs-head'), refresh = button('Refresh runs', () => loadRuns());
    head.append(el('h2','Saved runs'), refresh);
    const stats = el('p', 'Reading local history…', 'lab-muted'), warning = el('p', '', 'lab-error');
    const table = el('table', null, 'runs-list'), thead = el('thead'), tr = el('tr');
    ['Score','Duration','Result','Time'].forEach(label => tr.append(el('th', label))); thead.append(tr);
    const tbody = el('tbody'); table.append(thead,tbody); const scroll = el('div',null,'runs-scroll'); scroll.append(table);
    const message = el('p','', 'lab-muted'), detail = el('div'); detail.hidden = true;
    const detailTitle = el('h3'), source = el('p','', 'lab-muted'), image = el('img',null,'replay-image');
    const frameLabel = el('p','', 'lab-muted'), replayControls = el('div',null,'replay-controls');
    const play = button('Play sampled replay', toggleReplay), seek = el('input'); seek.type='range'; seek.min='0'; seek.step='1'; seek.value='0'; seek.setAttribute('aria-label','Replay image');
    seek.addEventListener('input', () => { stopReplay(); renderFrame(Number(seek.value)); });
    replayControls.append(play, seek); const actions = el('ol',null,'run-actions');
    const actionsDetails = el('details'); actionsDetails.append(el('summary','Action log (latest 60)'),actions);
    const download = el('a','Export run JSON','lab-button'); download.setAttribute('download','');
    const review = button('Review selected run with Mubit', () => {
      if (!selected || review.disabled) return;
      window.DinoMubit.reviewSavedRun(selected.id);
    });
    const buttons = el('div',null,'replay-controls'); buttons.append(download,review);
    const reviewHint = el('p','', 'lab-muted'), reviewOutput = el('div',null,'run-review');
    detail.append(detailTitle,source,image,frameLabel,replayControls,el('p','Sampled screenshots follow their recorded timings; this is not a continuous video.','lab-muted'),actionsDetails,buttons,reviewHint,reviewOutput);
    panel.append(head,stats,warning,scroll,message,detail,el('p','Obstacle sequences are random. These scores are observations, not a controlled benchmark.','lab-muted'));
    controls.append(sidebar,panel); document.body.prepend(header,stage,controls);
    ui = { panel,refresh,stats,warning,tbody,message,detail,detailTitle,source,image,frameLabel,play,seek,actions,download,review,reviewHint,reviewOutput };
    controls.addEventListener('keydown', event => event.stopPropagation());
    controls.addEventListener('keyup', event => event.stopPropagation());
    controls.addEventListener('mousedown', event => event.stopPropagation());
    controls.addEventListener('mouseup', event => event.stopPropagation());
    controls.addEventListener('touchstart', event => event.stopPropagation());
    controls.addEventListener('touchend', event => event.stopPropagation());
    window.addEventListener('dino-run-saved', event => loadRuns(event.detail?.id || event.detail?.run?.id));
    window.addEventListener('dino-mubit-status', updateReviewButton);
    window.addEventListener('dino-controller-change', updateReviewButton);
    window.addEventListener('dino-review-result', event => { if (!event.detail?.runId) return; reviews.set(event.detail.runId,event.detail); renderReview(); updateReviewButton(); });
    document.addEventListener('visibilitychange', () => { if (document.hidden) stopReplay(); });
    updateReviewButton(); loadRuns();
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded',init,{once:true}); else init();
})();
