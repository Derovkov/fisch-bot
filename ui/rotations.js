/* Quick switch > Rotations: fish a set time with each rod, then switch to the
 * next (Python does the timing and switching between casts: Api.queue_rotation,
 * get_state().rotation). Uses $, esc, S, META, running, show, saveSettings,
 * paintSettings, configState, makePicker from the other scripts. */
(function () {
  let rotations = [], kind = "setups", editing = null, steps = [], base = null, busy = false, listKey = "";
  const MIN = 1, MAX = 600;
  const mmss = s => { s = Math.max(0, Math.round(s)); const h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60), x = s % 60;
    return h ? `${h}h ${String(m).padStart(2, "0")}m` : `${m}:${String(x).padStart(2, "0")}`; };
  const mins = m => m >= 60 ? `${Math.floor(m / 60)}h${m % 60 ? " " + Math.round(m % 60) + "m" : ""}` : `${m}m`;
  const summary = r => r.steps.map(st => `${st.rod} ${mins(st.minutes)}`).join(" → ");
  const find = id => rotations.find(r => r.id === id);
  window.rotationName = id => find(id)?.name;

  /* the Quick switch header box while a rotation is selected or running */
  window.rotationStatus = live => {
    if (live) {
      const cur = live.steps[live.index];
      return {status: "Rotation running", name: live.name,
              detail: `${cur.rod} · ${mmss(live.left_s)} left · then ${live.next_rod}`};
    }
    const r = !running && S.active_rotation && find(S.active_rotation);
    return r ? {status: "Selected rotation", name: r.name, detail: summary(r)} : null;
  };

  function setKind(k) {
    kind = k;
    document.querySelectorAll("#qs-kind button").forEach(b => b.classList.toggle("on", b.dataset.v === k));
    $("#config-list").hidden = k !== "setups"; $("#config-save-new").hidden = k !== "setups";
    $("#rot-list").hidden = k !== "rotations"; $("#rot-new").hidden = k !== "rotations";
    $("#qs-help").dataset.help = k === "rotations" ? "rotations" : "profiles";
    listKey = ""; paintList();
  }

  function paintList() {
    if (kind !== "rotations") return;
    const live = running ? configState.rotation : null;
    const key = JSON.stringify([rotations, S.active_rotation, live?.id, running, busy]);
    if (key === listKey) return;
    listKey = key;
    $("#rot-list").innerHTML = rotations.length ? rotations.map(r => {
      const on = live ? live.id === r.id : S.active_rotation === r.id;
      const label = on ? (running ? "Stop rotating" : "Turn off") : running ? "Switch" : "Use";
      return `<div class="config-row${on ? " active" : ""}"><div class="details"><b>${esc(r.name)}</b>`
        + `<small>${esc(summary(r))}</small></div>`
        + `<button class="mini-button" data-rot-use="${esc(r.id)}" ${busy ? "disabled" : ""}>${label}</button>`
        + `<button class="edit" data-rot-edit="${esc(r.id)}" aria-label="Edit ${esc(r.name)}" title="Edit or delete">⋯</button></div>`;
    }).join("") + '<button class="config-new" data-rot-new>+ New rotation</button>'
      : '<div class="config-empty">Rotate between rods on a timer --<br>e.g. 20 min on one rod, then 10 on another.<br>'
        + '<button class="mini-button accent" data-rot-new style="margin-top:10px">+ New rotation</button></div>';
  }

  async function use(id) {
    if (busy) return;
    const r = find(id); if (!r) return;
    const live = running ? configState.rotation : null;
    busy = true; paintList();
    try {
      if (live?.id === id) { await pywebview.api.end_rotation(); return; }        // Stop rotating
      if (!running && S.active_rotation === id) { S.active_rotation = ""; return; } // Turn off
      if (running) {
        const res = await pywebview.api.queue_rotation(id);
        if (!res.ok) { $("#err").textContent = res.error; return; }
      } else if (!S.auto_equip) {
        $("#err").textContent = "Turn on 'Equip rod in game' (below) to use a rotation -- it switches rods for you.";
        return;
      }
      const first = r.steps[0];
      S = Object.assign({}, S, r.base, {rod: first.rod, active_profile: "", active_rotation: id,
        rod_enchants: Object.assign({}, S.rod_enchants, {[first.rod]: first.enchants.slice()})});
      $("#err").textContent = "";
    } finally {
      busy = false; saveSettings(); paintSettings(); listKey = ""; paintList();
    }
  }

  /* ---------- editor ---------- */
  const enchantsOf = rod => (S.rod_enchants[rod] || []).slice(0, 3);

  function open(id = "") {
    editing = find(id) || null;
    steps = editing ? editing.steps.map(st => ({...st})) : [];
    base = editing ? editing.base : null;
    $("#rot-modal-title").firstChild.textContent = editing ? "Edit rotation " : "New rotation ";
    $("#rot-name").value = editing?.name || "";
    $("#rot-save").textContent = editing ? "Update rotation" : "Create rotation";
    $("#rot-delete").hidden = !editing; $("#rot-delete").textContent = "Delete"; $("#rot-delete").dataset.confirm = "";
    $("#rot-error").textContent = "";
    paintSteps(); $("#rot-modal").classList.add("on"); $("#rot-name").focus();
  }
  function close() { $("#rot-modal").classList.remove("on"); }

  function paintSteps() {
    $("#rot-steps").innerHTML = steps.length ? steps.map((st, i) => {
      const ench = enchantsOf(st.rod);
      return `<div class="rot-step" data-i="${i}"><span class="u-rank">${i + 1}</span>
        <div class="grow"><b>[${esc(st.rod)}]</b><small>${ench.length ? esc(ench.join(", ")) : "No enchants"} · from the Rods page</small></div>
        <label><input type="number" class="rot-min" min="${MIN}" max="${MAX}" step="1" value="${st.minutes}"> min</label>
        <div class="u-move"><button class="u-up" title="Earlier" ${i ? "" : "disabled"}>↑</button><button class="u-down" title="Later" ${i < steps.length - 1 ? "" : "disabled"}>↓</button></div>
        <button class="u-x" title="Remove">✕</button></div>`;
    }).join("") : '<div class="subtle rot-none">No rods yet -- add at least two below.</div>';
    const total = steps.reduce((t, st) => t + (+st.minutes || 0), 0);
    $("#rot-total").textContent = steps.length ? `${steps.length} rod${steps.length === 1 ? "" : "s"} · one round takes ${mins(total)}` : "";
    const b = base || S;
    $("#rot-preview").textContent = (editing ? "Shared settings: " : "Shared settings (your current ones): ")
      + `${b.max_fish ? b.max_fish + " casts per run" : "fishes until stopped"}${b.fast_cast ? " · fast cast" : ""} · lookahead ${b.lookahead}s · `
      + `deadband ${Math.round(b.deadband * 100)}% · ${b.focus === "pin" ? "keeps Roblox in front" : "pauses when you switch"}. `
      + "Each switch waits for the current cast and equips the rod through the Equipment Bag.";
    picker.refresh();
  }

  const picker = makePicker($("#rot-add"), {
    placeholder: "Add a rod…",
    items: () => {
      const own = new Set(S.owned || []);
      return (META?.rods || []).map(r => ({name: r.name, group: own.has(r.name) ? "Your rods" : "Other rods",
          sub: [r.location, (S.rod_enchants[r.name] || []).join(", ")].filter(Boolean).join(" · "),
          tag: r.name === S.rod ? "equipped" : ""}))
        .sort((a, b) => (a.group !== b.group) * (a.group === "Your rods" ? -1 : 1) || a.name.localeCompare(b.name));
    },
    onPick: rod => {
      if (steps.length >= 10) { $("#rot-error").textContent = "A rotation can have up to 10 rods."; return; }
      steps.push({rod, minutes: steps.length ? steps[steps.length - 1].minutes : 15});
      $("#rot-error").textContent = ""; paintSteps();
    },
  });

  $("#rot-steps").addEventListener("click", e => {
    const row = e.target.closest(".rot-step"); if (!row) return;
    const i = +row.dataset.i;
    if (e.target.closest(".u-x")) steps.splice(i, 1);
    else if (e.target.closest(".u-up") && i > 0) [steps[i - 1], steps[i]] = [steps[i], steps[i - 1]];
    else if (e.target.closest(".u-down") && i < steps.length - 1) [steps[i + 1], steps[i]] = [steps[i], steps[i + 1]];
    else return;
    paintSteps();
  });
  $("#rot-steps").addEventListener("change", e => {
    const row = e.target.closest(".rot-step"); if (!row || !e.target.classList.contains("rot-min")) return;
    const v = Math.min(MAX, Math.max(MIN, Math.round(+e.target.value || MIN)));
    steps[+row.dataset.i].minutes = v; e.target.value = v; paintSteps();
  });

  async function save() {
    if (busy) return;
    if (steps.length < 2) { $("#rot-error").textContent = "Add at least two rods."; return; }
    // Enchants come from the Rods page, so a rotation always uses what's on your rods now.
    const shared = base || Object.fromEntries(["max_fish", "focus", "debug", "trace", "keep", "lookahead", "deadband", "bite", "fast_cast"].map(k => [k, S[k]]));
    const data = {base: shared, steps: steps.map(st => ({rod: st.rod, minutes: st.minutes, enchants: enchantsOf(st.rod)}))};
    busy = true; $("#rot-save").disabled = true;
    try {
      const r = await pywebview.api.save_rotation($("#rot-name").value, JSON.stringify(data), editing?.id || "");
      if (!r.ok) { $("#rot-error").textContent = r.error; return; }
      rotations = r.rotations; close();
    } catch (err) { $("#rot-error").textContent = String(err); }
    finally { busy = false; $("#rot-save").disabled = false; listKey = ""; paintList(); paintSettings(); }
  }

  async function remove() {
    if (busy || !editing) return;
    const b = $("#rot-delete");
    if (!b.dataset.confirm) { b.textContent = "Delete this rotation?"; b.dataset.confirm = "yes"; return; }
    busy = true;
    try {
      const r = await pywebview.api.delete_rotation(editing.id);
      if (!r.ok) { $("#rot-error").textContent = r.error; return; }
      rotations = r.rotations;
      if (S.active_rotation === editing.id) { S.active_rotation = ""; saveSettings(); }
      close();
    } finally { busy = false; listKey = ""; paintList(); paintSettings(); }
  }

  /* ---------- wiring ---------- */
  $("#qs-kind").onclick = e => { const b = e.target.closest("button"); if (b) setKind(b.dataset.v); };
  $("#rot-new").onclick = () => open();
  $("#rot-list").onclick = e => {
    const u = e.target.closest("[data-rot-use]"), ed = e.target.closest("[data-rot-edit]");
    if (e.target.closest("[data-rot-new]")) open();
    else if (u) use(u.dataset.rotUse);
    else if (ed) open(ed.dataset.rotEdit);
  };
  $("#rot-save").onclick = save;
  $("#rot-cancel").onclick = close;
  $("#rot-delete").onclick = remove;
  $("#rot-modal").onclick = e => { if (e.target.id === "rot-modal") close(); };
  $("#rot-name").addEventListener("keydown", e => { if (e.key === "Enter") { e.preventDefault(); save(); } });
  document.addEventListener("keydown", e => { if (e.key === "Escape" && $("#rot-modal").classList.contains("on")) close(); });

  /* called from paintConfigurations (settings loaded, then every poll) */
  let loaded = false;
  window.paintRotations = () => {
    if (!loaded) {
      loaded = true;
      rotations = META.rotations || [];
      if (S.active_rotation && !find(S.active_rotation)) S.active_rotation = "";   // deleted elsewhere
      setKind(S.active_rotation ? "rotations" : "setups");
      return;
    }
    paintList();
  };
})();
