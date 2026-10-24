/* Pixel-only local rules. No game object/obstacle state is used here. */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.DinoLocalPolicy = api;
})(typeof globalThis === 'object' ? globalThis : this, function () {
  'use strict';
  function initialState() {
    return { speed: 360, last: null, lastAt: null, lastJumpAt: -10000, duckUntil: 0, playerAnchor: null };
  }
  function analyse(image, previous = {}) {
    const { data, width, height } = image;
    if (!data || width < 160 || height < 100 || data.length !== width * height * 4) {
      throw new Error('The game image is unavailable.');
    }
    const sx = width / Math.min(width, 600), sy = height / 150;
    const logicalWidth = Math.round(width / sx);
    const background = (Math.min(height - 1, Math.round(60 * sy)) * width + Math.round(5 * sx)) * 4;
    const darkBackground = data[background + 3] > 180 && data[background] < 100;
    function ink(x, y) {
      const px = Math.min(width - 1, Math.max(0, Math.floor(x * sx)));
      const py = Math.min(height - 1, Math.max(0, Math.floor(y * sy)));
      const i = (py * width + px) * 4;
      if (data[i + 3] < 140) return false;
      const light = (data[i] + data[i + 1] + data[i + 2]) / 3;
      return darkBackground ? light > 150 : light < 150;
    }
    // Learn the player's horizontal position from the clear opening frames.
    // Keeping that pixel anchor avoids mistaking an approaching cactus for
    // part of the dinosaur on displays where the native intro stays at x=0.
    const anchor = Number.isFinite(previous.playerAnchor) ? previous.playerAnchor : null;
    const playerStart = anchor === null ? 0 : Math.max(0, anchor - 4);
    const playerEnd = anchor === null ? 110 : Math.min(logicalWidth, anchor + 58);
    const dino = { left: playerEnd, right: 0, top: 130, bottom: 0, pixels: 0 };
    for (let x = playerStart; x < playerEnd; x++) {
      for (let y = 20; y <= 125; y++) {
        if (!ink(x, y)) continue;
        dino.left = Math.min(dino.left, x); dino.right = Math.max(dino.right, x);
        dino.top = Math.min(dino.top, y); dino.bottom = Math.max(dino.bottom, y); dino.pixels++;
      }
    }
    const groups = [];
    let group = null;
    const observedAnchor = anchor ?? (dino.pixels >= 40 ? dino.left : 50);
    for (let x = observedAnchor + 60; x < logicalWidth; x++) {
      let count = 0, top = 130, bottom = 0;
      for (let y = 45; y <= 125; y++) {
        if (ink(x, y)) { count++; top = Math.min(top, y); bottom = Math.max(bottom, y); }
      }
      if (count >= 2) {
        if (!group || x - group.right > 8) {
          if (group) groups.push(group);
          group = { left: x, right: x, top, bottom, pixels: count };
        } else {
          group.right = x; group.top = Math.min(group.top, top);
          group.bottom = Math.max(group.bottom, bottom); group.pixels += count;
        }
      }
    }
    if (group) groups.push(group);
    const obstacles = groups.filter(item => item.pixels >= 14 && item.right - item.left >= 3 && item.bottom - item.top >= 5)
      .map(item => ({ ...item, width: item.right - item.left + 1,
        kind: item.bottom >= 119 ? 'ground' : item.bottom >= 92 ? 'bird' : 'overhead' }));
    const player = dino.pixels >= 40 ? {
      ...dino, airborne: dino.bottom < 122 || dino.top < 78,
      ducking: dino.top >= 108,
    } : null;
    return { player, playerAnchor: observedAnchor, obstacles, darkBackground, width: logicalWidth, height: 150 };
  }
  function decide(observation, previous, nowMs, config = {}) {
    const jumpLeadMs = config.jumpLeadMs ?? 170;
    const duckLeadMs = config.duckLeadMs ?? 330;
    const duckExtraMs = config.duckExtraMs ?? 0;
    const next = { ...previous };
    if (observation.player) next.playerAnchor = observation.playerAnchor ?? previous.playerAnchor;
    const nearest = observation.obstacles.find(item => item.kind !== 'overhead') || null;
    if (nearest && previous.last && previous.lastAt !== null) {
      const dt = nowMs - previous.lastAt, movement = previous.last.left - nearest.left;
      if (dt > 10 && dt < 500 && movement > 0 && movement < 100 &&
          nearest.kind === previous.last.kind && Math.abs(nearest.width - previous.last.width) < 18) {
        const measured = movement / dt * 1000;
        if (measured >= 100 && measured <= 1000) next.speed = previous.speed * 0.65 + measured * 0.35;
      }
    }
    next.last = nearest; next.lastAt = nowMs;
    if (nowMs < previous.duckUntil && !observation.player?.airborne) {
      return { action: 'duck', reason: 'Hold duck until the visible bird has passed.', state: next };
    }
    if (!nearest) return { action: 'run', reason: 'No obstacle needs a move.', state: next };
    const front = observation.player?.right || 94;
    const distance = nearest.left - front;
    if (observation.player?.airborne || nowMs - previous.lastJumpAt < 260) {
      return { action: 'run', reason: 'Allow the current jump to finish.', state: next };
    }
    if (nearest.kind === 'bird' && distance <= next.speed * (duckLeadMs / 1000) + 15) {
      next.duckUntil = nowMs + Math.max(150, (distance + nearest.width + 55) / next.speed * 1000) + duckExtraMs;
      return { action: 'duck', reason: 'Duck under the visible bird.', state: next };
    }
    // Wider groups need a later take-off so the trailing cactus has passed
    // before the dinosaur descends. The jump itself stays entirely native.
    const jumpAt = next.speed * (jumpLeadMs / 1000) - Math.min(16, Math.max(0, nearest.width - 25) * 0.25);
    if (nearest.kind === 'ground' && distance <= jumpAt) {
      next.lastJumpAt = nowMs;
      return { action: 'jump', reason: 'Jump over the approaching ground obstacle.', state: next };
    }
    return { action: 'run', reason: 'The next obstacle is still outside the jump window.', state: next };
  }
  return Object.freeze({ initialState, analyse, decide });
});
