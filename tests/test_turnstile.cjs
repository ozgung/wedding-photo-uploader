const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const source = fs.readFileSync('app/static/app.js', 'utf8');

async function page({renderFails=false, sessionFails=false, friendly=false, browserLanguages=[], saved}={}) {
  const nodes = new Map();
  const storage = new Map(saved?[['wedding-language',saved]]:[]);
  const root = {};
  let script, widget, submitted;
  const node = id => {
    if (!nodes.has(id)) nodes.set(id, {hidden:false, disabled:true, value:'',
      classList:{toggle(){},contains(){return false;}}, setAttribute(){}, replaceChildren(){}});
    return nodes.get(id);
  };
  const store = {getItem:k=>storage.get(k),setItem:(k,v)=>storage.set(k,v),removeItem:k=>storage.delete(k)};
  const context = vm.createContext({URLSearchParams,localStorage:store,sessionStorage:store,
    location:{hash:friendly?'':'#invite=test-invitation',pathname:friendly?'/alex-sam-120627':'/'},history:{replaceState(){}},
    navigator:{languages:browserLanguages},
    document:{getElementById:node,documentElement:root,querySelectorAll:()=>[],createElement:()=>({}),head:{append:s=>{script=s;}}},
    window:{addEventListener(){},turnstile:{ready(){throw Error('Async scripts cannot use ready');},
      render(selector,options){if(renderFails)throw Error('Render failed');widget=options;},reset(){}}},
    fetch:async (url,options)=>{
      let data,ok=true;
      if(url==='/api/config') data={open:true,sitekey:'test-key',title:'Alex & Sam',monogram:'A&S',event_date:'2027-06-12',last_day:'2027-06-19',default_lang:'en',languages:['en','tr'],privacy_url:'',contact_email:''};
      else if(url.startsWith('/static/locales/')) data=JSON.parse(fs.readFileSync('app/static'+url.slice(7),'utf8'));
      else if(options.method==='GET'){ok=false;data={detail:'invitation_required'};}
      else {submitted=JSON.parse(options.body);ok=!sessionFails;data=sessionFails?{detail:'bot_check_failed'}:{csrf:'server-approved-session'};}
      return {ok,json:async()=>data};
    }});
  vm.runInContext(source,context);
  // Drain boot's config and session fetches before delivering the script load.
  await new Promise(resolve=>setImmediate(resolve));
  return {node,root,script,widget:()=>widget,submitted:()=>submitted};
}

test('async Turnstile loader renders and only server approval enables uploads',async()=>{
  const p=await page();
  p.script.onload();
  assert.equal(p.widget().action,'wedding_entry');
  assert.equal(p.node('choose').disabled,true);
  await p.widget().callback('test-proof');
  assert.deepEqual(p.submitted(),{invite:'test-invitation',turnstile:'test-proof'});
  assert.equal(p.node('choose').disabled,false);
  assert.equal(p.node('challenge').hidden,true);
});

test('server rejection keeps upload access disabled',async()=>{
  const p=await page({sessionFails:true});p.script.onload();
  await p.widget().callback('rejected-proof');
  assert.equal(p.node('choose').disabled,true);
  assert.match(p.node('message').textContent,/security check did not finish/);
});

test('friendly typed URL submits the configured event alias',async()=>{
  const p=await page({friendly:true});p.script.onload();
  await p.widget().callback('test-proof');
  assert.deepEqual(p.submitted(),{event:'alex-sam-120627',turnstile:'test-proof'});
  assert.equal(p.node('choose').disabled,false);
});

test('script and render failures show an error instead of leaving checking text',async()=>{
  for(const renderFails of [false,true]){
    const p=await page({renderFails});
    if(renderFails)p.script.onload();else p.script.onerror();
    assert.match(p.node('message').textContent,/security check is unavailable/);
    assert.equal(p.node('choose').disabled,true);
  }
});

test('guest language follows a saved choice, then the browser, then the configured default',async()=>{
  assert.equal((await page()).root.lang,'en');
  assert.equal((await page({browserLanguages:['fr-FR','tr-TR']})).root.lang,'tr');
  const p=await page({browserLanguages:['tr-TR'],saved:'en'});
  assert.equal(p.root.lang,'en');
  p.script.onerror();
  assert.match(p.node('message').textContent,/security check is unavailable/);
  const turkish=await page({browserLanguages:['tr']});turkish.script.onerror();
  assert.match(turkish.node('message').textContent,/Güvenlik kontrolüne ulaşılamıyor/);
});
