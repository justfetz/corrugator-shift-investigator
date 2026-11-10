// Real-browser layout check for the shell (issues #63, #86). The unit checks in test_ui.cjs run on a DOM stub with no
// layout, so the "nothing below the fold" and chat-flow criteria are measured here, in headless Chromium, against a
// running server:   python -m investigator.server --port 7860 &   node tests/layout_check.cjs [url] [screenshot-dir]
// Skips (exit 0) when Playwright is not installed; CI does not run it.
const assert=require('node:assert/strict');
let chromium; try{({chromium}=require('playwright'));}catch(e){console.log('layout check skipped: Playwright is not installed');process.exit(0);}
const url=process.argv[2]||'http://127.0.0.1:7860/', shots=process.argv[3]||null;
const canned=label=>{[...document.querySelectorAll('#examples .canned')].find(b=>b.textContent===label).click();};
const answered=()=>document.getElementById('card').className==='card answer';
const settled=()=>document.getElementById('card').className==='card answer'&&document.getElementById('card').getAttribute('data-reveal')!=='running';
const disabledControls=()=>[...document.querySelectorAll('button,input,select,textarea')].filter(e=>e.disabled&&e.id!=='rates-change').map(e=>e.id||e.textContent);
(async()=>{
 const browser=await chromium.launch({args:['--no-proxy-server']}), errors=[];
 const desktop=await browser.newPage({viewport:{width:1440,height:1040},deviceScaleFactor:1});
 desktop.on('pageerror',e=>errors.push(String(e)));desktop.on('console',m=>{if(m.type()==='error')errors.push(m.text());});
 await desktop.goto(url);await desktop.waitForFunction(()=>document.querySelectorAll('#examples .canned').length>0);
 // The skeleton: in the DOM in the task of the click, and still the card one animation frame later.
 const skeleton=await desktop.evaluate(()=>new Promise(resolve=>{
  const t0=performance.now();[...document.querySelectorAll('#examples .canned')].find(b=>b.textContent==='Why was this shift slow?').click();
  const sync=document.getElementById('card').className, blocks=document.querySelectorAll('#card .skel, #card .skel-chart').length;
  requestAnimationFrame(()=>resolve({sync,blocks,frame_ms:performance.now()-t0,meta:document.getElementById('card-meta').textContent}));}));
 assert.equal(skeleton.sync,'card answer skeleton');assert.ok(skeleton.blocks>=4);
 await desktop.waitForFunction(answered);await desktop.waitForTimeout(250);
 const fit=await desktop.evaluate(()=>{const box=s=>{const r=document.querySelector(s).getBoundingClientRect();return {top:Math.round(r.top),bottom:Math.round(r.bottom)};},charts=document.querySelectorAll('#charts .chart');
  return {viewport:innerHeight,scrollY,title:document.getElementById('card-title').textContent,meta:document.getElementById('card-meta').textContent,messages_top:Math.round(document.querySelector('#messages').getBoundingClientRect().top),charts:charts.length,
   second_chart_bottom:Math.round(charts[1].getBoundingClientRect().bottom),chart_tops:[...charts].map(c=>Math.round(c.getBoundingClientRect().top)),queries:box('#queries'),tiles:box('#metrics'),footer:box('#card-foot'),rail:box('#rail'),pane:box('#transcript')};});
 assert.equal(fit.scrollY,0);assert.ok(fit.messages_top>0&&fit.messages_top<fit.viewport,'#messages top inside the viewport');
 assert.ok(fit.second_chart_bottom<=fit.pane.bottom,'the second chart ends inside the visible pane');
 assert.equal(fit.chart_tops[0],fit.chart_tops[1],'the two charts sit side by side');
 assert.ok(fit.tiles.bottom<=fit.pane.bottom&&fit.footer.bottom<=fit.pane.bottom&&fit.rail.bottom<=fit.viewport,'tiles and footer inside the visible pane, the rail inside the viewport');
 assert.match(fit.meta,/^Shift 2 · Mon 14 Sep 2026 · offline · \d+ queries · \d+ ms$/);
 await desktop.waitForFunction(settled,null,{timeout:8000});   // the renders show the answer after its reveal (#65)
 if(shots)await desktop.screenshot({path:`${shots}/desktop.png`});
 // Three canned questions in a row: three cards (one open, two collapsed), no reload, no disabled control.
 const loads=await desktop.evaluate(()=>performance.getEntriesByType('navigation').length);
 await desktop.evaluate(canned,'Speed to target');await desktop.evaluate(canned,'Dry-end waste');
 const during=await desktop.evaluate(disabledControls);
 await desktop.waitForFunction(()=>document.getElementById('card').className==='card answer'&&[...document.querySelectorAll('#earlier .earlier-text span')].every(s=>/ ms$/.test(s.textContent)));
 const three=await desktop.evaluate(()=>({open:document.getElementById('card-title').textContent,earlier:[...document.querySelectorAll('#earlier .earlier-text')].map(e=>e.innerText.replace(/\s*\n\s*/g,' | ')),navigations:performance.getEntriesByType('navigation').length}));
 three.disabled_during=during;three.disabled_after=await desktop.evaluate(disabledControls);
 await desktop.waitForFunction(settled,null,{timeout:8000});await desktop.waitForTimeout(100);
 three.follow=await desktop.evaluate(()=>{const p=document.getElementById('transcript').getBoundingClientRect(),t=document.getElementById('card-title').getBoundingClientRect();return {pill_hidden:document.getElementById('new-pill').hidden,title_in_pane:t.top>=p.top&&t.bottom<=p.bottom};});
 assert.deepEqual(three.follow,{pill_hidden:true,title_in_pane:true},'questions asked in quick succession, cards landing behind the open one: the pane still follows, no pill (#86)');
 assert.equal(three.open,'Dry-end waste');assert.equal(three.earlier.length,2);assert.equal(three.navigations,loads);assert.deepEqual([...during,...three.disabled_after],[]);
 await desktop.waitForFunction(settled,null,{timeout:8000});   // the renders show the answer after its reveal (#65)
 if(shots)await desktop.screenshot({path:`${shots}/desktop-three.png`,fullPage:true});
 // Phone: one column, the rail behind "Questions", the card first.
 const phone=await browser.newPage({viewport:{width:390,height:844},deviceScaleFactor:1});
 phone.on('pageerror',e=>errors.push(String(e)));
 await phone.goto(url);await phone.waitForFunction(()=>document.querySelectorAll('#examples .canned').length>0);
 const closed=await phone.evaluate(()=>getComputedStyle(document.getElementById('rail')).display);
 await phone.click('#rail-toggle');const opened=await phone.evaluate(()=>getComputedStyle(document.getElementById('rail')).display);
 if(shots)await phone.screenshot({path:`${shots}/phone-questions.png`});
 await phone.click('#examples .canned');await phone.waitForFunction(answered);await phone.waitForTimeout(250);
 const narrow=await phone.evaluate(()=>({rail:getComputedStyle(document.getElementById('rail')).display,card_top:Math.round(document.getElementById('card').getBoundingClientRect().top),page_width:document.documentElement.scrollWidth,columns:getComputedStyle(document.getElementById('charts')).gridTemplateColumns.split(' ').length}));
 assert.equal(closed,'none');assert.equal(opened,'flex');assert.equal(narrow.rail,'none');assert.equal(narrow.page_width,390);assert.equal(narrow.columns,1);assert.ok(narrow.card_top<300);
 await phone.waitForFunction(settled,null,{timeout:8000});   // the renders show the answer after its reveal (#65)
 if(shots)await phone.screenshot({path:`${shots}/phone.png`});
 // ---- Issue #86: the chat flow. Three questions typed into the ask bar (Enter) without touching the scrollbar, at
 // 1280x720 and 390x844: the page never scrolls, the ask bar and the rail never move, each answer is in view when it
 // finishes (its title line inside the pane), earlier cards move up whole, and the offline time stays two digits.
 const chat={};
 const pane=()=>({top:Math.round(document.getElementById('transcript').getBoundingClientRect().top),bottom:Math.round(document.getElementById('transcript').getBoundingClientRect().bottom),scroll:Math.round(document.getElementById('transcript').scrollTop)});
 const frame=()=>{const r=s=>{const e=document.querySelector(s),x=e.getBoundingClientRect();return [Math.round(x.top),Math.round(x.bottom),Math.round(x.left),Math.round(x.width)];};
  const rail=document.getElementById('rail'),p=document.getElementById('transcript').getBoundingClientRect(),t=document.getElementById('card-title').getBoundingClientRect();
  return {scrollY,doc:document.documentElement.scrollHeight,viewport:innerHeight,ask:r('#ask'),rail:getComputedStyle(rail).display==='none'?null:r('#rail'),rail_scrolls:rail.scrollHeight>rail.clientHeight,
   title_in_pane:t.top>=p.top&&t.bottom<=p.bottom,title:document.getElementById('card-title').textContent,meta:document.getElementById('card-meta').textContent,pane_scroll:Math.round(document.getElementById('transcript').scrollTop),earlier:document.querySelectorAll('#earlier .earlier.drawn').length};};
 for(const [name,w,h] of [['desktop',1280,720],['phone',390,844]]){
  const page=await browser.newPage({viewport:{width:w,height:h},deviceScaleFactor:1}), calls=[];
  page.on('pageerror',e=>errors.push(String(e)));page.on('console',m=>{if(m.type()==='error')errors.push(m.text());});
  page.on('request',q=>calls.push([q.method(),new URL(q.url()).pathname,q.resourceType(),!!q.redirectedFrom()]));
  await page.goto(url);await page.waitForFunction(()=>document.querySelectorAll('#examples .canned').length>0);await page.waitForTimeout(200);
  const start=await page.evaluate(frame), steps=[];
  for(const q of ['Why was this shift slow?','Show speed to target','Show dry-end quality waste']){
   await page.click('#question');await page.fill('#question',q);const before=calls.length;await page.keyboard.press('Enter');
   await page.waitForFunction(settled,null,{timeout:8000});await page.waitForTimeout(150);
   const fetched=calls.slice(before).filter(c=>c[2]==='fetch'&&!c[3]);   // the /q GET; its redirect to the canonical address is the same fetch
   steps.push({...await page.evaluate(frame),fetches:fetched.map(c=>c[1])});
  }
  for(const s of steps){
   assert.equal(s.scrollY,0,`${name}: the page does not scroll`);assert.equal(s.doc,h,`${name}: the page is one window tall`);
   assert.deepEqual(s.ask,start.ask,`${name}: the ask bar never moves`);assert.ok(s.ask[1]<=h,`${name}: the ask bar is on screen`);
   assert.deepEqual(s.rail,start.rail,`${name}: the rail never moves`);assert.equal(s.title_in_pane,true,`${name}: the answer is in view when it finishes`);
   assert.match(s.meta,/ · offline · \d+ quer(y|ies) · \d{1,2} ms$/,`${name}: the offline time on the title line is still two digits`);
   assert.deepEqual(s.fetches,['/q'],`${name}: no new fetch on the hot path: one GET per question`);
  }
  assert.deepEqual(steps.map(s=>s.title),['Why was this shift slow?','Show speed to target','Show dry-end quality waste']);assert.deepEqual(steps.map(s=>s.earlier),[0,1,2],'earlier cards move up whole');
  assert.ok(steps[1].pane_scroll>steps[0].pane_scroll&&steps[2].pane_scroll>steps[1].pane_scroll,'the pane followed each new answer: older content moved up');
  if(name==='desktop')assert.equal(start.rail_scrolls,false,'at 1280x720 the rail fits: it does not scroll at all');
  assert.equal(await page.evaluate(()=>document.activeElement&&document.activeElement.id),'question','after send, focus stays in the ask bar');
  if(shots)await page.screenshot({path:`${shots}/${name}-chat.png`});
  // Scroll up during a reveal: the pane stops following, the pill appears, clicking it lands on the newest content.
  await page.fill('#question','Is it bond or warp?');await page.keyboard.press('Enter');
  await page.waitForFunction(()=>document.getElementById('card').getAttribute('data-reveal')==='running',null,{timeout:8000});
  // Small wheel notches, as a trackpad or a smooth-scrolling mouse sends them: each one moves the pane up and stays.
  const box=await page.locator('#transcript').boundingBox();await page.mouse.move(box.x+box.width/2,box.y+box.height/2);
  const tops=[(await page.evaluate(pane)).scroll];
  for(let k=0;k<6;k++){await page.mouse.wheel(0,-40);await page.waitForTimeout(40);tops.push((await page.evaluate(pane)).scroll);}
  assert.ok(tops.every((v,k)=>k===0||v<tops[k-1]),`${name}: every small step up stays up, never pulled back: ${tops}`);
  await page.waitForFunction(()=>!document.getElementById('new-pill').hidden,null,{timeout:3000});
  const paused={...await page.evaluate(pane),tops};
  if(shots&&name==='desktop')await page.screenshot({path:`${shots}/${name}-new-pill.png`});
  await page.click('#new-pill');await page.waitForTimeout(100);
  const resumed=await page.evaluate(()=>({pill_hidden:document.getElementById('new-pill').hidden,title_in_pane:(()=>{const p=document.getElementById('transcript').getBoundingClientRect(),t=document.getElementById('card-title').getBoundingClientRect();return t.top>=p.top&&t.bottom<=p.bottom;})(),scroll:Math.round(document.getElementById('transcript').scrollTop)}));
  assert.equal(resumed.pill_hidden,true);assert.equal(resumed.title_in_pane,true,'the pill lands on the newest answer');assert.ok(resumed.scroll>paused.scroll);
  await page.waitForFunction(settled,null,{timeout:8000});
  // Reload mid-thread: the thread above the addressed card comes back from GETs of the cards' own addresses; /api/ask is not called.
  const address=await page.evaluate(()=>location.pathname+location.search);
  calls.length=0;await page.reload();await page.waitForFunction(()=>document.querySelectorAll('#earlier .earlier.drawn').length===3&&document.getElementById('card').className==='card answer',null,{timeout:8000});
  const reloaded=await page.evaluate(()=>({open:document.getElementById('card-title').textContent,earlier:[...document.querySelectorAll('#earlier .earlier-text strong')].map(e=>e.textContent),address:location.pathname+location.search}));
  const gets=calls.filter(c=>c[1].startsWith('/corr/')&&c[2]==='fetch'), asks=calls.filter(c=>c[1]==='/api/ask');   // the page's own document is a /corr/ GET too; the cards are fetches
  assert.equal(reloaded.address,address);assert.equal(reloaded.open,'Is it bond or warp?');assert.deepEqual(reloaded.earlier,['Show dry-end quality waste','Show speed to target','Why was this shift slow?'],'newest first in the DOM, oldest at the top on screen');
  assert.equal(asks.length,0,'no /api/ask for the restored cards');assert.ok(gets.length===4&&gets.every(c=>c[0]==='GET'),JSON.stringify(calls));
  await page.waitForFunction(settled,null,{timeout:8000});
  if(shots&&name==='desktop')await page.screenshot({path:`${shots}/${name}-reload.png`});
  chat[name]={start:{ask:start.ask,rail:start.rail,rail_scrolls:start.rail_scrolls},steps:steps.map(s=>({title:s.title,meta:s.meta,pane_scroll:s.pane_scroll,title_in_pane:s.title_in_pane})),pill:{paused,resumed},reload:{...reloaded,corr_gets:gets.length,api_ask:asks.length}};
  await page.close();
 }
 assert.deepEqual(errors,[]);
 console.log(JSON.stringify({skeleton,fit,three,phone:{rail_closed:closed,rail_open:opened,...narrow},chat},null,1));
 console.log('layout check passed: 1440x1040 fits without scrolling; skeleton in the click task; three cards, nothing disabled; phone is one column with the rail behind Questions; at 1280x720 and 390x844 the page never scrolls, the ask bar and rail never move, each answer is in view, the pill stops and resumes the follow, a reload restores the thread by GET without /api/ask.');
 await browser.close();
})().catch(error=>{console.error(error);process.exit(1);});
