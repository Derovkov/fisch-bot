/* Misc tab: Lullaby buffs -- which of the Lullaby's modes (buffs) to grind, and
 * for how long each before switching to the next. Saved in the general config
 * (fischbot_general.json via save_general, key "lullaby"), NOT in the hot-swap
 * setups. Python sets the mode with the buttons on the Lullaby's card in the
 * Equipment Bag between casts (fischlullaby.LullabyBuffs); live status comes
 * from get_state().lullaby. Uses $, esc, S, META and running from index.html. */
(function () {
  let L = null, live = null, liveRod = null, saveTimer = null, busy = false;
  const MIN = 1, MAX = 600, MAX_STEPS = 12;
  // the buttons' colours on the card, top to bottom (the user's screenshot)
  const DOT = ["#e3c21a", "#5fb8c8", "#e0702a", "#7cc552", "#f0d0c4", "#e6a2e0"];
  const modes = () => META?.lullaby_modes || [];
  const modeAt = name => modes().findIndex(m => m.name === name);
  const dot = i => `<span class="m-dot" style="background:${DOT[i] || "var(--faint)"};color:${DOT[i] || "transparent"}"></span>`;
  const mins = m => m >= 60 ? `${Math.floor(m / 60)}h${m % 60 ? " " + Math.round(m % 60) + "m" : ""}` : `${m}m`;
  const mmss = s => { s = Math.max(0, Math.round(s)); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`; };

  async function init() {
    const g = await pywebview.api.get_general();
    L = g.ok && g.general.lullaby ? g.general.lullaby : {enabled: false, steps: []};
    paint();
  }

  function save() {
    clearTimeout(saveTimer);
    $("#m-saved").textContent = "Saving…";
    saveTimer = setTimeout(async () => {
      const r = await pywebview.api.save_general(JSON.stringify({lullaby: L}));
      if (r.ok) { L = r.general.lullaby; $("#m-saved").textContent = "Saved" + (running ? " -- the running bot follows the new schedule now" : ""); }
      else $("#m-saved").innerHTML = `<span style="color:var(--red)">Not saved: ${esc(r.error)}</span>`;
      paintSteps();
    }, 350);
  }

  function paintSteps() {
    const cur = running && live ? live.index : -1;
    $("#m-lb-steps").innerHTML = L.steps.length ? L.steps.map((st, i) => {
      const k = modeAt(st.mode), m = modes()[k] || {};
      return `<div class="m-step${i === cur ? " cur" : ""}" data-i="${i}"><span class="u-rank">${i + 1}</span>${dot(k)}
        <div><b>${esc(m.buff || st.mode)}</b> <span class="subtle">· ${esc(st.mode)} · button ${k + 1}</span><small>${esc(m.effect || "")}</small></div>
        <label>${L.steps.length > 1 ? `<input type="number" class="m-min" min="${MIN}" max="${MAX}" step="1" value="${st.minutes}"> min` : '<span class="subtle">stays on</span>'}</label>
        <div class="u-move"><button class="u-up" title="Earlier" ${i ? "" : "disabled"}>↑</button><button class="u-down" title="Later" ${i < L.steps.length - 1 ? "" : "disabled"}>↓</button></div>
        <button class="u-x" title="Remove">✕</button></div>`;
    }).join("") : '<div class="subtle u-empty">No buffs yet. Add the one to grind below -- or several, to switch between them on a timer.</div>';
    const total = L.steps.reduce((t, st) => t + st.minutes, 0);
    $("#m-lb-total").textContent = L.steps.length > 1 ? `${L.steps.length} buffs · one round takes ${mins(total)}` : "";
    $("#m-lb-modes").innerHTML = modes().map((m, i) =>
      `<button data-mode="${esc(m.name)}" title="${esc(m.name + ": " + m.effect)}" ${L.steps.length >= MAX_STEPS ? "disabled" : ""}>${dot(i)}${esc(m.buff)}</button>`).join("");
  }

  function paintNow() {
    const rod = (running && liveRod) || S.rod;
    $("#m-lb-rod").textContent = L.enabled && L.steps.length && rod !== "Lullaby"
      ? `Your rod is ${rod} -- equip the Lullaby (Rods page or a saved setup) for this to run.` : "";
    const on = L.enabled && L.steps.length > 0;
    $("#m-lb-now").hidden = !on;
    if (!on) return;
    let status, left = "";
    if (!running || !live) status = `Your next run starts by setting ${modes()[modeAt(L.steps[0].mode)]?.buff || L.steps[0].mode}.`;
    else {
      status = live.status || "waiting for the first cast boundary";
      left = [live.left_s != null ? `${mmss(live.left_s)} left` : "",
              live.switches ? `${live.switches} switch${live.switches === 1 ? "" : "es"} this run` : ""].filter(Boolean).join(" · ");
    }
    $("#m-lb-status").textContent = status;
    $("#m-lb-left").textContent = left;
    $("#m-lb-next").hidden = !(running && live && L.steps.length > 1);
    $("#m-lb-next").disabled = busy;
  }

  function paint() {
    if (!L) return;
    $("#m-lb-enabled").checked = L.enabled;
    paintSteps(); paintNow();
  }

  /* ---------- events ---------- */
  $("#m-lb-enabled").onchange = e => { L.enabled = e.target.checked; paintNow(); save(); };
  $("#m-lb-modes").addEventListener("click", e => {
    const b = e.target.closest("[data-mode]"); if (!b || L.steps.length >= MAX_STEPS) return;
    L.steps.push({mode: b.dataset.mode, minutes: L.steps.length ? L.steps[L.steps.length - 1].minutes : 15});
    paint(); save();
  });
  $("#m-lb-steps").addEventListener("click", e => {
    const row = e.target.closest(".m-step"); if (!row) return;
    const i = +row.dataset.i, A = L.steps;
    if (e.target.closest(".u-x")) A.splice(i, 1);
    else if (e.target.closest(".u-up") && i > 0) [A[i - 1], A[i]] = [A[i], A[i - 1]];
    else if (e.target.closest(".u-down") && i < A.length - 1) [A[i + 1], A[i]] = [A[i], A[i + 1]];
    else return;
    paint(); save();
  });
  $("#m-lb-steps").addEventListener("change", e => {
    const row = e.target.closest(".m-step"); if (!row || !e.target.classList.contains("m-min")) return;
    const v = Math.min(MAX, Math.max(MIN, Math.round(+e.target.value || MIN)));
    L.steps[+row.dataset.i].minutes = v; e.target.value = v; paintSteps(); save();
  });
  $("#m-lb-next").onclick = async () => {
    if (busy) return;
    busy = true; paintNow();
    try {
      const r = await pywebview.api.lullaby_next();
      $("#m-saved").textContent = r.ok ? "Switching to the next buff after this cast." : r.error;
    } finally { busy = false; paintNow(); }
  };

  window.paintMiscLive = (v, rod) => {
    // the list only repaints when the current step moves (left_s ticks every poll)
    const changed = (v ? v.index : null) !== (live ? live.index : null);
    live = v; liveRod = rod || null;
    if (!L || !document.querySelector("#v-misc.on")) return;
    if (changed && !document.activeElement?.closest?.("#v-misc input")) paint();
    else paintNow();
  };
  document.querySelector('[data-view="misc"]').addEventListener("click", () => L ? paint() : init());
  window.addEventListener("pywebviewready", init);
})();
