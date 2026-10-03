// Headless UI check; bridge is mocked, no Roblox capture or input.
const fs = require('node:fs');
const path = require('node:path');
const {pathToFileURL} = require('node:url');
const assert = require('node:assert/strict');
const {chromium} = require(process.env.FISCH_PLAYWRIGHT || 'playwright');
const root = path.resolve(__dirname,'..');
(async()=> {
  const meta = JSON.parse(fs.readFileSync(path.join(root,'tmp/ui_meta_fixture.json'),'utf8'));
  const indexes = Object.fromEntries(['baits','totems'].map(k=>[k,JSON.parse(fs.readFileSync(path.join(root,`ui/${k}.json`),'utf8'))]));
  const browser = await chromium.launch({channel:'msedge',headless:true});
  try {
    const page = await browser.newPage({viewport:{width:1280,height:840}});
    await page.route(/^https?:/,route=>route.abort());
    const errors=[];page.on('pageerror',e=>errors.push(e.message));
    await page.addInitScript(({meta,indexes})=> {
      window.fixtureMeta=meta;meta.saved={...meta.defaults,rod:'Duskwire',owned:['Duskwire','Fabulous Rod']};
      const setups=[['steady','Steady Duskwire','Duskwire',.5],['crew','Crew · steady','Crew Rod',.35]];
      meta.profiles=setups.map(([id,name,rod,lookahead])=>({id,name,settings:{...meta.defaults,rod,lookahead,rod_enchants:{[rod]:[]}}}));
      window.mockState={running:false,state:'idle',caught:0,lost:0,casts:0,live:null,history:[],max_fish:20,started:null,calibrated:false,runs:[],logs:[],now:Date.now()/1000};
      window.savedSettings=null;
      window.mockHotkeys={start:'f7',stop:'f9',switch:'f6'};
      window.pywebview={api:{
        get_meta:async()=>meta,get_state:async()=>window.mockState,
        get_hotkeys:async()=>({ok:true,hotkeys:window.mockHotkeys,available:true}),
        save_hotkeys:async text=>{
          const keys=JSON.parse(text);
          if(new Set(Object.values(keys)).size!==3)return{ok:false,error:'use different shortcuts'};
          window.mockHotkeys=keys;return{ok:true,hotkeys:keys,available:true};
        },
        get_general:async()=>({ok:true,general:{useables:{enabled:true,bait:{manage:false,when_out:'stop',list:[]},totems:[{name:'Aurora Totem',on:true,when:'every',every_min:15,max_uses:2,keep:1}]}}}),
        get_index:async k=>({ok:true,data:indexes[k]}),
        save_settings:async text=>{window.savedSettings=JSON.parse(text);},
        save_profile:async(name,text,id)=> {
          const p={id:id || 'new-setup',name,settings:JSON.parse(text)};
          meta.profiles=meta.profiles.filter(row=>row.id!==p.id).concat(p);
          return {ok:true,profile:p,profiles:meta.profiles};
        },
        delete_profile:async id=> {meta.profiles=meta.profiles.filter(row=>row.id!==id);return{ok:true,profiles:meta.profiles};},
        queue_configuration:async text=> {
          const s=JSON.parse(text), p=meta.profiles.find(p=>p.id===s.active_profile);
          window.mockState.pending_config={id:s.active_profile,name:p?.name || 'Custom setup',settings:s};return{ok:true};
        },cancel_configuration:async()=>{window.mockState.pending_config=null;},
        read_quests:async()=>{
          window.mockState.search={busy:true,kind:'quests'};
          return new Promise(resolve=>window.finishSearch=()=>resolve({ok:false,cancelled:true}));
        },
        scan_rods:async()=>{
          window.mockState.search={busy:true,kind:'rods'};
          return new Promise(resolve=>window.finishSearch=()=>resolve({ok:true,cancelled:true,complete:false,
            cards:[{rod:'Duskwire',enchants:[],equipped:true}]}));
        },
        scan_progress:async()=>({busy:window.mockState.search?.busy,done:1,total:100,found:1}),
        start:async()=>({ok:true}),stop:async()=>{
          window.mockState.search={busy:false};
          window.finishSearch?.();window.finishSearch=null;
        },
      }};
    },{meta,indexes});
    await page.goto(pathToFileURL(path.join(root,'ui/index.html')).href);
    await page.evaluate(()=>window.dispatchEvent(new Event('pywebviewready')));
    await page.waitForFunction(()=>document.querySelectorAll('.config-row').length===2);
    await page.locator('[data-view="settings"]').click();
    await page.locator('#hk-start').fill('ctrl+alt+r');
    await page.locator('#hk-stop').fill('f10');
    await page.locator('#hk-switch').fill('f11');
    await page.locator('#hk-save').click();
    await page.waitForFunction(()=>document.querySelector('[data-shortcut="start"]').textContent==='CTRL+ALT+R');
    await page.locator('[data-view="help"]').click();
    assert.match(await page.locator('#v-help').textContent(),/CTRL\+ALT\+R/);
    assert.match(await page.locator('#v-help').textContent(),/two minutes/);
    for(const size of [{width:1280,height:840},{width:1080,height:720}]){
      await page.setViewportSize(size);
      const width=await page.evaluate(()=>({s:document.querySelector('.main').scrollWidth,c:document.querySelector('.main').clientWidth}));
      assert.ok(width.s<=width.c+1,JSON.stringify(width));
    }
    await page.setViewportSize({width:1280,height:840});
    await page.screenshot({path:path.join(root,'tmp/ui-help-preview.png')});
    await page.locator('[data-view="quests"]').click();
    assert.equal(await page.locator('#q-cancel').isVisible(),false);
    await page.locator('#q-read').click();
    await page.waitForFunction(()=>window.mockState.search?.busy);
    assert.equal(await page.locator('#q-cancel').isVisible(),true);
    assert.match(await page.locator('#q-cancel').textContent(),/F10/);
    await page.locator('#q-cancel').click();
    await page.waitForFunction(()=>document.querySelector('#q-count').textContent==='Quest search cancelled');
    assert.equal(await page.locator('#q-cancel').isVisible(),false);
    // Dashboard Stop also works during a search, with no fishing worker running.
    await page.locator('#q-read').click();
    await page.locator('[data-view="dash"]').click();
    await page.waitForFunction(()=>!document.querySelector('#stop').disabled);
    assert.equal(await page.locator('#start').isDisabled(),true);
    await page.locator('#stop').click();
    await page.waitForFunction(()=>!window.questReading);
    await page.locator('[data-view="rods"]').first().click();
    await page.locator('#scan').click();await page.locator('#scan-go').click();
    await page.waitForFunction(()=>window.mockState.search?.kind==='rods');
    await page.locator('[data-view="dash"]').click();
    await page.waitForFunction(()=>!document.querySelector('#stop').disabled);
    await page.locator('#stop').click();
    await page.waitForFunction(()=>document.querySelector('#scan-sub').textContent.includes('before you cancelled'));
    await page.evaluate(()=>document.querySelector('#scan-modal').classList.remove('on'));
    await page.waitForFunction(()=>document.querySelector('#stop').disabled);
    await page.locator('[data-view="use"]').click();
    await page.evaluate(()=> {
      window.mockState.weather={names:['Spring','Night','Rain','Aurora Borealis'],complete:true};
      window.mockState.useables={enabled:true,items:{'Aurora Totem':{uses:0,count:9,status:'Aurora Borealis is already active'}}};
    });
    await page.waitForFunction(()=>document.querySelector('#u-weather-now').textContent.includes('Aurora Borealis'));
    assert.match(await page.locator('#u-totems').textContent(),/already active/);
    await page.evaluate(()=>window.mockState.weather={names:['Spring','Night'],complete:false});
    await page.waitForFunction(()=>document.querySelector('#u-weather-now').textContent.includes('totems deferred'));
    await page.screenshot({path:path.join(root,'tmp/ui-weather-preview.png')});
    await page.locator('[data-view="dash"]').click();
    await page.getByRole('button',{name:'Use',exact:true}).first().click();
    assert.equal(await page.locator('#config-name').textContent(),'Steady Duskwire');
    assert.deepEqual(await page.evaluate(()=>S.owned),['Duskwire','Fabulous Rod']);
    await page.locator('#config-save-new').click();
    await page.locator('#config-input').fill('My test setup');await page.locator('#config-save').click();
    await page.waitForFunction(()=>document.querySelectorAll('.config-row').length===3);
    await page.evaluate(()=> {
      const m=window.fixtureMeta;
      window.mockState={...window.mockState,running:true,state:'reeling',caught:18,lost:2,casts:20,max_fish:50,started:Date.now()/1000-140,calibrated:true,
        active_config:{id:'steady',name:'Steady Duskwire',settings:m.profiles.find(p=>p.id==='steady').settings},
        live:{in_reel:true,fill:.67,inside:true,elapsed:4.2},
        history:Array.from({length:20},(_,i)=>({caught:i!==8 && i!==14,duration:5.1+i%3,filling:.97,readings:49,confirmation:i%2?'duskwire passive':'caught message'})),runs:[]};
    });
    await page.waitForFunction(()=>document.querySelector('#n-caught').textContent==='18');
    await page.locator('[data-config-use="crew"]').click();
    await page.waitForFunction(()=>!document.querySelector('#config-pending').hidden);
    assert.match(await page.locator('#config-pending').textContent(),/Crew/);
    assert.match(await page.locator('#subline').textContent(),/Duskwire/);
    await page.locator('#config-cancel').click();
    await page.waitForFunction(()=>document.querySelector('#config-pending').hidden);
    // A hotkey switch can be applied between polls, with no queued snapshot
    // ever seen by JavaScript. The selected draft must still follow it.
    await page.evaluate(()=> {
      const p=window.fixtureMeta.profiles.find(p=>p.id==='steady');
      window.mockState.active_config={...p,origin:'hotkey'};
    });
    await page.waitForFunction(()=>S.rod==='Duskwire' && S.active_profile==='steady');
    await page.locator('.help[data-help="profiles"]').hover();
    assert.match(await page.locator('#tip').textContent(),/current cast/);
    await page.mouse.move(1,1);
    for(const size of [{width:1280,height:840},{width:1080,height:720}]) {
      await page.setViewportSize(size);
      const bounds=await page.evaluate(()=> {
        const main=document.querySelector('.main'), dash=document.querySelector('#v-dash');
        return {scrollWidth:main.scrollWidth,clientWidth:main.clientWidth,bottom:dash.getBoundingClientRect().bottom,height:innerHeight};
      });
      assert.ok(bounds.scrollWidth<=bounds.clientWidth+1,JSON.stringify(bounds));
      assert.ok((await page.locator('.act').boundingBox()).height<290,'Reel activity should stay compact by default');
      await page.screenshot({path:path.join(root,`tmp/ui-preview-${size.width}.png`)});
    }
    await page.locator('[data-config-edit="new-setup"]').click();
    await page.locator('#config-delete').click();await page.locator('#config-delete').click();
    await page.waitForFunction(()=>document.querySelectorAll('.config-row').length===2);
    assert.deepEqual(errors,[]);
    console.log('UI checks passed: setups, switching, tooltips, layouts, weather, hotkeys, Help and search cancellation.');
  } finally {await browser.close();}
})().catch(e=> {console.error(e);process.exitCode=1;});
