// Real-browser check that card text stays inside the card (issue #177). The unit checks in test_ui.cjs run on a DOM stub
// with no layout, so a phrase that spills past the card (a delta chip read "▼ −0.03 % of yield after trim and shear" and ran
// 113 px past a 390 px card) is measured here, in headless Chromium, against a running server:
//   python -m investigator.server --port 7860 &   node tests/card_layout_check.cjs [base-url] [screenshot-dir] [--questions file]
// It asks every canned question under the selected shift and All at 390, 768 and 1440 px, then each question of the optional
// file (one per line) by its address at 390 px. It fails listing every element that ends past the card's right edge or holds
// a line it cannot wrap. Tables, code, charts and the query list scroll on their own and are not counted.
// Skips (exit 0) when Playwright is not installed; CI does not run it.
const assert=require('node:assert/strict'), fs=require('node:fs');
let chromium; try{({chromium}=require('playwright'));}catch(e){console.log('card layout check skipped: Playwright is not installed');process.exit(0);}
const args=process.argv.slice(2), qi=args.indexOf('--questions'), file=qi>=0?args.splice(qi,2)[1]:null;
const base=args[0]||'http://127.0.0.1:7860', shots=args[1]||null, day='2026-09-14';
const settled=()=>document.getElementById('card').className==='card answer'&&document.getElementById('card').getAttribute('data-reveal')!=='running';
function spills(){
 const card=document.getElementById('card'), edge=card.getBoundingClientRect().right, out=[];
 for(const el of card.querySelectorAll('*')){
  const r=el.getBoundingClientRect(), cs=getComputedStyle(el);
  if(!r.width||!r.height||cs.visibility==='hidden'||cs.display==='none'||el.closest('pre,svg,.chart,table,.queries,.trace,details:not([open])'))continue;
  const past=r.right-edge>1, unwrapped=el.children.length===0&&el.scrollWidth-el.clientWidth>1&&!/auto|scroll|hidden/.test(cs.overflowX);
  if(past||unwrapped)out.push({tag:el.tagName.toLowerCase(),class:String(el.className||'').slice(0,40),past:Math.round(r.right-edge),text:(el.textContent||'').trim().slice(0,90)});
 }
 return out.filter((o,i,a)=>!a.slice(0,i).some(p=>p.text===o.text));
}
(async()=>{
 const browser=await chromium.launch({args:['--no-proxy-server']}), found=[];
 const record=async(page,where)=>{const bad=await page.evaluate(spills);if(bad.length){found.push({...where,bad:bad.slice(0,3)});if(shots)await page.screenshot({path:`${shots}/${where.width}-${String(where.label).replace(/\W+/g,'_').slice(0,60)}.png`});}};
 for(const [width,height] of [[390,844],[768,1024],[1440,1040]]){
  for(const scope of ['shift','day']){
   const page=await browser.newPage({viewport:{width,height},deviceScaleFactor:1});
   await page.goto(`${base}/corr/1/day/${day}${scope==='shift'?'/shift/2':''}`);
   await page.waitForFunction(()=>document.querySelectorAll('.canned[data-question]').length>0);
   for(const label of await page.evaluate(()=>[...document.querySelectorAll('.canned[data-question]')].map(b=>b.textContent))){
    await page.evaluate(l=>[...document.querySelectorAll('.canned[data-question]')].find(b=>b.textContent===l).click(),label);
    const done=await page.waitForFunction(settled,null,{timeout:15000}).then(()=>true,()=>false);await page.waitForTimeout(120);
    if(!done)console.log(`note: '${label}' at ${width} px under ${scope} did not settle in 15 s; measured as it stands`);
    await record(page,{width,scope,label});
   }
   await page.close();
  }
 }
 if(file){
  const page=await browser.newPage({viewport:{width:390,height:844},deviceScaleFactor:1});
  for(const q of fs.readFileSync(file,'utf8').split('\n').map(s=>s.trim()).filter(s=>s&&!s.startsWith('#'))){
   await page.goto(`${base}/q?day=${day}&shift=2&scope=shift&period=day&q=${encodeURIComponent(q)}`);
   await page.waitForFunction(()=>{const c=document.getElementById('card');return c&&c.getAttribute('data-reveal')!=='running'&&/answer/.test(c.className)&&!/skeleton/.test(c.className);},null,{timeout:15000}).catch(()=>{});
   await page.waitForTimeout(120);await record(page,{width:390,scope:'shift',label:q});
  }
  await page.close();
 }
 await browser.close();
 assert.deepEqual(found,[],JSON.stringify(found,null,1));
 console.log(`card layout checks passed: every canned card at 390, 768 and 1440 px under one shift and All${file?', and each question of '+file+' at 390 px':''}, nothing past the card's edge.`);
})().catch(e=>{console.error(e.message);process.exit(1);});
