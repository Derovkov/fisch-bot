/* Settings > Reel skins: the skins the bot has learned (fischskins.py), with
 * rename and forget. Uses $ and esc from index.html. */
(function () {
  const rgb = c => `rgb(${c[0]},${c[1]},${c[2]})`;
  const pct = k => Math.round(k * 100) + "%";

  function row(s, current) {
    const rods = Object.entries(s.rods || {}).map(([rod, v]) =>
      `${esc(rod)}: ${v.reels} reel${v.reels === 1 ? "" : "s"}, ${v.caught} caught, ${v.perfect} perfect${v.slider_w ? `, slider ${Math.round(v.slider_w)}px` : ""}`).join("<br>");
    return `<div class="sk-row${s.id === current ? " cur" : ""}" data-id="${esc(s.id)}">
      <div class="sk-sw"><i style="background:${rgb(s.track)}" title="track"></i><i style="background:${rgb(s.slider)}" title="slider"></i></div>
      <div class="sk-body">
        <input class="sk-name" value="${esc(s.name)}" maxlength="40" aria-label="Skin name">
        ${s.id === current ? '<span class="chip own">in use</span>' : ""}
        <div class="sk-info">track ${pct(s.k)} long${s.progress ? " · progress fills by " + esc(s.progress) : ""}${s.last_seen ? " · seen " + new Date(s.last_seen * 1000).toLocaleString() : ""}</div>
        <div class="sk-info">${rods || "no reels yet"}</div>
      </div>
      <button class="mini-button sk-forget" title="Forget this skin">Forget</button>
    </div>`;
  }

  async function paint() {
    const r = await pywebview.api.get_skins();
    if (!r.ok) return;
    $("#sk-list").innerHTML = r.skins.length ? r.skins.map(s => row(s, r.current)).join("")
      : "No skins saved yet -- they're learned as you fish.";
  }

  $("#sk-list").addEventListener("change", async e => {
    const inp = e.target.closest(".sk-name"); if (!inp) return;
    const id = inp.closest(".sk-row").dataset.id;
    const r = await pywebview.api.edit_skin(id, inp.value, false);
    if (!r.ok) alert(r.error); paint();
  });
  $("#sk-list").addEventListener("keydown", e => { if (e.key === "Enter" && e.target.closest(".sk-name")) e.target.blur(); });
  $("#sk-list").addEventListener("click", async e => {
    const b = e.target.closest(".sk-forget"); if (!b) return;
    const row = b.closest(".sk-row");
    if (!confirm(`Forget "${row.querySelector(".sk-name").value}"? The bot learns it again next time it sees it.`)) return;
    await pywebview.api.edit_skin(row.dataset.id, "", true); paint();
  });
  document.querySelectorAll('[data-view="settings"]').forEach(b => b.addEventListener("click", paint));
})();
