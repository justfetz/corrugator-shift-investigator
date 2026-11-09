// Issue #63 in a real browser: what the DOM stub in test_ui.cjs cannot measure. Not part of CI (no browser
// there); run it against a local server after any change to index.html, style.css or the card code:
//
//   python -m investigator.server --port 7860 &
//   node tests/browser_layout.cjs [http://127.0.0.1:7860] [--shots <folder>]
//
// Needs Playwright with a Chromium (`npm i playwright && npx playwright install chromium`, or a machine that
// already has both). It asserts the acceptance criteria of the issue: at 1440x1040 one answered card is above
// the fold; the skeleton is in the DOM in the same task as the submit; three canned questions make three
// cards with nothing disabled and no reload; offline is the default and the /api/ask payload is unchanged;
// at 390x844 the page is one column with the rail behind "Questions". --shots writes the pictures.
const assert=require('node:assert/strict');
let chromium;try{({chromium}=require('playwright'));}catch(e){console.error('Playwright is not installed here: npm i playwright && npx playwright install chromium');process.exit(2);}
const args=process.argv.slice(2), shotsAt=args.indexOf('--shots'), shots=shotsAt>=0?args[shotsAt+1]:null;
const base=(args.find((a,i)=>/^https?:/.test(a)&&i!==shotsAt+1)||'http://127.0.0.1:7860').replace(/\/$/,'');
const PAYLOAD=['question','day','shift','corrupt','session_id','mode','scope','period'];
(async()=>{
 const browser=await chromium.launch({args:['--no-proxy-server']});
 const open=async(viewport,scale)=>{
  const page=await browser.newPage({viewport,deviceScaleFactor:scale}), seen={errors:[],asks:[],paths:new Set(),loads:0};
  page.on('console',m=>{if(m.type()==='error')seen.errors.push(m.text());});page.on('pageerror',e=>seen.errors.push(String(e)));
  page.on('request',r=>{const url=new URL(r.url());seen.paths.add(`${r.method()} ${url.pathname}`);if(url.pathname==='/api/ask')seen.asks.push(JSON.parse(r.postData()));if(r.isNavigationRequest())seen.loads++;});
  await page.goto(base+'/');await page.waitForFunction(()=>document.getElementById('scope-help').textContent.length>0);await page.waitForTimeout(400);   // /api/config is in and the idle tiles have had their turn
  return {page,seen};
 };
 const shot=async(page,name)=>{if(shots)await page.screenshot({path:`${shots}/${name}.png`});};
 const disabled=page=>page.evaluate(()=>[...document.querySelectorAll('button,input,select,textarea')].filter(e=>e.disabled).map(e=>e.id||e.textContent));

 // ---- desktop, 1440x1040
 let {page,seen}=await open({width:1440,height:1040},shots?2:1);
 assert.equal(await page.inputValue('#mode'),'offline','offline is the default agent');
 assert.deepEqual(await disabled(page),['rates-change'],'idle: only the rates placeholder is disabled');
 seen.asks.length=0;
 await page.fill('#question','Why was this shift slow?');
 // the skeleton: submit and read the card in the same task, before the browser can paint a frame
 const submit=await page.evaluate(()=>{const t0=performance.now();document.getElementById('ask').requestSubmit();const card=document.getElementById('card');
  return {cls:card.className,ms:performance.now()-t0,title:document.getElementById('card-question').textContent,status:document.getElementById('status').textContent,skeleton:getComputedStyle(document.getElementById('skeleton')).display,disabled:[...document.querySelectorAll('button,input,select')].filter(e=>e.disabled).map(e=>e.id)};});
 assert.equal(submit.cls,'card answer skeleton');assert.equal(submit.title,'Why was this shift slow?');assert.match(submit.status,/ · offline · querying…$/);assert.equal(submit.skeleton,'flex');
 assert.ok(submit.ms<16,`the skeleton took ${submit.ms.toFixed(1)} ms of script, inside one 16 ms frame`);
 assert.deepEqual(submit.disabled,['rates-change'],'nothing is disabled while the question is out');
 await page.waitForSelector('#card.done');await page.waitForTimeout(250);   // the trace table is built two frames after the answer (#33)
 const fold=await page.evaluate(()=>{const box=e=>{const b=e.getBoundingClientRect();return {top:Math.round(b.top),bottom:Math.round(b.bottom)};};const charts=[...document.querySelectorAll('#charts .chart')];
  return {viewport:innerHeight,scrollY:scrollY,title:box(document.querySelector('.card-title')),queries:box(document.getElementById('queries')),messages:box(document.getElementById('messages')),charts:charts.map(box),columns:new Set(charts.map(c=>Math.round(c.getBoundingClientRect().left))).size,tiles:box(document.getElementById('metrics')),tileCount:document.querySelectorAll('#metrics .metric').length,foot:box(document.querySelector('.card-foot')),status:document.getElementById('status').textContent,rail:{scroll:document.getElementById('rail').scrollHeight,client:document.getElementById('rail').clientHeight,bottom:Math.round(document.getElementById('rail').getBoundingClientRect().bottom)}};});
 console.log('1440x1040, "Why was this shift slow?" answered:',JSON.stringify(fold));
 assert.equal(fold.scrollY,0,'measured without scrolling');
 assert.ok(fold.messages.top>0&&fold.messages.top<fold.viewport,`#messages top ${fold.messages.top} is inside the ${fold.viewport}px viewport`);
 assert.equal(fold.charts.length,2);assert.equal(fold.columns,2,'the two charts sit side by side');
 assert.ok(fold.charts[1].bottom<=fold.viewport,`the second chart ends at ${fold.charts[1].bottom}, inside the viewport`);
 assert.ok(fold.title.top<fold.queries.top&&fold.queries.bottom<=fold.messages.top&&fold.messages.bottom<=fold.charts[0].top&&Math.max(...fold.charts.map(c=>c.bottom))<=fold.tiles.top&&fold.tiles.bottom<=fold.foot.top,'title line, query box, summary, charts, tiles, footer line, in that order');
 assert.equal(fold.tileCount,6);assert.ok(fold.foot.bottom<=fold.viewport,`the footer line with the exports ends at ${fold.foot.bottom}: nothing of the card frame is below the fold`);
 assert.ok(fold.rail.scroll<=fold.rail.client&&fold.rail.bottom<=fold.viewport,'the rail fits without scrolling');
 assert.match(fold.status,/^Shift 2 · Mon 14 Sep 2026 · offline · 6 queries · \d+ ms$/);
 await shot(page,'desktop');
 // three canned questions in a row: three cards, one open and two collapsed, no reload, nothing disabled
 for(const title of ['Show dry-end quality waste','Show speed to target','Is it bond or warp?']){await page.click(`#examples button[title="${title}"]`);await page.waitForFunction(q=>document.querySelector('#card.done')&&document.getElementById('card-question').textContent===q,title);}
 const thread=await page.evaluate(()=>({open:document.getElementById('card-question').textContent,earlier:[...document.querySelectorAll('#thread .earlier')].map(e=>[...e.children].map(c=>c.textContent).join(' · '))}));
 console.log('after three canned questions:',JSON.stringify(thread));
 assert.equal(thread.open,'Is it bond or warp?');assert.equal(thread.earlier.length,3);   // the two canned answers before it, and the typed question under them
 assert.match(thread.earlier[0],/^Show speed to target · Shift 2 · \d+ ms · Open$/);assert.match(thread.earlier[1],/^Show dry-end quality waste · Shift 2 · \d+ ms · Open$/);assert.match(thread.earlier[2],/^Why was this shift slow\? · Shift 2 · \d+ ms · Open$/);
 assert.deepEqual(await disabled(page),['rates-change']);assert.equal(seen.loads,1,'one page load');
 assert.equal(seen.asks.length,4);for(const body of seen.asks){assert.deepEqual(Object.keys(body),PAYLOAD);assert.equal(body.mode,'offline');}
 assert.deepEqual([...seen.paths].filter(p=>p.startsWith('POST')||p.includes('/api/')).sort(),['GET /api/config','POST /api/ask'],'no new server route');
 await shot(page,'desktop-thread');
 await page.click('#thread .earlier:last-child .open');await page.waitForFunction(()=>document.getElementById('card-question').textContent==='Why was this shift slow?');
 assert.equal(seen.asks.length,4,'opening an earlier answer asks nothing');
 await page.click('#theme');await page.waitForTimeout(100);await shot(page,'desktop-dark');
 assert.deepEqual(seen.errors,[]);await page.close();

 // ---- phone, 390x844
 ({page,seen}=await open({width:390,height:844},shots?2:1));
 const phone=async()=>page.evaluate(()=>({rail:getComputedStyle(document.getElementById('rail')).display,toggle:getComputedStyle(document.getElementById('rail-toggle')).display,columns:getComputedStyle(document.querySelector('.shell-grid')).gridTemplateColumns.split(' ').length,sideways:document.documentElement.scrollWidth>innerWidth,askTop:Math.round(document.getElementById('ask').getBoundingClientRect().top),cardTop:Math.round(document.getElementById('card').getBoundingClientRect().top+scrollY)}));
 let p=await phone();assert.deepEqual([p.rail,p.toggle,p.columns,p.sideways],['none','flex',1,false],'one column, the rail behind the Questions button');
 await page.click('#rail-toggle');assert.equal((await phone()).rail,'flex');await shot(page,'phone-questions');
 await page.click('#examples button[title^="Why was this shift slow"]');await page.waitForSelector('#card.done');await page.waitForTimeout(250);
 p=await phone();console.log('390x844, answered from the rail:',JSON.stringify(p));
 assert.deepEqual([p.rail,p.sideways],['none',false]);assert.ok(p.cardTop<200,`the card starts at ${p.cardTop}px: card first`);
 await page.evaluate(()=>scrollTo(0,0));await shot(page,'phone');
 assert.deepEqual(seen.errors,[]);await page.close();
 await browser.close();
 console.log('browser layout checks passed: one card above the fold at 1440x1040, skeleton in the same task as the submit, three cards without a reload or a disabled control, payload unchanged, one column on a phone.');
})().catch(error=>{console.error(error);process.exit(1);});
