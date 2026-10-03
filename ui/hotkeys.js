/* App-wide shortcuts live in the general config, not rod setups. */
(function () {
  let bindings = {start: 'f7', stop: 'f9', switch: 'f6'};
  window.hotkeyLabel = action => (bindings[action] || '').toUpperCase();
  function paint(r) {
    bindings = r.hotkeys;
    for (const [action, key] of Object.entries(bindings)) $('#hk-' + action).value = key;
    document.querySelectorAll('[data-shortcut]').forEach(el => el.textContent = window.hotkeyLabel(el.dataset.shortcut));
    if (typeof META !== 'undefined' && META) {
      META.hotkeys = bindings;
      const base = META.help_base || META.help;
      META.help = Object.fromEntries(Object.entries(base).map(([k, v]) => [k,
        v.replace(/F6|F9/g, key => window.hotkeyLabel(key === 'F6' ? 'switch' : 'stop'))]));
    }
    $('#hk-status').textContent = r.available ? 'Saved · shortcuts work while Roblox is in front.'
      : 'Saved · global shortcuts unavailable' + (r.error ? ': ' + r.error : '; use the buttons or restart the app.');
  }
  $('#hk-save').onclick = async () => {
    const draft = Object.fromEntries(Object.keys(bindings).map(a => [a, $('#hk-' + a).value]));
    const r = await pywebview.api.save_hotkeys(JSON.stringify(draft));
    if (r.ok) paint(r);
    else $('#hk-status').textContent = 'Not saved: ' + r.error;
  };
  $('#hk-defaults').onclick = () => {
    for (const [a, v] of Object.entries({start: 'f7', stop: 'f9', switch: 'f6'})) $('#hk-' + a).value = v;
    $('#hk-status').textContent = 'Default keys ready · click Save hotkeys to apply.';
  };
  window.addEventListener('pywebviewready', async () => {
    try { const r = await pywebview.api.get_hotkeys(); if (r.ok) paint(r); }
    catch (e) { $('#hk-status').textContent = 'Could not load hotkeys: ' + e.message; }
  });
})();
