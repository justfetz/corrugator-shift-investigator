// Real-browser layout check for the shell (issue #63). The unit checks in test_ui.cjs run on a DOM stub with no
// layout, so the "nothing below the fold" criteria are measured here, in headless Chromium, against a running
// server:   python -m investigator.server --port 7860 &   node tests/layout_check.cjs [url] [screenshot-dir]
// Skips (exit 0) when Playwright is not installed; CI does not run it.
const assert=require('node:assert/strict');
let chromium; try{({chromium}=require('playwright'));}catch(e){console.log('layout check skipped: Playwright is not installed');process.exit(0);}
const url=process.argv[2]||'http://127.0.0.1:7860/', shots=process.argv[3]||null;
const canned=label=>{[...document.querySelectorAll('#examples .canned')].find(b=>b.textContent===label).click();};
const answered=()=>document.getElementById('card').className==='card answer';
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
   second_chart_bottom:Math.round(charts[1].getBoundingClientRect().bottom),chart_tops:[...charts].map(c=>Math.round(c.getBoundingClientRect().top)),queries:box('#queries'),tiles:box('#metrics'),footer:box('#card-foot'),rail:box('#rail')};});
 assert.equal(fit.scrollY,0);assert.ok(fit.messages_top>0&&fit.messages_top<fit.viewport,'#messages top inside the viewport');
 assert.ok(fit.second_chart_bottom<=fit.viewport,'the second chart ends inside the viewport');
 assert.equal(fit.chart_tops[0],fit.chart_tops[1],'the two charts sit side by side');
 assert.ok(fit.tiles.bottom<=fit.viewport&&fit.footer.bottom<=fit.viewport&&fit.rail.bottom<=fit.viewport,'tiles, footer and rail inside the viewport');
 assert.match(fit.meta,/^Shift 2 · Mon 14 Sep 2026 · offline · \d+ queries · \d+ ms$/);
 if(shots)await desktop.screenshot({path:`${shots}/desktop.png`});
 // Three canned questions in a row: three cards (one open, two collapsed), no reload, no disabled control.
 const loads=await desktop.evaluate(()=>performance.getEntriesByType('navigation').length);
 await desktop.evaluate(canned,'Speed to target');await desktop.evaluate(canned,'Dry-end waste');
 const during=await desktop.evaluate(disabledControls);
 await desktop.waitForFunction(()=>document.getElementById('card').className==='card answer'&&[...document.querySelectorAll('#earlier .earlier span')].every(s=>/ ms$/.test(s.textContent)));
 const three=await desktop.evaluate(()=>({open:document.getElementById('card-title').textContent,earlier:[...document.querySelectorAll('#earlier .earlier')].map(e=>e.innerText.replace(/\s*\n\s*/g,' | ')),navigations:performance.getEntriesByType('navigation').length}));
 three.disabled_during=during;three.disabled_after=await desktop.evaluate(disabledControls);
 assert.equal(three.open,'Dry-end waste');assert.equal(three.earlier.length,2);assert.equal(three.navigations,loads);assert.deepEqual([...during,...three.disabled_after],[]);
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
 if(shots)await phone.screenshot({path:`${shots}/phone.png`});
 assert.deepEqual(errors,[]);
 console.log(JSON.stringify({skeleton,fit,three,phone:{rail_closed:closed,rail_open:opened,...narrow}},null,1));
 console.log('layout check passed: 1440x1040 fits without scrolling; skeleton in the click task; three cards, nothing disabled; phone is one column with the rail behind Questions.');
 await browser.close();
})().catch(error=>{console.error(error);process.exit(1);});
