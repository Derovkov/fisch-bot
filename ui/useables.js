/* Useables tab: totems and baits the bot may use, with per-item limits.
 * Saved in the general config (fischbot_general.json via save_general), NOT in
 * the hot-swap setups. Live status comes from get_state().useables.
 * Uses $ and esc from index.html. */
(function () {
  let U = null, TOTEMS = [], BAITS = [], live = null, weather = null, saveTimer = null, totemPick = null, baitPick = null;
  const WHEN = [["start", "At start"], ["every", "Every"], ["quest", "For quests"]];
  const TOTEM_DEFAULT = {on: true, when: "every", every_min: 15, max_uses: 0, keep: 0};
  const BAIT_DEFAULT = {on: true, max_uses: 0, keep: 0};

  async function init() {
    const [g, t, b] = await Promise.all([pywebview.api.get_general(),
      pywebview.api.get_index("totems"), pywebview.api.get_index("baits")]);
    U = g.ok ? g.general.useables : {enabled: false, bait: {manage: false, when_out: "stop", list: []}, totems: []};
    TOTEMS = t.ok ? t.data.totems : [];
    BAITS = b.ok ? b.data.baits : [];
    paint();
  }

  function save() {
    clearTimeout(saveTimer);
    $("#u-saved").textContent = "Saving…";
    saveTimer = setTimeout(async () => {
      const r = await pywebview.api.save_general(JSON.stringify({useables: U}));
      if (r.ok) { U = r.general.useables; $("#u-saved").textContent = "Saved" + (running ? " -- the running bot uses the new limits now" : ""); }
      else $("#u-saved").innerHTML = `<span style="color:var(--red)">Not saved: ${esc(r.error)}</span>`;
    }, 350);
  }

  const totemInfo = n => TOTEMS.find(t => t.name === n) || {};
  const baitInfo = n => BAITS.find(b => b.name === n) || {};
  const num = (v, cls, attrs = "") => `<input type="number" class="${cls}" value="${v}" min="0" step="1" ${attrs}>`;

  function liveLine(name) {
    const it = live && live.items && live.items[name];
    if (!it) return "";
    const bits = [];
    if (it.uses) bits.push(`${it.uses} used this run`);
    if (it.count != null && !/ left/.test(it.status || "")) bits.push(`${it.count} left`);
    if (it.status) bits.push(it.status);
    return bits.length ? `<div class="u-live">${esc(bits.join(" · "))}</div>` : "";
  }

  function paintTotems() {
    totemPick?.refresh();
    $("#u-totems").innerHTML = U.totems.length ? U.totems.map((t, i) => {
      const info = totemInfo(t.name);
      return `<div class="u-item${t.on ? "" : " off"}" data-i="${i}">
        ${info.icon ? `<img src="${esc(info.icon)}" alt="">` : "<span></span>"}
        <div class="u-body">
          <div class="u-name">${esc(t.name)}${info.cooldown ? ' <span class="chip">has a cooldown</span>' : ""}${info.weather ? ` <span class="chip">${esc(info.weather)}</span>` : ""}</div>
          <div class="u-eff">${esc(info.effect || "")}</div>
          <div class="u-ctl">
            <div class="seg u-when">${WHEN.map(([v, l]) => `<button data-v="${v}" class="${t.when === v ? "on" : ""}">${l}</button>`).join("")}</div>
            ${t.when !== "start" ? `<label>${t.when === "quest" ? "at most every" : "every"} ${num(t.every_min, "u-every", 'min="1"')} min</label>` : ""}
            <label>Max per run ${num(t.max_uses, "u-max")}</label>
            <label>Keep at least ${num(t.keep, "u-keep")}</label>
          </div>
          ${liveLine(t.name)}
        </div>
        <div class="u-side"><label class="sw"><input type="checkbox" class="u-on" ${t.on ? "checked" : ""}><span></span></label>
          <button class="u-x" title="Remove">✕</button></div>
      </div>`;
    }).join("") : '<div class="subtle u-empty">No totems yet. Add one above -- it must be in your hotbar in Roblox.</div>';
  }

  function stats(b) {
    const s = [["Preferred luck", b.preferred_luck], ["Universal luck", b.universal_luck], ["Resilience", b.resilience], ["Lure speed", b.lure_speed]]
      .filter(([, v]) => v != null).map(([l, v]) => `${l} ${v > 0 ? "+" : ""}${v}`);
    return s.join(" · ");
  }

  function paintBaits() {
    $("#u-bait-manage").checked = U.bait.manage;
    document.querySelectorAll("#u-bait-out button").forEach(b => b.classList.toggle("on", b.dataset.v === U.bait.when_out));
    baitPick?.refresh();
    const L = U.bait.list;
    $("#u-baits").innerHTML = L.length ? L.map((b, i) => {
      const info = baitInfo(b.name), cur = live && live.current_bait === b.name;
      return `<div class="u-item${b.on ? "" : " off"}${cur ? " cur" : ""}" data-i="${i}">
        ${info.icon ? `<img src="${esc(info.icon)}" alt="">` : "<span></span>"}
        <div class="u-body">
          <div class="u-name"><span class="u-rank">${i + 1}</span>${esc(b.name)}${cur ? ' <span class="chip own">in use</span>' : ""}${info.rarity ? ` <span class="chip">${esc(info.rarity)}</span>` : ""}</div>
          <div class="u-eff">${esc(stats(info))}${info.ability ? " · " + esc(info.ability) : ""}</div>
          <div class="u-ctl">
            <label>Max casts per run ${num(b.max_uses, "u-max")}</label>
            <label>Keep at least ${num(b.keep, "u-keep")}</label>
          </div>
          ${liveLine(b.name)}
        </div>
        <div class="u-side"><label class="sw"><input type="checkbox" class="u-on" ${b.on ? "checked" : ""}><span></span></label>
          <div class="u-move"><button class="u-up" title="Use earlier" ${i ? "" : "disabled"}>↑</button><button class="u-down" title="Use later" ${i < L.length - 1 ? "" : "disabled"}>↓</button></div>
          <button class="u-x" title="Remove">✕</button></div>
      </div>`;
    }).join("") : '<div class="subtle u-empty">No baits yet. Add them in the order you want them used.</div>';
    paintNow();
  }

  function paintNow() {
    const conditions = $("#u-weather-now");
    conditions.textContent = weather ? "Weather: " + (weather.names?.join(" · ") || "unreadable")
      + (weather.complete ? "" : " · incomplete; totems deferred") : "Weather: read between casts.";
    const el = $("#u-bait-now");
    if (!live || !live.current_bait) { el.textContent = U.bait.manage ? "Current bait: read during a run." : ""; return; }
    el.innerHTML = `Current bait: <b>${esc(live.current_bait)}</b>${live.bait_count != null ? ` · ${live.bait_count} left` : ""}`
      + (live.out_of_bait ? ' · <span style="color:var(--amber)">every bait in your list is at its limit</span>' : "");
  }

  function paint() {
    if (!U) return;
    $("#u-enabled").checked = U.enabled;
    paintTotems(); paintBaits();
  }

  /* ---------- events ---------- */
  $("#u-enabled").onchange = e => { U.enabled = e.target.checked; save(); };
  $("#u-bait-manage").onchange = e => { U.bait.manage = e.target.checked; save(); paintNow(); };
  $("#u-bait-out").onclick = e => { const b = e.target.closest("button"); if (!b) return; U.bait.when_out = b.dataset.v; paintBaits(); save(); };

  /* add pickers: searchable, grouped, with icons and what each item does */
  const TOTEM_GROUPS = [[/weather/i, "Weather"], [/event/i, "Events"], [/limited|unobtain/i, "Limited / unobtainable"]];
  const totemGroup = t => (TOTEM_GROUPS.find(([re]) => re.test(t.kind || "")) || [0, "Other"])[1];
  const RARITY = ["Trash", "Common", "Uncommon", "Unusual", "Rare", "Legendary", "Mythical", "Exotic", "Secret", "Special", "Limited"];
  const rarityAt = r => { const i = RARITY.indexOf(r); return i < 0 ? 99 : i; };
  totemPick = makePicker($("#u-totem-add"), {
    placeholder: "Add a totem…", empty: "Every totem is already in your list",
    items: () => !U ? [] : TOTEMS.filter(t => !U.totems.some(x => x.name === t.name))
      .map(t => ({name: t.name, icon: t.icon, group: totemGroup(t), sub: t.effect || "", tag: t.cooldown ? "cooldown" : ""}))
      .sort((a, b) => TOTEM_GROUPS.findIndex(g => g[1] === a.group) - TOTEM_GROUPS.findIndex(g => g[1] === b.group) || a.name.localeCompare(b.name)),
    onPick: n => { U.totems.push({name: n, ...TOTEM_DEFAULT}); paintTotems(); save(); },
  });
  baitPick = makePicker($("#u-bait-add"), {
    placeholder: "Add a bait…", empty: "Every bait is already in your list",
    items: () => !U ? [] : BAITS.filter(b => !U.bait.list.some(x => x.name === b.name))
      .sort((a, b) => rarityAt(a.rarity) - rarityAt(b.rarity) || a.name.localeCompare(b.name))
      .map(b => ({name: b.name, icon: b.icon, group: b.rarity || "Other", sub: [stats(b), b.ability].filter(Boolean).join(" · "),
                  tag: Object.keys(b.mutations || {})[0] || "", keywords: Object.keys(b.mutations || {}).join(" ")})),
    onPick: n => { U.bait.list.push({name: n, ...BAIT_DEFAULT}); paintBaits(); save(); },
  });

  function wire(listSel, arr, repaint) {
    const root = $(listSel);
    root.addEventListener("click", e => {
      const row = e.target.closest(".u-item"); if (!row) return;
      const i = +row.dataset.i, A = arr();
      if (e.target.closest(".u-x")) { A.splice(i, 1); }
      else if (e.target.closest(".u-up") && i > 0) { [A[i - 1], A[i]] = [A[i], A[i - 1]]; }
      else if (e.target.closest(".u-down") && i < A.length - 1) { [A[i + 1], A[i]] = [A[i], A[i + 1]]; }
      else if (e.target.closest(".u-when button")) { A[i].when = e.target.closest("button").dataset.v; }
      else return;
      repaint(); save();
    });
    root.addEventListener("change", e => {
      const row = e.target.closest(".u-item"); if (!row) return;
      const it = arr()[+row.dataset.i], t = e.target;
      const v = Math.max(+t.min || 0, Math.round(+t.value || 0));
      if (t.classList.contains("u-on")) { it.on = t.checked; row.classList.toggle("off", !t.checked); }
      else if (t.classList.contains("u-every")) it.every_min = Math.max(1, v);
      else if (t.classList.contains("u-max")) it.max_uses = v;
      else if (t.classList.contains("u-keep")) it.keep = v;
      else return;
      save();
    });
  }
  wire("#u-totems", () => U.totems, paintTotems);
  wire("#u-baits", () => U.bait.list, paintBaits);

  window.paintUseablesLive = (v, w) => {
    const changed = JSON.stringify(v) !== JSON.stringify(live) || JSON.stringify(w) !== JSON.stringify(weather);
    live = v;
    weather = w;
    if (changed && U && document.querySelector("#v-use.on") && !document.activeElement?.closest?.("#v-use input")) paint();
  };
  document.querySelector('[data-view="use"]').addEventListener("click", () => U ? paint() : init());
  window.addEventListener("pywebviewready", init);
})();
