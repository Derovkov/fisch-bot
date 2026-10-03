// Headless UI check; bridge is mocked, no Roblox capture or input.
const fs = require('node:fs');
const path = require('node:path');
const {pathToFileURL} = require('node:url');
const assert = require('node:assert/strict');
const {chromium} = require(process.env.FISCH_PLAYWRIGHT || 'playwright');
const root = path.resolve(__dirname,'..');
(async()=> {
  const meta = JSON.parse(fs.readFileSync(path.join(root,'tmp/ui_meta_fixture.json'),'utf8'));
  const browser = await chromium.launch({channel:'msedge',headless:true});
  try {
    const page = await browser.newPage({viewport:{width:1280,height:840}});
    await page.route(/^https?:/,route=>route.abort());
    const errors=[];page.on('pageerror',e=>errors.push(e.message));
    await page.addInitScript(meta=> {
      window.fixtureMeta=meta;meta.saved={...meta.defaults,rod:'Duskwire',owned:['Duskwire','Fabulous Rod']};
      const setups=[['steady','Steady Duskwire','Duskwire',.5],['crew','Crew · steady','Crew Rod',.35]];
      meta.profiles=setups.map(([id,name,rod,lookahead])=>({id,name,settings:{...meta.defaults,rod,lookahead,rod_enchants:{[rod]:[]}}}));
      window.mockState={running:false,state:'idle',caught:0,lost:0,casts:0,live:null,history:[],max_fish:20,started:null,calibrated:false,runs:[],logs:[],now:Date.now()/1000};
      window.savedSettings=null;
      window.pywebview={api:{
        get_meta:async()=>meta,get_state:async()=>window.mockState,
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
        start:async()=>({ok:true}),stop:async()=>{},
      }};
    },meta);
    await page.goto(pathToFileURL(path.join(root,'ui/index.html')).href);
    await page.evaluate(()=>window.dispatchEvent(new Event('pywebviewready')));
    await page.waitForFunction(()=>document.querySelectorAll('.config-row').length===2);
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
    console.log('UI checks passed: save/use/delete, queued switch/cancel, tooltip, active rod, 1280 and 1080px layouts.');
  } finally {await browser.close();}
})().catch(e=> {console.error(e);process.exitCode=1;});
