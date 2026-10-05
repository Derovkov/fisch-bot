/* Searchable picker: a search box that drops down a list of items with icons.
 * Replaces native <select>s, whose popup Windows draws in its own (unreadable)
 * colours and which can't be searched. Uses esc from index.html.
 *
 *   makePicker(host, {placeholder, empty, items: () => [...], onPick: name => ...})
 *   item: {name, sub?, icon?, group?, tag?, keywords?}
 *
 * Every word typed must appear in the name, sub text, group or keywords; names
 * that start with the search come first. The list is position:fixed so cards
 * with overflow:auto don't clip it. */
window.matchWords = (text, q) => {
  const t = text.toLowerCase();
  return q.toLowerCase().split(/\s+/).filter(Boolean).every(w => t.includes(w));
};

window.makePicker = function (host, opt) {
  host.classList.add("pk");
  host.innerHTML = `<svg class="pk-ic" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>`
    + `<input type="text" class="pk-in" autocomplete="off" spellcheck="false" placeholder="${esc(opt.placeholder || "Search…")}">`;
  const input = host.querySelector(".pk-in");
  const list = document.createElement("div");
  list.className = "pk-list"; list.setAttribute("role", "listbox"); list.hidden = true;
  document.body.appendChild(list);
  let shown = [], at = 0;

  const rank = (it, q) => { const n = it.name.toLowerCase(); return n.startsWith(q) ? 0 : n.includes(q) ? 1 : 2; };
  function mark(name, q) {
    const i = q ? name.toLowerCase().indexOf(q) : -1;
    return i < 0 ? esc(name) : esc(name.slice(0, i)) + "<u>" + esc(name.slice(i, i + q.length)) + "</u>" + esc(name.slice(i + q.length));
  }

  function render() {
    const q = input.value.trim().toLowerCase(), all = opt.items();
    shown = all.filter(it => !q || matchWords([it.name, it.sub, it.group, it.keywords].filter(Boolean).join(" "), q));
    if (q) shown = shown.map((it, i) => [it, i]).sort((a, b) => rank(a[0], q) - rank(b[0], q) || a[1] - b[1]).map(x => x[0]);
    at = Math.max(0, Math.min(at, shown.length - 1));
    let html = "", group = null;
    shown.forEach((it, i) => {
      if (!q && it.group && it.group !== group) { html += `<div class="pk-group">${esc(it.group)}</div>`; group = it.group; }
      html += `<div class="pk-opt${i === at ? " at" : ""}" data-i="${i}" role="option">`
        + (it.icon ? `<img src="${esc(it.icon)}" alt="">` : "<span></span>")
        + `<div><b>${mark(it.name, q)}</b>${it.sub ? `<small>${esc(it.sub)}</small>` : ""}</div>`
        + (it.tag ? `<i>${esc(it.tag)}</i>` : "") + "</div>";
    });
    list.innerHTML = html || `<div class="pk-none">${all.length ? "Nothing matches “" + esc(input.value.trim()) + "”" : esc(opt.empty || "Nothing left to add")}</div>`;
    const n = all.length;
    list.dataset.count = q ? `${shown.length} of ${n}` : `${n}`;
  }

  function place() {
    const r = input.getBoundingClientRect(), w = Math.max(r.width + 26, 340);
    const below = innerHeight - r.bottom - 12, above = r.top - 12, up = below < 240 && above > below;
    list.style.width = w + "px";
    list.style.left = Math.max(8, Math.min(r.right - w, innerWidth - w - 8)) + "px";
    list.style.maxHeight = Math.min(380, up ? above : below) + "px";
    list.style.top = up ? "" : (r.bottom + 6) + "px";
    list.style.bottom = up ? (innerHeight - r.top + 6) + "px" : "";
  }
  function open() { render(); place(); list.hidden = false; }
  function close() { list.hidden = true; }
  function move(d) {
    if (!shown.length) return;
    at = (at + d + shown.length) % shown.length;
    list.querySelectorAll(".pk-opt").forEach(o => o.classList.toggle("at", +o.dataset.i === at));
    list.querySelector(".pk-opt.at")?.scrollIntoView({block: "nearest"});
  }
  function pick(i) {
    const it = shown[i]; if (!it) return;
    input.value = ""; at = 0;
    opt.onPick(it.name);
    if (!list.hidden) { render(); place(); }
  }

  input.addEventListener("focus", open);
  input.addEventListener("click", () => { if (list.hidden) open(); });
  input.addEventListener("input", () => { at = 0; open(); });
  input.addEventListener("blur", () => setTimeout(close, 120));
  input.addEventListener("keydown", e => {
    if (e.key === "ArrowDown") { e.preventDefault(); list.hidden ? open() : move(1); }
    else if (e.key === "ArrowUp") { e.preventDefault(); move(-1); }
    else if (e.key === "Enter") { e.preventDefault(); if (!list.hidden) pick(at); }
    else if (e.key === "Escape") { e.preventDefault(); input.value = ""; close(); input.blur(); }
  });
  list.addEventListener("mousedown", e => e.preventDefault());          // keep focus in the box
  list.addEventListener("click", e => { const o = e.target.closest(".pk-opt"); if (o) pick(+o.dataset.i); });
  document.addEventListener("scroll", e => { if (!list.hidden && !list.contains(e.target)) place(); }, true);
  addEventListener("resize", () => { if (!list.hidden) place(); });
  return {refresh() { if (!list.hidden) render(); }, input};
};
