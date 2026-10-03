/* Quests and Index tabs.
 *   Quests: the quest tracker as last read (by the running bot after every cast, or
 *           "Read now" when it isn't running), the plan for mutation objectives,
 *           and this run's mutation tally.
 *   Index:  wiki indexes (fischwiki.py): mutations, weather, totems, quest givers,
 *           fish and other fishables.
 * Uses $, esc and S from index.html. */
(function () {
  let lastQuests = null, lastMuts = {};

  /* ---------- Quests ---------- */
  function paintQuests(v) {
    if (!v) return;
    lastQuests = v;
    const qs = v.quests || [];
    $("#q-count").textContent = `${qs.length} tracked · ${qs.filter(q => q.done).length} complete`
      + (v.read_at ? ` · read ${new Date(v.read_at * 1000).toLocaleTimeString()}` : "");
    $("#q-list").innerHTML = qs.length ? qs.map(q => `
      <div class="q-quest">
        <div class="q-title">${esc(q.title)}${q.done ? ' <span class="done">✓ complete</span>' : ""}</div>
        ${q.npc ? `<div class="q-giver">${esc(q.npc)}${q.location ? " · " + esc(q.location) : ""}${q.done ? " · hand it in" : ""}</div>` : ""}
        ${q.description ? `<div class="q-desc">${esc(q.description)}</div>` : ""}
        ${q.objectives.map(o => {
          const pct = o.need ? Math.min(100, 100 * (o.have || 0) / o.need) : (o.done ? 100 : 0);
          return `<div class="q-obj${o.done ? " done" : ""}">
            <span class="box">${o.done ? "✓" : ""}</span>
            <span class="txt">${esc(o.text)}
              ${reqChip(o)}
              ${o.perfect ? '<span class="chip perf">perfect catch</span>' : ""}</span>
            ${o.need ? `<span class="q-prog"><i style="width:${pct}%"></i></span><span class="q-num">${o.have}/${o.need}</span>` : ""}
          </div>`;
        }).join("")}
      </div>`).join("") : '<div class="subtle">No quests on the tracker.</div>';
    const plan = v.plan || [];
    $("#q-plan").innerHTML = plan.length ? plan.map(l => {
      const [need, how] = l.split(" -- needs ");
      return `<div class="q-plan-line">${esc(need)}<br><b>needs ${esc(how || "")}</b></div>`;
    }).join("") : "No unfinished objective needs a mutation.";
  }

  /* What an objective needs: one mutation ON the item(s) -- "Gusty Empyrean
     Relic" is Empyrean Relic with Gusty, not two mutations -- plus any
     attributes (Shiny, Big...), which don't count toward the one-mutation limit. */
  function reqChip(o) {
    const m = (o.mutations || [])[0], a = o.attributes || [], items = o.items || [];
    if (!m && !a.length) return "";
    const what = items.length ? items.slice(0, 2).join(" / ") + (items.length > 2 ? ` +${items.length - 2}` : "") : "any fish";
    return `<span class="chip req" title="mutation${m ? ": " + esc(m) : " -- none"}${a.length ? " · attributes: " + esc(a.join(", ")) : ""} · on ${esc(items.join(", ") || "any fish")}">`
      + a.map(x => `<i class="at">${esc(x)}</i> `).join("") + (m ? `<b>${esc(m)}</b> ` : "") + `${esc(what)}</span>`;
  }

  function paintMuts(m) {
    lastMuts = m || {};
    const e = Object.entries(lastMuts).sort((a, b) => b[1] - a[1]);
    $("#q-muts").innerHTML = e.length ? e.map(([n, c]) => `<span class="chip mut">${esc(n)} ×${c}</span>`).join(" ")
      : "None yet.";
  }

  window.paintExtrasLive = st => {
    if (st.quests) paintQuests(st.quests);
    if (st.mutations && Object.keys(st.mutations).length) paintMuts(st.mutations);
  };

  $("#q-read").onclick = async () => {
    const b = $("#q-read"); b.disabled = true; b.textContent = "Reading…";
    window.questReading = true;
    $("#q-cancel").hidden = false;
    try {
      const r = await pywebview.api.read_quests();
      if (r.cancelled) { $("#q-count").textContent = "Quest search cancelled"; return; }
      if (!r.ok) { $("#q-list").innerHTML = `<div class="subtle" style="color:var(--red)">${esc(r.error)}</div>`; return; }
      paintQuests(r);
    } finally {
      window.questReading = false;
      $("#q-cancel").hidden = true;
      b.disabled = false; b.innerHTML = "⟳&nbsp; Read now";
    }
  };
  $("#q-cancel").onclick = () => pywebview.api.stop();

  /* ---------- Index ---------- */
  const DATA = {};
  let kind = "mutations", filter = "all", query = "";
  const FILTERS = {
    mutations: [["all", "All"], ["mine", "From my rods"], ["rod", "Rods"], ["enchant", "Enchants"], ["natural", "Natural"], ["event", "Event / limited"]],
    weather: [["all", "All"], ["weather", "Weather"], ["modifier", "Modifiers"], ["sovereign", "Sovereign"], ["astral", "Astral"], ["time", "Time / season"]],
    totems: [["all", "All"], ["weather", "Weather"], ["event", "Events"]],
    quests: [["all", "All"], ["repeatable", "Repeatable"], ["withquests", "Has quests"]],
    fish: [["all", "All"], ["fish", "Fish"], ["nonfish", "Other fishables"], ["quest", "In my quests"]],
  };
  const LIMIT = 250;

  async function load(k) {
    if (DATA[k]) return DATA[k];
    const r = await pywebview.api.get_index(k);
    if (!r.ok) { $("#ix-list").innerHTML = `<div class="subtle" style="color:var(--red)">${esc(r.error)}</div>`; return null; }
    return (DATA[k] = r.data);
  }

  const owned = () => (S.owned || []);
  const chips = (arr, cls = "") => arr.map(x => `<span class="chip ${cls}">${esc(x)}</span>`).join("");
  const match = txt => !query || txt.toLowerCase().includes(query);

  function rowsMutations(d) {
    const mine = new Set(owned());
    return d.mutations.filter(m => {
      const rods = Object.keys(m.rods || {});
      if (filter === "mine" && !rods.some(r => mine.has(r))) return false;
      if (!["all", "mine"].includes(filter) && m.group !== filter) return false;
      return match([m.name, m.group, rods.join(" "), (m.enchants || []).join(" "), (m.weather || []).join(" ")].join(" "));
    }).map(m => {
      const rods = Object.entries(m.rods || {}).sort((a, b) => (mine.has(b[0]) - mine.has(a[0])) || ((b[1] || 0) - (a[1] || 0)));
      return `<div class="ix-row"><span></span>
        <div class="nm">${esc(m.name)}<small>${typeof m.value === "number" ? m.value + "× value" : esc(m.value)} · ${esc(m.group)}${m.type && m.type !== "Mutations" ? " · " + esc(m.type) : ""}</small></div>
        <div class="dt">${rods.map(([r, c]) => `<span class="chip ${mine.has(r) ? "own" : ""}">${esc(r)}${c ? " " + c + "%" : ""}</span>`).join("")}
          ${chips((m.enchants || []).map(e => "enchant: " + e))}${chips((m.weather || []).map(w => "weather: " + w))}
          ${chips((m.events || []).map(w => "event: " + w))}${chips((m.seasons || []).map(w => "season: " + w))}
          ${!rods.length && !(m.enchants || []).length && !(m.weather || []).length ? `<span>${esc((m.sources || []).join(", "))}</span>` : ""}</div></div>`;
    });
  }

  function rowsWeather(d) {
    return d.weather.filter(w => {
      if (filter === "time" ? !["time", "season"].includes(w.group) : filter !== "all" && w.group !== filter) return false;
      return match([w.name, w.group, w.totem || "", Object.keys(w.mutations || {}).join(" ")].join(" "));
    }).map(w => `<div class="ix-row">${w.icon ? `<img src="${esc(w.icon)}" alt="">` : "<span></span>"}
      <div class="nm">${esc(w.name)}<small>${esc(w.group)}${w.totem ? " · " + esc(w.totem) : ""}${w.natural ? " · natural" : ""}</small></div>
      <div class="dt">${Object.entries(w.mutations || {}).map(([m, c]) => `<span class="chip mut">${esc(m)}${c ? " " + c + "%" : ""}</span>`).join("")}
        <span>${esc((w.effects || []).filter(e => !/^\{\{Mutation/.test(e)).slice(0, 3).join(" · "))}</span></div></div>`);
  }

  function rowsTotems(d) {
    return d.totems.filter(t => {
      if (filter === "weather" && !/Weather/i.test(t.kind || "")) return false;
      if (filter === "event" && !/Event/i.test(t.kind || "")) return false;
      return match([t.name, t.effect || "", t.weather || ""].join(" "));
    }).map(t => `<div class="ix-row">${t.icon ? `<img src="${esc(t.icon)}" alt="">` : "<span></span>"}
      <div class="nm">${esc(t.name)}<small>${esc(t.kind || "")}${t.cooldown ? " · has a cooldown" : ""}</small></div>
      <div class="dt"><span>${esc(t.effect || "")}</span>${t.weather ? `<span class="chip">${esc(t.weather)}</span>` : ""}</div></div>`);
  }

  function rowsQuests(d) {
    return d.npcs.filter(n => {
      if (filter === "repeatable" && !n.repeatable) return false;
      if (filter === "withquests" && !n.quests.length) return false;
      return match([n.npc, n.locations.join(" "), n.quests.map(q => q.name).join(" ")].join(" "));
    }).map(n => `<div class="ix-row"><span></span>
      <div class="nm">${esc(n.npc)}<small>${esc(n.locations.join(", "))}${n.gps ? " · " + n.gps.join(", ") : ""}</small></div>
      <div class="dt">${n.repeatable ? '<span class="chip rep">repeatable</span>' : ""}
        ${n.quests.map(q => `<span class="chip" title="${esc(q.steps.flatMap(s => s.tasks.map(t => t.text)).join(" · "))}">${esc(q.name)}${q.steps.some(s => s.tasks.some(t => t.mutations.length)) ? " ◆" : ""}</span>`).join("")}</div></div>`);
  }

  function rowsFish(d) {
    const want = new Set((lastQuests?.quests || []).flatMap(q => q.objectives.filter(o => !o.done).flatMap(o => o.items || [])));
    return d.fish.filter(f => {
      if (filter === "fish" && f.nonfish) return false;
      if (filter === "nonfish" && !f.nonfish) return false;
      if (filter === "quest" && !want.has(f.name)) return false;
      return match([f.name, f.rarity || "", f.type || "", f.locations.join(" "), f.bait || "", f.weather || "", f.time || ""].join(" "));
    }).map(f => `<div class="ix-row"><span></span>
      <div class="nm">${esc(f.name)}<small>${esc(f.rarity || "?")}${f.nonfish ? " · " + esc(f.type || "non-fish") : ""}</small></div>
      <div class="dt">${want.has(f.name) ? '<span class="chip own">quest</span>' : ""}${chips(f.locations)}
        ${f.time ? `<span class="chip">${esc(f.time)}</span>` : ""}${f.weather ? `<span class="chip">weather: ${esc(f.weather)}</span>` : ""}
        ${f.season ? `<span class="chip">season: ${esc(f.season)}</span>` : ""}${f.bait ? `<span>bait: ${esc(f.bait)}</span>` : ""}</div></div>`);
  }

  async function paintIndex() {
    document.querySelectorAll("#ix-kind button").forEach(b => b.classList.toggle("on", b.dataset.v === kind));
    $("#ix-filter").innerHTML = FILTERS[kind].map(([v, l]) => `<button data-v="${v}" class="${v === filter ? "on" : ""}">${l}</button>`).join("");
    const d = await load(kind); if (!d) return;
    const rows = {mutations: rowsMutations, weather: rowsWeather, totems: rowsTotems, quests: rowsQuests, fish: rowsFish}[kind](d);
    $("#ix-count").textContent = `${rows.length} shown · fetched ${d.fetched || "?"}`;
    $("#ix-list").innerHTML = rows.slice(0, LIMIT).join("") + (rows.length > LIMIT ? `<div class="ix-more">… ${rows.length - LIMIT} more -- narrow the search</div>` : "");
  }

  $("#ix-kind").onclick = e => { const b = e.target.closest("button"); if (!b) return; kind = b.dataset.v; filter = "all"; paintIndex(); };
  $("#ix-filter").onclick = e => { const b = e.target.closest("button"); if (!b) return; filter = b.dataset.v; paintIndex(); };
  $("#ix-search").oninput = e => { query = e.target.value.trim().toLowerCase(); paintIndex(); };
  document.querySelector('[data-view="index"]').addEventListener("click", () => paintIndex());
})();
