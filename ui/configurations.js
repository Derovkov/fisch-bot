/* Named setups. Inventory/favourites remain global when loading a setup. */
let profiles = [], configState = {}, editingConfig = null, editingSnapshot = null;
let configListKey = '', configBusy = false, configReturnFocus = null;
let lastHotkeySetup = '';
const configKeys = ['rod','max_fish','focus','debug','trace','keep','lookahead','deadband','bite'];
function configSnapshot(settings) {
  const value = {};
  for (const key of configKeys) value[key] = settings[key];
  value.rod_enchants = {[settings.rod]:[...(settings.rod_enchants?.[settings.rod] || [])]};
  return value;
}
function sameConfig(a,b) { return !!a && !!b && JSON.stringify(configSnapshot(a)) === JSON.stringify(configSnapshot(b)); }
function configLabel() {
  const saved = profiles.find(p=>p.id===S.active_profile);
  return saved ? saved.name + (sameConfig(saved.settings,S) ? '' : ' · modified') : 'Custom setup';
}
function initConfigurations(rows) {
  profiles = rows;
  $('#config-save-new').onclick = ()=>openConfiguration();
  $('#config-list').onclick = async e=> {
    const use = e.target.closest('[data-config-use]'), edit = e.target.closest('[data-config-edit]');
    if (e.target.closest('[data-config-new]')) return openConfiguration();
    if (use) await useConfiguration(use.dataset.configUse);
    if (edit) openConfiguration(edit.dataset.configEdit);
  };
  $('#config-apply').onclick = async ()=> {
    if (!running) return show('settings');
    await applyDraft();
  };
  $('#config-cancel').onclick = async ()=> { await pywebview.api.cancel_configuration(); };
  $('#config-modal-cancel').onclick = closeConfiguration;
  $('#config-modal').onclick = e=> { if(e.target.id==='config-modal') closeConfiguration(); };
  $('#config-save').onclick = ()=>saveConfiguration(false);
  $('#config-save-copy').onclick = ()=>saveConfiguration(true);
  $('#config-delete').onclick = deleteConfiguration;
  $('#config-input').oninput = ()=> {
    $('#config-delete').textContent = 'Delete'; $('#config-delete').dataset.confirm = '';
  };
  document.addEventListener('keydown',e=> {
    if (!$('#config-modal').classList.contains('on')) return;
    if (e.key==='Escape') closeConfiguration();
    if (e.key==='Enter' && e.target.id==='config-input') {e.preventDefault(); saveConfiguration(false);}
    if (e.key==='Tab') {
      const focusable = [...$('#config-modal').querySelectorAll('button,input')].filter(x=>!x.hidden && !x.disabled);
      const first=focusable[0], last=focusable[focusable.length-1];
      if (e.shiftKey && document.activeElement===first) {e.preventDefault(); last.focus();}
      else if (!e.shiftKey && document.activeElement===last) {e.preventDefault(); first.focus();}
    }
  });
}
function paintConfigurations(state) {
  if (!META) return;
  if (state) configState=state;
  const active = running ? configState.active_config : null, pending = running ? configState.pending_config : null;
  const hotkeySetup = pending?.origin==='hotkey' ? pending : active?.origin==='hotkey' ? active : null;
  if (hotkeySetup) {
    const key=JSON.stringify(hotkeySetup);
    if(key!==lastHotkeySetup) {
      lastHotkeySetup=key;
      S=Object.assign({},S,configSnapshot(hotkeySetup.settings),{active_profile:hotkeySetup.id,
        rod_enchants:Object.assign({},S.rod_enchants,hotkeySetup.settings.rod_enchants)});
      paintSettings(); saveSettings();
    }
  } else lastHotkeySetup='';
  $('#config-status').textContent = active ? 'Running setup' : 'Selected setup';
  $('#config-name').textContent = active?.name || configLabel();
  const settings = active?.settings || S;
  $('#config-detail').textContent = `${settings.rod} · ${settings.max_fish ? settings.max_fish+' casts' : 'Unlimited casts'}`;
  $('#config-pending').hidden = !pending;
  if (pending) $('#config-pending span').textContent = `${pending.name} · switches after this cast`;
  const dirty = active && !sameConfig(active.settings,S);
  $('#config-apply').textContent = running ? (dirty ? 'Apply changes' : 'Up to date') : 'Edit setup';
  $('#config-apply').disabled = running && (!dirty || configBusy);
  $('#config-hint').textContent = dirty ? 'Edited setup · apply after this cast' : running ? `${window.hotkeyLabel?.('switch') || 'F6'} · next saved setup` : 'Saved locally · ready for next Start';
  const key = JSON.stringify([profiles, S.active_profile, active, pending, running, configBusy]);
  if (key===configListKey) return;
  configListKey=key;
  $('#config-list').innerHTML = profiles.length ? profiles.map(p=> {
    const current = running ? active?.id===p.id && sameConfig(active.settings,p.settings) : p.id===S.active_profile;
    const queued = pending?.id===p.id;
    const ench = p.settings.rod_enchants?.[p.settings.rod] || [];
    return `<div class="config-row${current?' active':''}${queued?' pending':''}"><div class="details"><b>${esc(p.name)}</b><small>${esc(p.settings.rod)}${ench.length?' · '+ench.length+' enchant'+(ench.length===1?'':'s'):''}</small></div><button class="mini-button" data-config-use="${esc(p.id)}" ${configBusy || queued || (current && (!pending || !running))?'disabled':''}>${queued?'Queued':current?'Active':running?'Switch':'Use'}</button><button class="edit" data-config-edit="${esc(p.id)}" aria-label="Edit ${esc(p.name)}" title="Rename, update or delete">⋯</button></div>`;
  }).join('') + '<button class="config-new" data-config-new>+ New setup from current settings</button>'
    : '<div class="config-empty">Keep your favourite setups here.<br>Save your current rod and settings to get started.<br><button class="mini-button accent" data-config-new style="margin-top:10px">+ New setup</button></div>';
}
async function useConfiguration(id) {
  if (configBusy) return;
  const row=profiles.find(p=>p.id===id); if (!row) return;
  const next = Object.assign({},S,configSnapshot(row.settings),{active_profile:id,
    rod_enchants:Object.assign({},S.rod_enchants,row.settings.rod_enchants)});
  configBusy=true; paintConfigurations();
  try {
    if (running) {
      const result=await pywebview.api.queue_configuration(JSON.stringify(next));
      if (!result.ok) {$('#err').textContent=result.error; return;}
    }
    S=next; saveSettings(); paintSettings(); $('#err').textContent='';
  } catch(e) {$('#err').textContent='Could not switch configuration: '+e;}
  finally {configBusy=false; configListKey=''; paintConfigurations();}
}
async function applyDraft() {
  if (configBusy) return;
  configBusy=true; paintConfigurations();
  try {
    const result=await pywebview.api.queue_configuration(JSON.stringify(S));
    $('#err').textContent=result.ok ? '' : result.error;
  } finally {configBusy=false; paintConfigurations();}
}
function openConfiguration(id='') {
  editingConfig=profiles.find(p=>p.id===id) || null;
  editingSnapshot=editingConfig && editingConfig.id!==S.active_profile ? configSnapshot(editingConfig.settings) : configSnapshot(S);
  $('#config-modal-title').textContent=editingConfig ? 'Edit setup' : 'New setup';
  $('#config-input').value=editingConfig?.name || '';
  $('#config-save').textContent=editingConfig ? 'Update setup' : 'Create setup';
  $('#config-delete').hidden=!editingConfig; $('#config-save-copy').hidden=!editingConfig;
  $('#config-delete').textContent='Delete'; $('#config-delete').dataset.confirm='';
  const s=editingSnapshot, ench=s.rod_enchants[s.rod];
  $('#config-preview').textContent=`${s.rod}${ench.length?' · '+ench.join(', '):''}\n${s.max_fish?s.max_fish+' casts':'Unlimited casts'} · lookahead ${s.lookahead}s · deadband ${Math.round(s.deadband*100)}%\n${s.trace?'Measurements on':'Measurements off'} · ${s.keep?'Keep run logs':'Delete run data at Stop'}`;
  $('#config-preview').style.whiteSpace='pre-line';
  $('#config-error').textContent=''; configReturnFocus=document.activeElement;
  $('#config-modal').classList.add('on'); $('#config-input').focus();
}
function closeConfiguration() {
  if(configBusy) return;
  $('#config-modal').classList.remove('on');
  configReturnFocus?.focus();
}
async function saveConfiguration(copy) {
  if(configBusy) return;
  configBusy=true;
  $('#config-save').disabled=true; $('#config-save-copy').disabled=true;
  try {
    const result=await pywebview.api.save_profile($('#config-input').value,JSON.stringify(editingSnapshot),copy?'':editingConfig?.id || '');
    if (!result.ok) {$('#config-error').textContent=result.error;return;}
    profiles=result.profiles;
    // Saving a different setup must not silently change the draft/active rod.
    if (sameConfig(S,result.profile.settings)) S.active_profile=result.profile.id;
    saveSettings(); paintSettings(); $('#config-modal').classList.remove('on'); configReturnFocus?.focus();
  } catch(e) {$('#config-error').textContent=String(e);}
  finally {configBusy=false; $('#config-save').disabled=false; $('#config-save-copy').disabled=false; paintConfigurations();}
}
async function deleteConfiguration() {
  if(configBusy || !editingConfig) return;
  const button=$('#config-delete');
  if (!button.dataset.confirm) {button.textContent='Delete this setup?';button.dataset.confirm='yes';return;}
  configBusy=true;
  try {
    const result=await pywebview.api.delete_profile(editingConfig.id);
    if(!result.ok) {$('#config-error').textContent=result.error;return;}
    profiles=result.profiles;
    if(S.active_profile===editingConfig.id) S.active_profile='';
    saveSettings(); $('#config-modal').classList.remove('on');configReturnFocus?.focus();
  } finally {configBusy=false; paintConfigurations();}
}
