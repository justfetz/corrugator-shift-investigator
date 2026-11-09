// Unit-level UI checks with a minimal DOM stub; no browser session or visual QA.
const fs=require('node:fs'), vm=require('node:vm'), assert=require('node:assert/strict');
const html=fs.readFileSync('src/investigator/web/index.html','utf8');
class Element {
 constructor(tag){this.tag=tag;this.children=[];this.value='';this.dataset={};this.disabled=false;this.checked=false;this.textContent='';this.style={};this.events={};this.selectedOptions=[{textContent:'Week ending on date'}];}
 append(...items){this.children.push(...items);}
 replaceChildren(...items){this.children=items;}
 setAttribute(key,value){this[key]=value;}
 addEventListener(name,fn){this.events[name]=fn;}
 querySelector(){return null;}
 click(){}
}
const elements=Object.fromEntries([...html.matchAll(/id="([^"]+)"/g)].map(m=>[m[1],new Element(m[1])]));
Object.assign(elements.day,{value:'2026-09-14'});elements.period.value='day';elements.shift.value='2';elements.scope.value='shift';elements.mode.value='offline';
const document={getElementById:id=>{assert.ok(elements[id],`Unknown control ${id}`);return elements[id];},createElement:tag=>new Element(tag),createElementNS:(_,tag)=>new Element(tag),querySelectorAll:()=>[]};
const context={document,fetch:async()=>({ok:true,json:async()=>({days:['2026-09-01','2026-09-30'],token:'test',evals:{badge:'160/160 golden · 12 ms p50',ran_at:'2026-11-09T19:21Z',commit:'abc1234'}})}),window:{addEventListener(){}},console,setTimeout,URL,Blob,AbortController};
vm.createContext(context);vm.runInContext(fs.readFileSync("src/investigator/web/trail.js","utf8"),context);vm.runInContext(fs.readFileSync("src/investigator/web/app.js","utf8"),context);   // trail.js first, as index.html loads them
vm.runInContext('updateScope()',context);assert.equal(elements['scope-help'].textContent,'Monday 14 Sep 2026 · shift 2 · 15:00–23:00 · compared with Friday 11 Sep');
elements.scope.value='day';vm.runInContext('updateScope()',context);assert.match(elements['scope-help'].textContent,/all three shifts · 24 hours from 07:00 · compared with Friday 11 Sep/);
elements.scope.value='shift';vm.runInContext('updateScope()',context);assert.match(elements['scope-help'].textContent,/shift 2/);
elements.mode.value='openai';vm.runInContext('updateScope()',context);assert.equal(elements['key-label'].hidden,false);
// A week, month or 4-4-5 period keeps the question box, the agent select and the live key: the period tools answer
// questions about the selected window; the deterministic report moves to its own button.
elements.period.value='month';elements.period.events.change();assert.equal(elements.mode.value,'openai');assert.equal(elements['key-label'].hidden,false);
elements.period.value='week';vm.runInContext('updateScope()',context);assert.equal(elements.question.disabled,false);assert.equal(elements.mode.disabled,false);assert.equal(elements.report.hidden,false);assert.equal(elements.submit.textContent,'Ask');assert.match(elements['mode-help'].textContent,/^Ask about the selected week/);
elements.period.value='day';vm.runInContext('updateScope()',context);assert.equal(elements.question.disabled,false);assert.equal(elements.report.hidden,true);
elements.mode.value='offline';
// Anthropic locally: the key label and badge follow the selected provider, and the key travels under its own header name.
elements.mode.value='anthropic';vm.runInContext('updateScope()',context);assert.equal(elements['key-label'].hidden,false);assert.match(elements['key-label-text'].textContent,/^Anthropic API key/);assert.match(elements['mode-badge'].textContent,/Anthropic selected/);
elements.mode.value='openai';vm.runInContext('updateScope()',context);assert.match(elements['key-label-text'].textContent,/^OpenAI API key/);
assert.equal(vm.runInContext("JSON.stringify(Object.values(PROVIDERS).map(p=>p.header))",context),'["X-OpenAI-Key","X-Anthropic-Key"]');
// Public demo with only the Anthropic server key: that option is relabelled, the OpenAI option is removed, Anthropic is preselected.
const options={openai:new Element('option'),anthropic:new Element('option')};for(const [v,o] of Object.entries(options)){o.value=v;o.removed=false;o.remove=()=>{o.removed=true;};}
elements.mode.querySelector=sel=>{const v=sel.match(/value="(\w+)"/)[1];return options[v]&&!options[v].removed?options[v]:null;};
vm.runInContext("config={public:true,live_modes:['anthropic'],live_model_available:true,days:['2026-09-01','2026-09-30'],token:'t'};applyPublicMode()",context);
assert.equal(elements.mode.value,'offline');/* issue #42: offline is the default even with a server key */assert.equal(options.anthropic.textContent,'Anthropic / server key');assert.equal(options.openai.removed,true);assert.equal(elements['key-label'].hidden,true);assert.match(elements['mode-help'].textContent,/Anthropic/);
elements.mode.querySelector=()=>null;
// Public demo: with the server's model available the default stays offline (issue #42) and the model is a choice; without it the OpenAI option is gone and offline is forced.
vm.runInContext("config={public:true,live_model_available:true,days:['2026-09-01','2026-09-30'],token:'t'};applyPublicMode()",context);assert.equal(elements.mode.value,'offline');assert.equal(elements['key-label'].hidden,true);assert.match(elements['mode-badge'].textContent,/model available/);
elements.mode.value='openai';vm.runInContext("config={public:true,live_model_available:false,days:['2026-09-01','2026-09-30'],token:'t'};applyPublicMode()",context);assert.equal(elements.mode.value,'offline');assert.equal(elements.mode.disabled,true);
vm.runInContext("config={days:['2026-09-01','2026-09-30'],token:'test'}",context);elements.mode.disabled=false;elements.mode.value='offline';
const row={observed_shift_fpm:600,speed_to_target_pct:75,valid_setups:60,paper_changes:30,maintenance_pct:3,operator_pct:1,dry_end_pct:1.2,trim_pct:2,shear_pct:.5,throughput_in:94,lineal_per_setup:4800};
context.result={run_id:'test',session_id:'session',production_day:'2026-09-14',selected_shifts:[2],mode:'offline',status:'complete',context:{shift:2},usage:{tool_calls:1,model_calls:0},trace:[{tool:'get_shift_kpis',ok:true,elapsed_ms:1,data:{shift:2,coverage:'complete',rows:[row]}}],tables:[{title:'get_shift_notes',shift:2,coverage:'complete',rows:[{notes:'<script>untrusted</script>'}]}],charts:[{title:'Output heatmap',type:'calendar_heatmap',coverage:'complete',unit:'estimated good sq ft',rows:[{day:'2026-09-14',available:true,estimated_good_sqft:1000000,gross_sqft:1020000,speed_to_target_pct:75,downtime_pct:4,maintenance_pct:3,operator_pct:1,dry_end_pct:1.2,trim_pct:2,shear_pct:.5,paper_changes:27,valid_setups:60,heavy_hitter:'Upper knife / Knife jam',heavy_hitter_minutes:12,heavy_hitter_stops:3}]},{shift:2,coverage:'complete',unit:'ft/min',x:'wet_end_id',y:'actual_fpm',rows:[{wet_end_id:'W2-01',actual_fpm:600,target_fpm:800,speed_to_target_pct:75,grade:'200-C'}]}]};
const first=context.result.charts[0].rows[0];
context.result.charts[0].rows.push({...first,day:'2026-09-15',estimated_good_sqft:1500000},{...first,day:'2026-09-16',estimated_good_sqft:2000000},{day:'2026-09-17',available:false});
vm.runInContext('showResult(result)',context);assert.equal(elements.tables.children.length,1);assert.equal(elements.charts.children.length,1);
// issue #63: the eleven numbers of the shift KPI row sit on six tiles, each leading with one number and carrying its companion beneath.
assert.deepEqual(elements.metrics.children.map(t=>t.children.map(c=>c.children.length?c.children.map(x=>x.textContent).join(' '):c.textContent)),[['Speed','600.0 ft/min','75.0% of target'],['Maintenance','3.00 %','operator 1.00%'],['Trim','2.00 %','shear 0.50%'],['Dry-end waste','1.20 %','above its flag'],['Dry-end setups','60','30 paper changes'],['Throughput','94.0 in','4,800 ft lineal / setup']]);
assert.deepEqual(elements.metrics.children.map(t=>/\bwarning\b/.test(t.className)),[true,true,false,true,false,false]);   // below target, maintenance above its target, dry-end above its flag; the tile text prints no threshold
vm.runInContext('showResult(result)',context);assert.equal(elements.charts.children.length,1);assert.equal(elements.tables.children.length,1);assert.equal(elements.trace.children.length,1);   // a second answer replaces the first one's evidence, never stacks on it
const heatmap=elements.heatmaps.children[0];
const cells=heatmap.children.find(e=>e.className==='heatmap-grid').children.filter(e=>e.tag==='button');
assert.deepEqual(cells.slice(0,3).map(e=>e.style.backgroundColor),['rgba(8, 127, 117, 0.220)','rgba(8, 127, 117, 0.610)','rgba(8, 127, 117, 1.000)']);
assert.equal(cells[3].style.backgroundColor,undefined);assert.match(cells[3].className,/missing/);
// issue #36: the day number flips to white only on cells deep enough for white to read better than black (alpha 1 here, not .61).
assert.deepEqual(cells.slice(0,3).map(e=>/\bdeep\b/.test(e.className)),[false,false,true]);
assert.ok(vm.runInContext('heatLuminance(.96)',context)>.179&&vm.runInContext('heatLuminance(.965)',context)<.179);   // the flip sits where both inks clear 4.5:1
for(const phrase of ['estimated good','gross','speed','downtime','maintenance','operator','dry-end','trim','shear','27 paper changes','Dry-end setups: 60','heavy hitter Upper knife / Knife jam'])assert.ok(cells[0].title.includes(phrase),phrase);
assert.equal(cells[0]['aria-label'],cells[0].title);
const tip=heatmap.children.find(e=>e.className==='heatmap-tip');
for(const event of ['mouseenter','focus','click']){cells[1].events[event]();assert.equal(tip.textContent,cells[1].title);}
assert.ok(heatmap.children.some(e=>e.className==='heatmap-legend'));
assert.ok(html.indexOf('id="heatmaps"')<html.indexOf('id="metrics"'));
context.result.charts[0].rows=[first];vm.runInContext('showResult(result)',context);
assert.equal(elements.heatmaps.children.length,1);
assert.equal(elements.heatmaps.children[0].children.find(e=>e.className==='heatmap-grid').children.find(e=>e.tag==='button').style.backgroundColor,'rgba(8, 127, 117, 1.000)');
vm.runInContext('reset()',context);assert.equal(elements.card.className,'card answer idle');assert.equal(elements.pdf.disabled,false);assert.equal(elements.tables.children.length,0);   // the exports sit on the card's footer line, which only a finished answer shows; nothing is disabled (#63)
assert.equal(elements.heatmaps.children.length,0);

// ---- Scope sentence: the plain-English read-back for each period, with the comparison base or its absence.
const days=['2026-08-03','2026-08-04','2026-09-11','2026-09-14','2026-09-30'];
const sentence=state=>vm.runInContext(`scopeSentence(${JSON.stringify(state)})`,context);
assert.equal(sentence({day:'2026-09-14',period:'day',shift:2,scope:'shift',days,first_day:'2026-08-03'}),'Monday 14 Sep 2026 · shift 2 · 15:00–23:00 · compared with Friday 11 Sep');
assert.equal(sentence({day:'2026-09-14',period:'day',shift:3,scope:'day',days,first_day:'2026-08-03'}),'Monday 14 Sep 2026 · all three shifts · 24 hours from 07:00 · compared with Friday 11 Sep');
assert.equal(sentence({day:'2026-09-14',period:'week',shift:1,scope:'shift',days,first_day:'2026-08-03'}),'Week of Mon 14 – Fri 18 Sep 2026 · shift 1 · compared with the week of Mon 7 – Fri 11 Sep 2026');
assert.equal(sentence({day:'2026-09-02',period:'week',shift:1,scope:'day',days,first_day:'2026-08-03'}),'Week of Mon 31 Aug – Fri 4 Sep 2026 · all three shifts · compared with the week of Mon 24 – Fri 28 Aug 2026');
assert.equal(sentence({day:'2026-09-14',period:'month',shift:2,scope:'shift',days,first_day:'2026-08-03'}),'September 2026 · shift 2 · compared with August 2026');
assert.match(sentence({day:'2026-08-05',period:'month',shift:2,scope:'shift',days,first_day:'2026-08-03'}),/^August 2026 · shift 2 · no earlier month in the store/);
assert.match(sentence({day:'2026-08-03',period:'day',shift:2,scope:'shift',days,first_day:'2026-08-03'}),/first day in the store/);
assert.match(sentence({day:'2026-08-03',period:'week',shift:2,scope:'shift',days,first_day:'2026-08-03'}),/no earlier week in the store/);
assert.match(sentence({day:'2026-09-14',period:'accounting',shift:2,scope:'shift',days,first_day:'2026-08-03'}),/^4-4-5 accounting period containing Monday 14 Sep 2026 · shift 2/);

// ---- Weekend rejection: the reason names the weekday, the accepted day is restored, the note is shown.
assert.match(vm.runInContext(`dayRejection('2026-09-12',${JSON.stringify(days)})`,context),/^Saturday 12 Sep 2026 is a Saturday: the plant is down at weekends.*Monday to Friday, 3 Aug 2026 to 30 Sep 2026/);
assert.match(vm.runInContext(`dayRejection('2026-10-05',${JSON.stringify(days)})`,context),/outside the record store/);
assert.equal(vm.runInContext(`dayRejection('2026-09-14',${JSON.stringify(days)})`,context),null);
vm.runInContext(`config={days:${JSON.stringify(days)},first_day:'2026-08-03',last_day:'2026-09-30',token:'t'};acceptedDay='2026-09-14'`,context);
elements.day.value='2026-09-13';const rejection=elements.day.events.change();
assert.match(rejection,/Sunday 13 Sep 2026 is a Sunday/);assert.equal(elements.day.value,'2026-09-14');assert.equal(elements['day-note'].hidden,false);assert.equal(elements['day-note'].textContent,rejection);
elements.day.value='2026-09-11';assert.equal(elements.day.events.change(),null);assert.equal(elements['day-note'].hidden,true);assert.match(elements['scope-help'].textContent,/^Friday 11 Sep 2026/);
elements.day.value='2026-09-14';elements.day.events.change();

// ---- Segmented buttons mirror the hidden selects; chips are grouped by intent.
vm.runInContext('renderSegments();renderQuestionChips()',context);
const pressed=group=>elements[group+'-seg'].children.filter(b=>b['aria-pressed']==='true').map(b=>b.dataset.value);
assert.deepEqual(elements['period-seg'].children.map(b=>b.dataset.value),['day','week','month','accounting']);
assert.deepEqual(pressed('period'),['day']);assert.deepEqual(pressed('shift'),['2']);
elements['shift-seg'].children.find(b=>b.dataset.value==='all').events.click();assert.equal(elements.scope.value,'day');assert.deepEqual(pressed('shift'),['all']);
elements['shift-seg'].children.find(b=>b.dataset.value==='1').events.click();assert.equal(elements.scope.value,'shift');assert.equal(elements.shift.value,'1');
elements['shift-seg'].children.find(b=>b.dataset.value==='2').events.click();
elements['period-seg'].children.find(b=>b.dataset.value==='month').events.click();assert.equal(elements.period.value,'month');assert.equal(elements.question.disabled,false);
// Under a period the chips follow the window word and the per-shift matrices are disabled; under Day everything is live.
const chipList=()=>elements.examples.children.flatMap(g=>g.children[1].children);
assert.ok(chipList().some(b=>b.textContent==='Costliest reason this month'&&b.dataset.question==='Which downtime reason cost the most minutes this month?'&&!b.disabled));
assert.ok(chipList().some(b=>b.textContent==='This month vs last'&&b.dataset.question==='How did this month go versus the previous one?'));
assert.deepEqual(chipList().filter(b=>b.disabled).map(b=>b.textContent),['Setup matrix','Read notes','Compare shifts','Rank losses','Orders + quantities']);
elements['period-seg'].children.find(b=>b.dataset.value==='accounting').events.click();assert.ok(chipList().some(b=>b.dataset.question==='Stops per day this period'));
elements['period-seg'].children.find(b=>b.dataset.value==='day').events.click();assert.equal(elements.period.value,'day');assert.equal(chipList().filter(b=>b.disabled).length,0);assert.ok(chipList().some(b=>b.textContent==='Costliest reason this week'));
assert.deepEqual(elements.examples.children.map(g=>g.children[0].textContent),['Speed','Downtime','Waste','Compare','Orders']);
const chips=elements.examples.children.flatMap(g=>g.children[1].children);
for(const q of ['How did September go versus August?','Which downtime reason cost the most minutes this week?','Is it bond or warp?','Stops per day this week','Trend of trim over eight weeks','Compare all shifts','Show orders and quantities'])assert.ok(chips.some(b=>b.dataset.question===q),q);
assert.equal(chips.find(b=>b.dataset.question==='Compare all shifts').dataset.scope,'day');

// ---- KPI tiles from a get_period_kpis envelope: change -> class, previous value, more, withheld.
const walk=(el,out=[])=>{for(const c of el.children||[]){out.push(c);walk(c,out);}return out;};
const kpiRow=(metric,label,unit,current,previous,delta,better,change)=>({metric,label,unit,current,previous,delta,better,change});
const kpiRows=[kpiRow('volume_msf','Volume','MSF',2252.2,2366.5,-114.3,'higher','worse'),kpiRow('downtime_pct','Downtime','% of scheduled minutes',2.11,1.48,0.63,'lower','worse'),kpiRow('shear_pct','Shear','% of gross',0.63,0.67,-0.04,'lower','better'),kpiRow('stops','Stops','count',7,7,0,'lower','same'),kpiRow('setups','Setup records','count',60,58,2,'neutral','neutral'),
 ...['speed_run_fpm','speed_all_fpm','minutes_per_stop','trim_pct','dry_end_pct','bale_pct','maintenance_pm_pct','operator_pct','lineal_ft','downtime_minutes','bond_pct'].map(m=>kpiRow(m,m,'% of gross',1,1,0,'lower','same'))];
const envelope=(comparison)=>({tool:'get_period_kpis',window:{label:'2026-09-14 (Mon)',coverage:'complete'},previous_window:{label:'2026-09-11 (Fri)',coverage:'complete'},shift:2,coverage:'complete',comparison,rows:kpiRows,trace:{sql:'SELECT 1',parameters:[[1]]}});
context.result={run_id:'r',session_id:'s',production_day:'2026-09-14',selected_shifts:[],mode:'offline',status:'complete',context:{},usage:{tool_calls:2,model_calls:0},sections:[],trace:[{tool:'get_period_kpis',arguments:{window:{period:'day',anchor:'2026-09-14'},compare:true,shift:2},ok:true,elapsed_ms:3,data:envelope('complete')}],tables:[],charts:[]};
vm.runInContext('showResult(result)',context);
const tiles=walk(elements.kpis).filter(e=>/^tile\b/.test(e.className));
assert.equal(tiles.length,16);assert.equal(tiles.filter(t=>t.className.includes('extra')).length,4);
assert.equal(tiles[0].children[0].textContent,'Volume');   // priority order: volume first
const chipOf=t=>walk(t).find(e=>/^delta\b/.test(e.className));
const byLabel=Object.fromEntries(tiles.map(t=>[t.children[0].textContent,t]));
assert.match(chipOf(byLabel.Volume).className,/\bworse\b/);assert.equal(chipOf(byLabel.Volume).textContent,'▼ −114.3 MSF');
assert.match(chipOf(byLabel.Downtime).className,/\bworse\b/);assert.equal(chipOf(byLabel.Downtime).textContent,'▲ +0.63 pts');
assert.match(chipOf(byLabel.Shear).className,/\bbetter\b/);assert.match(byLabel.Shear.className,/\bbetter\b/);
assert.match(chipOf(byLabel.Stops).className,/\bsame\b/);assert.match(chipOf(byLabel['Setup records']).className,/\bneutral\b/);
assert.equal(walk(byLabel.Volume).find(e=>e.className==='prev').textContent,'prev 2,366.5 MSF');
assert.equal(walk(byLabel.Volume).find(e=>e.className==='v').children.map(c=>c.textContent).join(' '),'2,252.2 MSF');
const more=walk(elements.kpis).find(e=>/\bmore\b/.test(e.className));assert.equal(more.textContent,'Show 4 more');more.events.click();assert.equal(more.textContent,'Show fewer');
context.result.trace[0].data=envelope('withheld: a window is incomplete or unavailable');context.result.trace[0].data.rows=kpiRows.map(r=>({...r,delta:null,change:null}));
vm.runInContext('showResult(result)',context);
const withheld=walk(elements.kpis).filter(e=>/^delta\b/.test(e.className));
assert.equal(withheld.length,16);assert.ok(withheld.every(c=>c.className.includes('withheld')&&c.textContent==='withheld'));
assert.ok(walk(elements.kpis).some(e=>e.className==='warning-note'&&/Deltas withheld/.test(e.textContent)));
assert.ok(walk(elements.trace).some(e=>e.className==='tool'&&e.textContent==='get_period_kpis'));   // the trail row names the tool

// ---- Chart node counts from sample specs: paired bars when y2 is present, a line with partial weeks.
elements.charts.replaceChildren();
vm.runInContext(`showChart({type:'bar',title:'paired',coverage:'complete',unit:'per row: see unit',x:'label',y:'current',y2:'previous',rows:[{label:'Volume',unit:'MSF',current:10,previous:8,change:'better'},{label:'Trim',unit:'% of gross',current:3,previous:2.5},{label:'Stops',unit:'count',current:5,previous:null}]})`,context);
let marks=walk(elements.charts);
assert.equal(marks.filter(e=>e.class==='bar cur').length,3);assert.equal(marks.filter(e=>e.class==='bar prev').length,2);
assert.ok(marks.some(e=>e.className==='lg'));
elements.charts.replaceChildren();
vm.runInContext(`showChart({type:'line',title:'trend',coverage:'partial',unit:'% of gross',x:'week_start',y:'value',rows:[{week_start:'2026-08-24',value:2.9,partial:false,production_days:5},{week_start:'2026-08-31',value:3.0,partial:false,production_days:5},{week_start:'2026-09-07',value:3.1,partial:false,production_days:5},{week_start:'2026-09-14',value:3.15,partial:true,production_days:3}]})`,context);
marks=walk(elements.charts);
assert.equal(marks.filter(e=>e.class==='line').length,1);assert.equal(marks.filter(e=>/^dot\b/.test(e.class)).length,4);assert.equal(marks.filter(e=>e.class==='dot partial').length,1);
assert.ok(marks.some(e=>e.tag==='text'&&e.textContent==='partial'));
elements.charts.replaceChildren();
vm.runInContext(`showChart({type:'bar',title:'pareto',coverage:'complete',unit:'minutes',x:'category',y:'minutes',y2:'previous_minutes',rows:[{category:'Other',stops:11,minutes:140.9,share_pct:44,cumulative_pct:44,previous_minutes:157},{category:'Order change',stops:37,minutes:46.7,share_pct:14.6,cumulative_pct:58.6,previous_minutes:33.3}]})`,context);
marks=walk(elements.charts);
assert.equal(marks.filter(e=>e.class==='bar vital').length,2);assert.ok(marks.some(e=>e.tag==='text'&&e.textContent==='141 · 44% · cum 44%'));
elements.charts.replaceChildren();
vm.runInContext(`showChart({type:'bar',title:'waste',coverage:'complete',unit:'% of gross',x:'label',y:'pct_of_gross',y2:'previous_pct_of_gross',rows:[{category:'trim',label:'Side trim',pct_of_gross:3.1,previous_pct_of_gross:3.0},{category:'bond',label:'Dry-end: bond',pct_of_gross:0.4,previous_pct_of_gross:0.4},{category:'warp',label:'Dry-end: warp',pct_of_gross:0.33,previous_pct_of_gross:0.47},{category:'misalignment',label:'Dry-end: misalignment',pct_of_gross:0.3,previous_pct_of_gross:0.3},{category:'moisture',label:'Dry-end: moisture',pct_of_gross:0.1,previous_pct_of_gross:0.1}]})`,context);
marks=walk(elements.charts);
assert.equal(marks.filter(e=>/^seg s[1234]$/.test(e.class)).length,8);   // two stacked bars x four segments (bond, warp, misalignment, moisture)
assert.equal(marks.filter(e=>e.class==='bar cur').length,1);           // the non-dry-end categories paired beneath
elements.charts.replaceChildren();
const wetEnds=Array.from({length:12},(_,i)=>({wet_end_id:'WE'+i,actual_fpm:400+i*30,target_fpm:800,speed_to_target_pct:50+i*3,grade:'200-C'}));
vm.runInContext(`showChart({shift:2,coverage:'complete',unit:'ft/min',x:'wet_end_id',y:'actual_fpm',rows:${JSON.stringify(wetEnds)}})`,context);
marks=walk(elements.charts);
assert.equal(marks.filter(e=>e.class==='bar cur').length,10);          // five best and five worst of twelve
const toggle=marks.find(e=>e.tag==='button');assert.equal(toggle.textContent,'Show all 12');toggle.events.click();
assert.equal(walk(elements.charts).filter(e=>e.class==='bar cur').length,12);

// ---- Messages: consecutive reported-note lines fold into one disclosure.
elements.messages.replaceChildren();
vm.runInContext(`message('Investigator',['Shift 2: 600 ft/min.','Shift 2, ST1 - reported note (synthetic, unverified): a','Shift 2, ST2 - reported note (synthetic, unverified): b','Done.'])`,context);
const msg=elements.messages.children[0];
assert.equal(msg.children.length,4);   // label, paragraph, one note group, paragraph
assert.equal(msg.children[2].className,'note-group');assert.equal(msg.children[2].children[0].textContent,'2 reported notes (synthetic, unverified — observations, not causes)');
console.log('UI unit checks passed: scope sentence, weekend rejection, segments, chip groups, KPI tiles, charts, trail row, note folding, heatmap, idle card.');

// ---- issue #33: with a frame scheduler, tables and charts paint before the trace is built; a newer answer cancels a stale trace.
{
 const frames=[];
 context.requestAnimationFrame=f=>{frames.push(f);return frames.length;};
 vm.runInContext("result={run_id:'a',session_id:'s',production_day:'2026-09-14',selected_shifts:[2],mode:'offline',status:'complete',context:{},usage:{tool_calls:1,model_calls:0},sections:[],trace:[{tool:'get_shift_notes',ok:true,elapsed_ms:1,arguments:{shift:2},data:{shift:2,coverage:'complete',rows:[]}}],tables:[{title:'get_shift_notes',shift:2,coverage:'complete',rows:[{notes:'x'}]}],charts:[{shift:2,coverage:'complete',unit:'ft/min',x:'wet_end_id',y:'actual_fpm',rows:[{wet_end_id:'W2-01',actual_fpm:600,target_fpm:800,speed_to_target_pct:75,grade:'200-C'}]}]}",context);
 vm.runInContext('showResult(result)',context);
 assert.equal(elements.charts.children.length,1);assert.equal(elements.tables.children.length,1);
 assert.equal(elements.trace.children.length,0);              // not yet: first paint carries the evidence, not the trail
 const drain=()=>{while(frames.length)frames.shift()();};      // a rAF callback runs before its frame paints, so the trace is two frames deep
 assert.equal(frames.length,1);frames.shift()();
 assert.equal(elements.trace.children.length,0);              // first frame: still nothing, the paint happens after this callback
 assert.equal(frames.length,1);frames.shift()();
 assert.equal(elements.trace.children.length,1);              // second frame, after the paint: the trail is built
 vm.runInContext('showResult(result)',context);const stale=frames.shift();
 vm.runInContext('showResult(Object.assign({},result,{run_id:"newer"}))',context);const fresh=frames.shift();
 stale();drain();assert.equal(elements.trace.children.length,0);   // the stale chain sees a newer answer and does nothing
 fresh();drain();assert.equal(elements.trace.children.length,1);
 delete context.requestAnimationFrame;                         // later checks run synchronously again
 console.log('UI issue-33 checks passed: tables and charts paint before the trace; a stale frame is skipped.');
}

// ---- issue #40: the query box is the first thing below the question, step 1 open, built in the first paint; the
// per-step data ("What came back") stays below and keeps #33's two-frame deferral; the copy text is the SQL with its
// bound values; charts and tables link back to their query box; on a phone every box is one line with the SQL on tap.
{
 const at=id=>html.indexOf(`id="${id}"`);
 assert.ok(at('examples')<at('ask')&&at('ask')<at('card-question')&&at('card-question')<at('queries')&&at('queries')<at('messages'),'the canned questions and the ask bar; the card title; the query box directly under it; then the narrative');
 assert.ok(at('queries')<at('charts')&&at('charts')<at('tables')&&at('tables')<at('trace'),'queries, then the evidence, then the per-step data');
 assert.ok(html.includes('<details id="execution"><summary>What came back, per query'));
 const frames=[]; context.requestAnimationFrame=f=>{frames.push(f);return frames.length;};
 const copied=[]; context.navigator={clipboard:{writeText:async t=>{copied.push(t);}}};
 const sql='SELECT reason, sum(seconds)/60 AS minutes\nFROM stops WHERE shift = ?\nGROUP BY reason';
 vm.runInContext(`result={run_id:'q',session_id:'s',production_day:'2026-09-14',selected_shifts:[2],mode:'offline',status:'complete',context:{},usage:{tool_calls:3,model_calls:0},sections:['Shift 2 lost 14.9 minutes.'],
  trace:[{result_id:'r:1',tool:'get_downtime_breakdown',arguments:{shift:2,group_by:'reason',kind:null},ok:true,elapsed_ms:2.14,data:{shift:2,coverage:'complete',rows:[{category:'Order change',down_minutes:9.2},{category:'Knife jam',down_minutes:5.7}],trace:{sql:${JSON.stringify(sql)},parameters:[2]}}},
         {result_id:'r:2',tool:'get_shift_notes',arguments:{shift:2},ok:true,elapsed_ms:1.8,data:{shift:2,coverage:'complete',rows:[{notes:'n'}],trace:{sql:'SELECT notes FROM notes WHERE shift = ?',parameters:[2]}}},
         {result_id:'r:3',tool:'render_chart',arguments:{result_id:'r:1'},ok:true,elapsed_ms:0.05,data:{shift:2,coverage:'complete',rows:[{category:'Order change',down_minutes:9.2}],type:'bar',x:'category',y:'down_minutes',unit:'minutes',source_result_id:'r:1'}}],
  tables:[{title:'get_shift_notes',shift:2,coverage:'complete',source_result_id:'r:2',rows:[{notes:'n'}]}],
  charts:[{type:'bar',source_result_id:'r:1',shift:2,coverage:'complete',unit:'minutes',x:'category',y:'down_minutes',rows:[{category:'Order change',down_minutes:9.2},{category:'Knife jam',down_minutes:5.7}]}]}`,context);
 vm.runInContext('showResult(result)',context);
 const q=elements.queries.children, boxes=q.filter(e=>/^query\b/.test(e.className));
 assert.equal(q[0].id,'query-0');assert.match(q[q.length-1].children[q[q.length-1].children.length-1].textContent,/^3 queries · 4 ms on the server\. Fixed, parameterized queries\./);   // the box opens on the first query; the count is on the card's title line and, with the server's time, in the foot (#63)
 assert.deepEqual(boxes.map(b=>b.id),['query-0','query-1','query-2']);       // one block per tool step, in order, right after the head
 assert.deepEqual(boxes.map(b=>b.open),[true,false,false]);                   // step 1 open; the others one line with the SQL on tap
 assert.equal(q[q.length-1].className,'queries-foot');
 assert.equal(elements.trace.children.length,0);                              // first paint: the boxes are there, the per-step data is not (#33)
 assert.equal(elements.charts.children.length,1);
 assert.deepEqual(boxes[0].children[0].children.map(c=>c.textContent),['get_downtime_breakdown','(shift=2, group_by=reason)','· 2 rows · 2.1 ms']);
 assert.equal(boxes[0].children[1].className,'sql');
 const copyBtn=walk(boxes[0]).find(e=>e.tag==='button');assert.equal(copyBtn.textContent,'Copy SQL');copyBtn.events.click();
 assert.deepEqual(copied,[sql+'\n\n-- bound values\n[2]']);                   // the copy text equals the SQL with its bound values
 assert.equal(vm.runInContext("sqlText({ok:true,data:{trace:{sql:'SELECT a,\\n        b FROM t\\n        WHERE x = ?',parameters:[1]}}})",context),'SELECT a,\nb FROM t\nWHERE x = ?\n\n-- bound values\n[1]');   // the Python string's indentation is dropped, nothing else
 assert.deepEqual(boxes[2].children[0].children.map(c=>c.textContent).slice(0,2),['render_chart','(result_id=query 1)']);   // a result id reads as the query it names
 assert.equal(vm.runInContext("sqlText({ok:true,data:{daily_sql:'SELECT 1 WHERE shift = ?',daily_parameters:[2]}})",context),'SELECT 1 WHERE shift = ?\n\n-- bound values\n[2]');   // the period report's per-day query carries its bound values too
 assert.equal(walk(boxes[0]).find(e=>e.className==='came-back').href,'#step-0');
 assert.ok(walk(boxes[2]).some(e=>e.tag==='a'&&e.href==='#query-0'));          // a render_chart step names the query it drew
 assert.ok(walk(elements.charts).some(e=>e.className==='query-link'&&e.href==='#query-0'));   // the chart links back to its query box
 assert.ok(walk(elements.tables).some(e=>e.className==='query-link'&&e.href==='#query-1'));   // so does the table
 vm.runInContext("showTable({title:'get_rankings',shift:2,coverage:'incomplete',source_result_id:'r:2',rows:[]})",context);   // an empty (withheld) table still links back
 assert.ok(walk(elements.tables.children[elements.tables.children.length-1]).some(e=>e.className==='query-link'&&e.href==='#query-1'));
 const copyAll=walk(q[q.length-1]).find(e=>e.textContent==='Copy all SQL');copyAll.events.click();
 assert.match(copied[1],/^-- query 1: get_downtime_breakdown\(shift=2, group_by=reason\)\nSELECT reason/);assert.match(copied[1],/\n\n-- query 2: get_shift_notes\(shift=2\)\nSELECT notes/);
 while(frames.length)frames.shift()();                                        // after the paint: the per-step rows exist, with the ids the boxes point at
 assert.deepEqual(elements.trace.children.map(e=>e.id),['step-0','step-1','step-2']);
 const byId=document.getElementById;document.getElementById=id=>id==='step-0'?elements.trace.children[0]:byId(id);
 elements.execution.open=false;walk(boxes[0]).find(e=>e.className==='came-back').events.click();
 assert.equal(elements.execution.open,true);assert.equal(elements.trace.children[0].open,true);   // "What came back" unfolds the trail and the step
 document.getElementById=byId;
 context.matchMedia=()=>({matches:true});vm.runInContext('showResult(result)',context);   // a phone: every box is one line
 assert.deepEqual(elements.queries.children.filter(e=>/^query\b/.test(e.className)).map(b=>b.open),[false,false,false]);
 delete context.matchMedia;
 const phoneCss=fs.readFileSync('src/investigator/web/style.css','utf8');const phoneBlock=phoneCss.slice(phoneCss.indexOf('@media(max-width:850px)'));
 assert.ok(phoneBlock.includes('.query>summary .args{display:none}')&&phoneBlock.includes('.query>summary{flex-wrap:nowrap;overflow:hidden}'),'at 390 px each step is tool · rows · ms on one line; the SQL is on tap');
 vm.runInContext('reset()',context);assert.equal(elements.queries.children.length,0);
 delete context.requestAnimationFrame;delete context.navigator;
 console.log('UI issue-40 checks passed: query box first and open in the first paint; copy text is the SQL with bound values; links both ways; one line per step on a phone.');
}

// ---- issue #36: light by default whatever the OS prefers; a pinned choice in localStorage wins; the toggle stays.
const themeSrc=fs.readFileSync('src/investigator/web/theme.js','utf8'), css=fs.readFileSync('src/investigator/web/style.css','utf8');
function visit({saved,osDark,withButton=true}={}){   // one page load: theme.js runs in <head>, then the header button exists
 const root=new Element('html'), button=new Element('button'), store=saved?{'csi-theme':saved}:{};
 const ctx={document:{documentElement:root,readyState:'complete',getElementById:id=>id==='theme'&&withButton?button:null,addEventListener(){}},
  localStorage:{getItem:k=>k in store?store[k]:null,setItem:(k,v)=>{store[k]=v;}},window:{matchMedia:()=>({matches:!!osDark})}};
 vm.createContext(ctx);vm.runInContext(themeSrc,ctx);return {root,button,store};
}
const fresh=visit({osDark:true});                                   // first visit with a dark OS theme renders light
assert.equal(fresh.root.dataset.theme,undefined);assert.equal(fresh.button.textContent,'Dark');assert.equal(fresh.button['aria-pressed'],'false');
fresh.button.events.click();                                        // toggling to dark pins it
assert.equal(fresh.root.dataset.theme,"dark");assert.equal(fresh.store["csi-theme"],"dark");assert.equal(fresh.button.textContent,"Light");assert.equal(fresh.button['aria-pressed'],'true');
const reload=visit({saved:'dark',osDark:false});                    // and a reload stays dark, even on a light OS
assert.equal(reload.root.dataset.theme,'dark');assert.equal(reload.button.textContent,'Light');
reload.button.events.click();assert.equal(reload.root.dataset.theme,'light');assert.equal(reload.store['csi-theme'],'light');
assert.equal(visit({saved:'light',osDark:true}).root.dataset.theme,'light');
assert.equal(visit({saved:'sepia',osDark:true}).root.dataset.theme,undefined);   // an unknown pinned value is ignored
assert.doesNotThrow(()=>visit({osDark:true,withButton:false}));     // the guide page without a button, or a page before the header, still loads
assert.ok(!css.includes('prefers-color-scheme'),'the stylesheet must not follow the OS theme');
assert.match(css,/:root\[data-theme="dark"\]\{/);                   // the dark tokens still exist for the pinned choice
assert.ok(html.includes('<script src="/theme.js"></script><meta charset'),'theme.js loads first in <head> so a pinned theme applies before first paint');
assert.match(html,/<button type="button" id="theme" class="theme-toggle"/);
assert.ok(/\.heatmap-scale\{[^}]*rgb\(8 127 117/.test(css)&&!css.includes('42 120 214'),'the legend ramp is the cell colour');
// Every light-theme series colour reads at 3:1 or better on the chart panel (WCAG 1.4.11), and chart text inks at 4.5:1.
// The panel is .chart's --surface-2, not the page surface: every chart SVG, legend swatch and heatmap cell is drawn on it.
const lightTokens=Object.fromEntries([...css.slice(css.indexOf(':root{'),css.indexOf(':root[data-theme="dark"]')).matchAll(/--([a-z0-9-]+):(#[0-9a-f]{6})/g)].map(m=>[m[1],m[2]]));
const lum=hex=>[1,3,5].map(i=>parseInt(hex.slice(i,i+2),16)/255).map(v=>v<=.03928?v/12.92:((v+.055)/1.055)**2.4).reduce((s,v,i)=>s+v*[.2126,.7152,.0722][i],0);
const contrast=(a,b)=>{const [hi,lo]=[lum(a),lum(b)].sort((x,y)=>y-x);return (hi+.05)/(lo+.05);};
assert.match(css,/\n\.chart\{[^}]*background:var\(--surface-2\)/,'chart marks are drawn on --surface-2');
const panel=lightTokens['surface-2'];
const onPanel=Object.fromEntries(['s1','s2','s3','s4','prev','target','ink','ink-2'].map(k=>[k,Number(contrast(lightTokens[k],panel).toFixed(2))]));
for(const k of ['s1','s2','s3','s4','prev','target'])assert.ok(contrast(lightTokens[k],panel)>=3,`${k} ${lightTokens[k]} is ${onPanel[k]}:1 on the chart panel ${panel}`);
for(const k of ['ink','ink-2'])assert.ok(contrast(lightTokens[k],panel)>=4.5,`${k} is ${onPanel[k]}:1`);   // chart labels, ticks, captions and legend text
for(const k of ['s1','s2'])assert.ok(contrast(lightTokens[k],lightTokens['surface-3'])>=3,`${k} on the target band ${lightTokens['surface-3']}`);   // range-chart bars run from zero across the --surface-3 band
assert.match(css,/\.chart \.inseg\{fill:#000;/);assert.match(css,/\.chart \.inseg\.s4\{fill:#fff\}/);   // segment labels: black on s1..s3, white on s4
for(const k of ['s1','s2','s3'])assert.ok(contrast(lightTokens[k],'#000000')>=4.5,`black segment label on ${k}`);assert.ok(contrast(lightTokens.s4,'#ffffff')>=4.5,'white segment label on s4');
const blend=(a,[r,g,b],bg)=>'#'+[r,g,b].map((v,i)=>Math.round(a*v+(1-a)*parseInt(bg.slice(1+2*i,3+2*i),16)).toString(16).padStart(2,'0')).join('');
for(const a of [.96,.965]){const cell=blend(a,[8,127,117],panel);assert.ok(contrast(cell,'#000000')>=4.5&&contrast(cell,'#ffffff')>=4.5,`heatmap inks at alpha ${a} on the chart panel`);}   // the ink flip holds on the panel too
assert.match(css,/\.chart \.tick\{[^}]*fill:var\(--ink-2\)/);assert.match(css,/\.chart \.muted,\.heatmap \.muted\{color:var\(--ink-2\)\}/);
console.log('light-theme contrast on the chart panel (--surface-2):',JSON.stringify(onPanel));
console.log('UI issue-36 checks passed: light by default, pinned theme wins, toggle present, heatmap ink flip.');

// ---- issue #17: post() refreshes /api/config once on a 403 and resends with the new token; a second 403 is returned, not retried forever.
(async()=>{
 await new Promise(r=>setTimeout(r,50));   // let init()'s own primeKpis() finish against the original fetch stub before swapping it
 const calls=[];
 const record=(path,init)=>{if(path==='/api/config'||(init&&init.body&&init.body.includes('probe-17')))calls.push([path,init&&init.headers?init.headers['X-Workbench-Token']:undefined]);};
 context.fetch=async(path,init)=>{
  record(path,init);
  if(path==='/api/config')return {ok:true,status:200,json:async()=>({token:'fresh',days:['2026-09-01','2026-09-30']})};
  const asks=calls.filter(c=>c[0]==='/api/ask').length;
  return asks===1?{ok:false,status:403,json:async()=>({error:'Missing session token'})}:{ok:true,status:200,json:async()=>({session_id:'s2',session_reset:true})};
 };
 vm.runInContext("config={token:'stale',days:['2026-09-01','2026-09-30']}",context);
 const response=await vm.runInContext("post('/api/ask',{question:'probe-17'})",context);
 assert.equal(response.status,200);
 assert.deepEqual(calls.map(c=>c[0]),['/api/ask','/api/config','/api/ask']);
 assert.deepEqual([calls[0][1],calls[2][1]],['stale','fresh']);
 assert.equal(vm.runInContext('config.token',context),'fresh');
 assert.equal(vm.runInContext('config.days.length',context),2);       // the refreshed config merges, it does not drop fields
 calls.length=0;
 context.fetch=async(path,init)=>{record(path,init);return path==='/api/config'?{ok:true,status:200,json:async()=>({token:'fresh2'})}:{ok:false,status:403,json:async()=>({error:'Origin not allowed'})};};
 const still=await vm.runInContext("post('/api/ask',{question:'probe-17'})",context);
 assert.equal(still.status,403);assert.deepEqual(calls.map(c=>c[0]),['/api/ask','/api/config','/api/ask']);   // one refresh, one resend, then the error stands
 // the resend carries the caller's extra headers (a loopback visitor key) even though ask() clears its own copy right after calling
 const seen=[];
 context.fetch=async(path,init)=>{if(path==='/api/config')return {ok:true,status:200,json:async()=>({token:'fresh3'})};seen.push(init.headers['X-OpenAI-Key']);return seen.length===1?{ok:false,status:403,json:async()=>({})}:{ok:true,status:200,json:async()=>({})};};
 await vm.runInContext("(()=>{const h={'X-OpenAI-Key':'sk-visitor'};const p=post('/api/ask',{question:'probe-17'},h);delete h['X-OpenAI-Key'];return p;})()",context);
 assert.deepEqual(seen,['sk-visitor','sk-visitor']);
 console.log('UI issue-17 checks passed: 403 refreshes the token once and resends.');
})().then(issue63).catch(error=>{console.error(error);process.exit(1);});

// ---- issue #63: the layout shell from the mockup. A left rail (looking at, ask in one click, your rates, agent and
// advanced folded), an ask bar (Ask is the offline planner, Ask the analyst the live agent when one is offered), one
// answer card that shows a skeleton in the same task as the submit and fills in place, earlier answers one line each.
// Nothing is disabled while a question is out and the /api/ask payload is the one it always was. What a stub cannot
// measure (where the fold falls at 1440x1040) is asserted in a real browser by tests/browser_layout.cjs.
async function issue63(){
 const at=id=>{const i=html.indexOf(`id="${id}"`);assert.ok(i>0,`id ${id} is in the page`);return i;};
 const inOrder=(ids,why)=>ids.slice(1).forEach((id,i)=>assert.ok(at(ids[i])<at(id),`${why}: ${ids[i]} before ${id}`));
 inOrder(['rail','day','shift-seg','period-seg','scope-help','examples','report','rates-change','advanced','mode','api-key','corrupt','reset','ask'],'the rail, then the main column');
 inOrder(['ask','question','submit','analyst','card','card-question','status','skeleton','queries','messages','charts','heatmaps','kpis','metrics','came-back','download','pdf','tables','execution','trace','thread'],'the ask bar, then the card frame, then the earlier answers');
 for(const text of ['>Looking at<','>Ask in one click<','Every canned question is a fixed query. No model, no wait.','>Your rates<','<dt>Paper</dt><dd>$55 / MSF</dd>','<dt>Machine time</dt><dd>$1,200 / hr</dd>','<dt>Board margin</dt><dd class="unset">not set</dd>'])assert.ok(html.includes(text),text);
 assert.match(html,/<button id="rates-change"[^>]* disabled [^>]*>Change rates<\/button>/);                       // a placeholder until the cost model (#31)
 assert.match(html,/<details class="rail-card rail-more" id="advanced">[\s\S]*id="mode"[\s\S]*id="corrupt"[\s\S]*<\/details>\s*<\/aside>/);   // agent choice and Advanced under one disclosure
 assert.match(html,/<select id="mode"><option value="offline">/);                                                 // offline stays the default
 assert.match(html,/<input id="question" type="text"/);assert.ok(!html.includes('<textarea'));                   // one input
 assert.match(html,/<button id="analyst" type="button"[^>]* hidden>Ask the analyst<\/button>/);
 assert.equal([...html.matchAll(/ disabled[ >]/g)].length,1,'the only disabled control in the page source is Change rates');
 const css=fs.readFileSync('src/investigator/web/style.css','utf8'), phone=css.slice(css.lastIndexOf('@media(max-width:850px)'));
 assert.ok(css.includes('.shell-grid{display:grid;grid-template-columns:300px minmax(0,1fr)'));
 assert.ok(css.includes('@media(min-width:1320px){#charts.pair{display:grid;grid-template-columns:repeat(2,minmax(0,1fr))'),'two charts side by side at desktop width');
 assert.ok(css.includes('.answer .query pre.sql{max-height:80px}'),'the open query shows four lines in the card');
 assert.ok(css.includes('.answer:not(.done) .card-foot,.answer:not(.done) #execution{display:none}'));
 assert.ok(css.includes('.answer.skeleton .sk-body{display:flex'));
 assert.ok(phone.includes('.rail{display:none')&&phone.includes('body.rail-open .rail{display:flex}')&&phone.includes('.rail-toggle{display:inline-flex')&&phone.includes('.shell-grid{grid-template-columns:minmax(0,1fr)'),'phone: one column, the rail behind the Questions button');
 assert.match(css,/body\.shell\{background:var\(--page\)/);assert.match(css,/:root\[data-theme="dark"\]\{[^}]*--page:#121212/);   // the shell has its page colour in both themes

 // a clean page: Monday 14 September, shift 2, offline, no key on the server, priming off so only questions are sent
 const sent=[], gate={}, sessions={}; let hold=null, nextTrace=null;   // gate[question]: a promise that answer waits for; sessions[question]: the session id it comes back with
 const kpiStep={result_id:'x:1',tool:'get_shift_kpis',arguments:{shift:2},ok:true,elapsed_ms:3,data:{shift:2,coverage:'complete',rows:[row]}};
 const answer=question=>({run_id:'run'+sent.length,session_id:sessions[question]||'s63',session_reset:false,production_day:'2026-09-14',selected_shifts:[2],mode:'offline rule-based planner; no language model',status:'complete',context:{},usage:{tool_calls:1,model_calls:0},sections:['Shift 2: 616.2 ft/min.'],trace:nextTrace||[kpiStep],tables:[],charts:[],report_markdown:'#'});
 context.fetch=async(path,init)=>{if(path==='/api/config')return {ok:true,status:200,json:async()=>({token:'t'})};const body=JSON.parse(init.body);sent.push({body,headers:init.headers});if(hold)await hold;if(gate[body.question])await gate[body.question];return {ok:true,status:200,json:async()=>answer(body.question)};};
 const tick=()=>new Promise(r=>setTimeout(r,5));
 Object.assign(elements.day,{value:'2026-09-14'});elements.period.value='day';elements.shift.value='2';elements.scope.value='shift';elements.mode.value='offline';elements.mode.disabled=false;elements.corrupt.checked=false;elements['api-key'].value='';
 vm.runInContext("config={token:'t',days:['2026-09-11','2026-09-14','2026-09-15'],live_modes:[],demo_guards:{rate_limit_per_min:5}};acceptedDay='2026-09-14';renderSegments();renderQuestionChips();reset()",context);
 const disabledNow=()=>Object.entries(elements).filter(([,e])=>e.disabled).map(([id])=>id);
 const lines=()=>elements.thread.children.map(r=>r.children.map(c=>c.textContent));
 assert.equal(elements.card.className,'card answer idle');assert.equal(elements['card-question'].textContent,'Ask about this shift');
 assert.equal(elements.status.textContent,'Shift 2 · Mon 14 Sep 2026 · no question yet');assert.equal(elements['scope-chip'].textContent,'Shift 2 · Mon 14 Sep 2026');
 assert.equal(elements.thread.children.length,0);assert.equal(elements.analyst.hidden,true);assert.equal(elements.submit.textContent,'Ask');
 assert.deepEqual(elements['shift-seg'].children.map(b=>b.textContent),['1 · 7–3','2 · 3–11','3 · 11–7','All']);assert.equal(elements['shift-seg'].children[1]['aria-label'],'Shift 2, 15:00 to 23:00');
 assert.deepEqual(elements['period-seg'].children.map(b=>b.textContent),['Day','Week','Month','4-4-5']);
 assert.match(elements.question.placeholder,/^Ask about this shift: /);

 // submit: the skeleton card is there before ask() first yields (the same task as the click), with nothing disabled
 let release;hold=new Promise(r=>{release=r;});
 const first=vm.runInContext("ask('Why was this shift slow?')",context);
 assert.equal(elements.card.className,'card answer skeleton');assert.equal(elements.card['aria-busy'],'true');
 assert.equal(elements['card-question'].textContent,'Why was this shift slow?');assert.equal(elements.status.textContent,'Shift 2 · Mon 14 Sep 2026 · offline · querying…');
 assert.deepEqual(disabledNow(),[]);assert.equal(elements.messages.children.length,0);
 assert.equal(sent.length,1);
 assert.deepEqual(Object.keys(sent[0].body),['question','day','shift','corrupt','session_id','mode','scope','period']);   // the /api/ask payload, unchanged
 assert.deepEqual(sent[0].body,{question:'Why was this shift slow?',day:'2026-09-14',shift:2,corrupt:false,session_id:null,mode:'offline',scope:'shift',period:'day'});
 release();hold=null;const returned=await first;
 assert.deepEqual(Object.keys(returned),['status','sections','production_day','shifts']);
 assert.equal(elements.card.className,'card answer done');assert.equal(elements.card['aria-busy'],'false');assert.equal(elements.card['aria-label'],'Answer: Why was this shift slow?');
 assert.match(elements.status.textContent,/^Shift 2 · Mon 14 Sep 2026 · offline · 1 query · \d+ ms$/);                // the title line carries the measured ms
 assert.match(elements.status.title,/^\d+ ms from submit to the rendered card in this browser; 3 ms of it in the queries on the server\. Planner: offline rule-based planner/);
 assert.equal(elements.messages.children.length,1);assert.deepEqual(elements.messages.children[0].children.map(c=>[c.tag,c.textContent]),[['p','Shift 2: 616.2 ft/min.']]);   // the summary, without a speaker label: the title line names the planner
 assert.equal(elements.queries.children[0].id,'query-0');assert.equal(elements.metrics.children.length,6);

 // three canned questions in a row: one open card, two collapsed lines (newest on top), no control disabled, no reload
 vm.runInContext('reset()',context);sent.length=0;
 const chip=label=>elements.examples.children.flatMap(g=>g.children[1].children).find(b=>b.textContent===label);
 for(const label of ['Why was this shift slow?','Dry-end waste','Speed to target']){chip(label).events.click();assert.equal(elements.card.className,'card answer skeleton');assert.deepEqual(disabledNow(),[]);await tick();}
 assert.equal(elements.card.className,'card answer done');assert.equal(elements['card-question'].textContent,'Show speed to target');
 assert.equal(elements.thread.children.length,2);assert.ok(elements.thread.children.every(r=>r.className==='card earlier'));
 assert.deepEqual(lines().map(l=>[l[0],l[2]]),[['Show dry-end quality waste','Open'],['Why was this shift slow? Chart downtime by reason.','Open']]);
 for(const l of lines())assert.match(l[1],/^Shift 2 · \d+ ms$/);                                                    // <question> · Shift 2 · 52 ms · Open
 assert.deepEqual(disabledNow(),[]);
 assert.deepEqual(sent.map(s=>[s.body.mode,s.body.session_id]),[['offline',null],['offline','s63'],['offline','s63']]);   // canned questions are offline; follow-ups keep the conversation
 assert.equal(elements.question.value,'Show speed to target');
 elements.thread.children[1].children[2].events.click();                                                           // Open the oldest: it becomes the card, the other two are the lines
 assert.equal(elements['card-question'].textContent,'Why was this shift slow? Chart downtime by reason.');assert.equal(elements.card.className,'card answer done');
 assert.deepEqual(lines().map(l=>l[0]),['Show speed to target','Show dry-end quality waste']);
 assert.equal(sent.length,3);                                                                                      // opening an earlier answer asks nothing

 // a second question while the first is still out: both are cards, the newer one is open, the older fills its line
 hold=new Promise(r=>{release=r;});
 const slow=vm.runInContext("ask('Show setup matrix')",context), next=vm.runInContext("ask('Show rankings')",context);
 assert.equal(elements['card-question'].textContent,'Show rankings');assert.equal(elements.card.className,'card answer skeleton');
 assert.deepEqual(lines()[0],['Show setup matrix','Shift 2 · querying…','Open']);
 release();hold=null;await Promise.all([slow,next]);
 assert.equal(elements.card.className,'card answer done');assert.match(lines()[0][1],/^Shift 2 · \d+ ms$/);assert.equal(elements.thread.children.length,4);

 // a failed question is a card too: the reason in the card, the line says so, nothing throws past the button
 const okFetch=context.fetch;context.fetch=async()=>({ok:false,status:400,json:async()=>({error:'Selected shift only'})});
 await assert.rejects(vm.runInContext("ask('Compare that with first shift')",context),/Selected shift only/);
 assert.equal(elements.card.className,'card answer failed');assert.match(elements.status.textContent,/ · offline · could not complete$/);
 assert.ok(elements.messages.children[0].children.some(c=>c.textContent==='Selected shift only'));
 context.fetch=okFetch;

 // the rail moves on: a fresh conversation and an idle card for the new scope; the thread stays, each line with the scope it was asked under
 elements['shift-seg'].children.find(b=>b.dataset.value==='3').events.click();
 assert.equal(elements.card.className,'card answer idle');assert.equal(elements.status.textContent,'Shift 3 · Mon 14 Sep 2026 · no question yet');
 assert.equal(elements.thread.children.length,6);assert.equal(lines()[0][1],'Shift 2 · could not complete');assert.match(lines()[1][1],/^Shift 2 · \d+ ms$/);
 elements.day.value='2026-09-15';elements.day.events.change();
 assert.match(lines()[1][1],/^Shift 2 · Mon 14 Sep 2026 · \d+ ms$/);                                                 // off its day, a line names its window
 elements.period.value='week';elements.period.events.change();assert.equal(elements.status.textContent,'Shift 3 · Week of Mon 14 – Fri 18 Sep 2026 · no question yet');assert.equal(elements['card-question'].textContent,'Ask about this week');
 elements.period.value='day';elements.period.events.change();elements.day.value='2026-09-14';elements.day.events.change();elements['shift-seg'].children.find(b=>b.dataset.value==='2').events.click();
 sent.length=0;chip('Dry-end waste').events.click();await tick();assert.equal(sent[0].body.session_id,null);        // a scope change started a fresh conversation
 elements.reset.events.click();assert.equal(elements.thread.children.length,0);assert.equal(elements.card.className,'card answer idle');   // "Clear answers"

 // Ask is the deterministic path whatever agent is chosen; Ask the analyst is the live one, shown only when one is on offer
 sent.length=0;elements.question.value='   ';elements.ask.events.submit({preventDefault(){}});assert.equal(sent.length,0);   // nothing to ask
 elements.mode.value='openai';vm.runInContext('updateScope()',context);assert.equal(elements.analyst.hidden,false);   // chosen under "Agent and advanced"
 elements.question.value='Why was this shift slow?';elements['api-key'].value='sk-visitor';
 elements.ask.events.submit({preventDefault(){}});await tick();
 assert.equal(sent[0].body.mode,'offline');assert.equal(sent[0].headers['X-OpenAI-Key'],undefined);assert.match(elements.status.textContent,/ · offline · 1 query · /);
 assert.equal(elements['api-key'].value,'sk-visitor');                                                             // an offline question leaves the key for the analyst
 elements.analyst.events.click();elements.analyst.events.click();                                                   // a double click is one request, not two paid rounds
 assert.equal(elements.status.textContent,'Shift 2 · Mon 14 Sep 2026 · OpenAI · querying…');await tick();
 assert.equal(sent.length,2);assert.equal(elements.thread.children.length,1);
 assert.equal(sent[1].body.mode,'openai');assert.equal(sent[1].headers['X-OpenAI-Key'],'sk-visitor');assert.equal(elements['api-key'].value,'');
 assert.deepEqual(Object.keys(sent[1].body),Object.keys(sent[0].body));
 elements.mode.value='offline';vm.runInContext('updateScope()',context);assert.equal(elements.analyst.hidden,true);    // no agent chosen, no server key: hidden
 vm.runInContext("config.live_modes=['openai','anthropic'];config.public=true;updateScope()",context);assert.equal(elements.analyst.hidden,false);   // the server's own key offers it
 elements['api-key'].value='sk-ignored';elements.analyst.events.click();await tick();
 assert.equal(sent[2].body.mode,'anthropic');assert.equal(sent[2].headers['X-Anthropic-Key'],undefined);             // Anthropic first; public mode never sends a visitor key
 vm.runInContext("config.live_modes=[];config.public=false;reset()",context);

 // the conversation follows the newest question, whichever answer lands last: A is held, B answers first, then A
 let letA;gate['question A']=new Promise(r=>{letA=r;});sessions['question A']='session-A';sessions['question B']='session-B';sent.length=0;
 const a=vm.runInContext("ask('question A')",context), b=vm.runInContext("ask('question B')",context);
 await b;letA();await a;await vm.runInContext("ask('question C')",context);
 assert.deepEqual(sent.map(s=>s.body.session_id),[null,null,'session-B']);
 // a question that names its own window: the title line and the collapsed line say what the query ran over, in the tool's words
 const september={period:'month',anchor:'2026-09-01',start:'2026-09-01',end:'2026-09-30',label:'September 2026 (2026-09-01 to 2026-09-30)',coverage:'complete'};
 nextTrace=[{result_id:'w:1',tool:'get_downtime_pareto',arguments:{window:{period:'month',anchor:'2026-09-01'},shift:null},ok:true,elapsed_ms:2,data:{window:september,shift:null,coverage:'complete',rows:[]}}];
 await vm.runInContext("ask('Which downtime reason cost the most minutes in September?')",context);
 assert.match(elements.status.textContent,/^All shifts · September 2026 \(2026-09-01 to 2026-09-30\) · offline · 1 query · \d+ ms$/);   // the rail says shift 2, 14 Sep; the query ran all shifts over the month
 nextTrace=[{result_id:'w:1',tool:'get_waste_breakdown',arguments:{window:{period:'day',anchor:'2026-09-14'},shift:2},ok:true,elapsed_ms:2,data:{window:{period:'day',anchor:'2026-09-14',start:'2026-09-14',end:'2026-09-14',label:'2026-09-14 (Mon)',coverage:'complete'},shift:2,coverage:'complete',rows:[]}}];
 await vm.runInContext("ask('Is it bond or warp?')",context);nextTrace=null;
 assert.match(elements.status.textContent,/^Shift 2 · Mon 14 Sep 2026 · offline · 1 query · \d+ ms$/);              // the rail's own window keeps the rail's wording
 assert.match(lines()[0][1],/^All shifts · September 2026 \(2026-09-01 to 2026-09-30\) · \d+ ms$/);
 // a card reopened before its frames ran builds its per-query rows once
 {const frames=[];context.requestAnimationFrame=f=>{frames.push(f);return frames.length;};
  const open=i=>elements.thread.children[i].children[2].events.click();open(0);open(0);open(0);while(frames.length)frames.shift()();
  assert.equal(elements.trace.children.length,1);assert.deepEqual(elements.trace.children.map(e=>e.id),['step-0']);delete context.requestAnimationFrame;}
 vm.runInContext('reset()',context);

 // the rail on a phone sits behind the Questions button; a canned question closes it again
 elements['rail-toggle'].events.click();assert.equal(elements['rail-toggle']['aria-expanded'],'true');assert.equal(elements['rail-toggle'].textContent,'Close');
 chip('Dry-end waste').events.click();assert.equal(elements['rail-toggle']['aria-expanded'],'false');assert.equal(elements['rail-toggle'].textContent,'Questions');await tick();

 // the card's evidence at desktop width: two charts side by side in 480-wide boxes, the speed chart three best and
 // three worst, the first three query lines with the rest behind "N more queries"
 const step=(i,tool,extra={})=>({result_id:'p:'+i,tool,arguments:{shift:2},ok:true,elapsed_ms:1,data:{shift:2,coverage:'complete',rows:[row],trace:{sql:'SELECT 1',parameters:[2]},...extra}});
 const wide={run_id:'wide',session_id:'s',production_day:'2026-09-14',selected_shifts:[2],mode:'offline',status:'complete',context:{},usage:{tool_calls:6,model_calls:0},sections:[],tables:[],
  trace:[step(1,'get_shift_kpis'),step(2,'get_downtime_breakdown'),step(3,'get_wet_end_performance'),step(4,'get_shift_notes'),step(5,'get_quality_breakdown'),step(6,'get_setup_matrix')],
  charts:[{type:'bar',source_result_id:'p:5',shift:2,coverage:'complete',unit:'minutes',x:'category',y:'down_minutes',rows:[{category:'Order change',down_minutes:6.4},{category:'Stacker',down_minutes:2.3}]},
          {shift:2,source_result_id:'p:3',coverage:'complete',unit:'ft/min',x:'wet_end_id',y:'actual_fpm',rows:Array.from({length:12},(_,i)=>({wet_end_id:'WE'+i,actual_fpm:400+i*30,target_fpm:800,speed_to_target_pct:50+i*3,grade:'200-C'}))}]};
 context.wide=wide;context.matchMedia=query=>({matches:/min-width:1320px/.test(query)});
 vm.runInContext('showResult(wide)',context);
 const all=(el,out=[])=>{for(const c of el.children||[]){out.push(c);all(c,out);}return out;};
 assert.equal(elements.charts.className,'pair');assert.equal(elements.charts.children.length,2);
 assert.deepEqual(all(elements.charts).filter(e=>e.tag==='svg').map(e=>e.viewBox.split(' ')[2]),['480','480']);
 assert.equal(all(elements.charts.children[1]).filter(e=>e.class==='bar cur').length,6);                              // three best and three worst of twelve
 assert.equal(all(elements.charts.children[1]).find(e=>e.tag==='button').textContent,'Show all 12');
 assert.deepEqual(elements.charts.children.map(c=>c.children[0].className),['chart-head','chart-head']);             // title and the link back to the query on one row
 const boxes=elements.queries.children.filter(e=>/^query\b/.test(e.className)), more=all(elements.queries).find(e=>e.tag==='button'&&/more quer/.test(e.textContent));
 assert.deepEqual(boxes.map(b=>!!b.hidden),[false,false,false,true,true,true]);assert.equal(more.textContent,'3 more queries');
 all(elements.charts.children[0]).find(e=>e.className==='query-link').events.click();                               // a chart drawn from a folded query unfolds the lines
 assert.ok(boxes.every(b=>!b.hidden));assert.equal(more.textContent,'Expand all');
 vm.runInContext('showResult(wide)',context);const again=all(elements.queries).find(e=>e.tag==='button'&&/more quer/.test(e.textContent));again.events.click();
 assert.ok(elements.queries.children.filter(e=>/^query\b/.test(e.className)).every(b=>!b.hidden));assert.equal(again.textContent,'Expand all');
 context.wide={...wide,charts:[wide.charts[0]]};vm.runInContext('showResult(wide)',context);
 assert.equal(elements.charts.className,'');assert.equal(all(elements.charts).find(e=>e.tag==='svg').viewBox.split(' ')[2],'690');   // one chart keeps the card's width
 delete context.matchMedia;vm.runInContext('reset()',context);
 console.log('UI issue-63 checks passed: rail, ask bar and card frame in order; skeleton in the same task as the submit; measured ms on the title line; three questions make one open card and two lines; nothing disabled; payload unchanged; Ask offline, analyst live; charts side by side.');
}


// ---- issue #26: the /evals page. Every case is listed once, the score and trend come from the board, opening a case
// runs it through POST /api/evals/run and draws the trail with the main page's traceRow, and the page fetches nothing else.
(async()=>{
 const page=fs.readFileSync('src/investigator/web/evals.html','utf8'), src=fs.readFileSync('src/investigator/web/evals.js','utf8');
 const els=Object.fromEntries([...page.matchAll(/id="([^"]+)"/g)].map(m=>[m[1],new Element(m[1])]));
 const run=(id,mode,provider,last,recording)=>({id,mode,provider,last,...(provider?{recording}:{})});
 const kase=(id,type,runs,extra={})=>({id,type,mode:runs[0].mode,also_live:runs.length>1,question:'Why was this shift slow?',turns:['Why was this shift slow?'],call:null,day:'2026-09-14',scope:'shift',shift:2,period:'day',corrupt:false,note:null,known_defect:null,expected:['Ends with status "complete"','Calls get_downtime_breakdown for shift 2 by reason'],runs,...extra});
 const rec={provider:'anthropic',models:['claude-haiku-5-5'],recorded_status:'complete',recorded_cost_usd:0.0047,model_responses:2,stale:false};
 const hist=(ran_at,p50)=>({ran_at,commit:'abc1234',dataset:'d9143ff1d150',passed:4,failed:0,skipped:0,total:4,pass_rate_pct:100,p50_ms:p50,p95_ms:p50===null?null:50,recorded_cost_usd:0.04,models:['claude-haiku-5-5'],exit:0});
 const board={badge:'4/4 golden · 12 ms p50',dataset_hash:'d9143ff1d15029b5',counts:{cases:3,runs:4,by_type:{routing:2,value:1},recordings:2},thresholds:{offline_latency_ms:{p50:100,p95:250}},types:['routing','faithfulness','value'],
  history:[hist('2026-10-29T15:02Z',null),hist('2026-11-09T18:02Z',12.6),hist('2026-11-09T19:21Z',11.5)],last_run:hist('2026-11-09T19:21Z',11.5),
  cases:[kase('why-slow','routing',[run('why-slow','offline',null,{outcome:'pass',ms:11,note:''}),run('why-slow@anthropic','live','anthropic',{outcome:'pass',ms:40,note:''},rec)]),
   kase('live-why-slow','routing',[run('live-why-slow@anthropic','live','anthropic',null,rec)]),
   kase('value-kpis','value',[run('value-kpis','offline',null,{outcome:'fail',ms:300,note:''})],{question:null,turns:[],call:'get_period_kpis for all shifts over the month containing 2026-09-14'})]};
 const result=id=>({id,case:id.split('@')[0],type:'routing',mode:id.includes('@')?'live':'offline',provider:id.includes('@')?'anthropic':null,outcome:'pass',failures:[],note:'complete, 2 tool calls',ms:9.4,ran_at:'2026-11-09T20:00:00Z',model_called:false,
  turns:[{question:'Why was this shift slow?',status:'complete',mode:'offline',sections:['Shift 2: 600 ft/min.'],narrative:[],usage:{tool_calls:2,model_calls:0},trace:[{tool:'get_shift_kpis',ok:true,elapsed_ms:1,arguments:{shift:2},data:{shift:2,coverage:'complete',rows:[{}]}},{tool:'get_downtime_breakdown',ok:true,elapsed_ms:1,arguments:{shift:2,group_by:'reason'},data:{shift:2,coverage:'complete',rows:[]}}]}],
  ...(id.includes('@')?{recording:{...rec,exchanges:[{model:'claude-haiku-5-5',http_status:200,calls:[{tool:'get_shift_overview',arguments:{shift:2}}],text:''},{model:'claude-haiku-5-5',http_status:200,calls:[],text:'done'}]}}:{})});
 const calls=[], urls=[];
 const fetchStub=async(path,init)=>{
  calls.push([init&&init.method||'GET',path,init&&init.body?JSON.parse(init.body):null,init&&init.headers?init.headers['X-Workbench-Token']:undefined]);
  if(path==='/evals.json')return {ok:true,status:200,json:async()=>board};
  if(path==='/api/config')return {ok:true,status:200,json:async()=>({token:'tok'})};
  const body=JSON.parse(init.body);
  if(body.all)return {ok:true,status:200,json:async()=>({ran_at:'2026-11-09T20:01:00Z',total:4,passed:3,failed:1,skipped:0,latency_ms:{p50:10,p95:40},model_called:false,cached:false,rows:board.cases.flatMap(c=>c.runs).map(r=>({id:r.id,case:r.id.split('@')[0],type:'routing',provider:r.provider,outcome:r.id==='value-kpis'?'fail':'pass',failures:[],note:'',ms:10}))})};
  return {ok:true,status:200,json:async()=>result(body.id)};
 };
 const doc={getElementById:id=>{assert.ok(els[id],`Unknown evals control ${id}`);return els[id];},createElement:tag=>new Element(tag),createElementNS:(_,tag)=>new Element(tag)};
 const ctx={document:doc,fetch:fetchStub,window:{innerWidth:390,location:{search:'?case=why-slow%40anthropic'},history:{replaceState:(a,b,url)=>urls.push(url)}},console,URLSearchParams};
 vm.createContext(ctx);vm.runInContext(fs.readFileSync('src/investigator/web/trail.js','utf8'),ctx);vm.runInContext(src,ctx);
 await vm.runInContext('ready',ctx);
 const all=(el,out=[])=>{for(const c of el.children||[]){out.push(c);all(c,out);}return out;};
 // every case once, with the last tracked result on its chip
 assert.deepEqual(els.cases.children.map(d=>d.id),['case-why-slow','case-live-why-slow','case-value-kpis']);
 assert.match(els.summary.textContent,/^4\/4 golden · 12 ms p50 · 3 cases, 4 runs \(1 cases run both offline and against a recording\) · last tracked run 2026-11-09 19:21 UTC at commit abc1234 · dataset d9143ff1d150$/);
 assert.equal(els['case-count'].textContent,'3 of 3 cases');
 const chipTexts=d=>all(d.children[0]).filter(e=>/^outcome\b/.test(e.className)).map(e=>e.textContent);
 assert.deepEqual(chipTexts(els.cases.children[2]),['offline · fail · 300 ms']);
 assert.deepEqual(chipTexts(els.cases.children[1]),['anthropic replay · not in the last tracked run']);
 // the score table: a row per type that has cases, then the total over runs
 const scoreRows=els['score-table'].children[1].children.map(tr=>tr.children.map(td=>td.textContent));
 assert.deepEqual(scoreRows.map(r=>[r[0],r[2],r[3],r[4]]),[['routing','2','2/2','—'],['value','1','0/1','—'],['all','3','2/3','—']]);
 // the trend: one chart per measure; the run without a recorded p50 is left out of the latency line and said so
 const [rate,latency]=els['trend-charts'].children;
 assert.equal(all(rate).filter(e=>e.class==='dot').length,3);assert.equal(all(latency).filter(e=>e.class==='dot').length,2);
 assert.equal(all(rate).filter(e=>e.class==='line').length,1);assert.ok(all(latency).some(e=>e.tag==='text'&&e.textContent==='11.5 ms'));
 assert.ok(all(latency).some(e=>/1 earlier run did not record it/.test(e.textContent)));assert.equal(els['trend-note'].textContent,'3 tracked runs');
 // ?case=why-slow@anthropic opened that case on load and ran both of its rows here, with the token
 const first=els.cases.children[0];
 assert.equal(first.open,true);
 assert.deepEqual(calls.filter(c=>c[0]==='POST').map(c=>[c[1],c[2].id,c[3]]),[['/api/evals/run','why-slow','tok'],['/api/evals/run','why-slow@anthropic','tok']]);
 assert.equal(all(first).filter(e=>/^trace-step ok$/.test(e.className)).length,4);          // two trail rows per run, drawn by the main page's traceRow
 assert.ok(all(first).some(e=>e.className==='tool'&&e.textContent==='get_downtime_breakdown'));
 assert.ok(all(first).some(e=>/Calls get_downtime_breakdown for shift 2 by reason/.test(e.textContent)));   // expected, in plain words, beside it
 assert.ok(all(first).some(e=>/recorded cost \$0\.0047\. No model was called now/.test(e.textContent)));    // the recording and its cost
 assert.deepEqual(chipTexts(first),['offline · pass · 9 ms','anthropic replay · pass · 9 ms']);
 // opening a second case runs it once; reopening does not run it again; "Run again" does
 const second=els.cases.children[1];second.open=true;await second.events.toggle();await second.events.toggle();
 assert.equal(calls.filter(c=>c[2]&&c[2].id==='live-why-slow@anthropic').length,1);assert.equal(urls[urls.length-1],'/evals?case=live-why-slow');
 await all(second).find(e=>e.tag==='button'&&e.textContent==='Run again').events.click();
 assert.equal(calls.filter(c=>c[2]&&c[2].id==='live-why-slow@anthropic').length,2);
 // the type filter hides the other cases and is carried in the URL
 els['type-filter'].children.find(b=>b.dataset.value==='value').events.click();
 assert.deepEqual(els.cases.children.map(d=>d.hidden),[true,true,false]);assert.equal(els['case-count'].textContent,'1 of 3 cases');assert.equal(urls[urls.length-1],'/evals?type=value');
 els.search.value='nothing-matches';els.search.events.input();assert.equal(els['case-count'].textContent,'0 of 3 cases');
 els.search.value='';els['type-filter'].children[0].events.click();assert.equal(els['case-count'].textContent,'3 of 3 cases');
 // Run all: one POST, the chips and the "this server, now" column follow it
 await els['run-all'].events.click();
 assert.deepEqual(calls[calls.length-1].slice(0,3),['POST','/api/evals/run',{all:true}]);
 assert.match(els['run-status'].textContent,/3\/4 passed, 1 failed \(value-kpis\) · offline p50 10 ms, p95 40 ms · no model called$/);
 assert.deepEqual(els['score-table'].children[1].children.map(tr=>tr.children[4].textContent),['3/3','0/1','3/4']);
 assert.equal(els['run-all'].disabled,false);
 // no model calls from the page: it talks to these three paths on its own origin and nothing else
 assert.deepEqual([...new Set(calls.map(c=>c[0]+' '+c[1]))].sort(),['GET /api/config','GET /evals.json','POST /api/evals/run']);
 assert.deepEqual([...new Set([...src.matchAll(/fetch\(([^,)]+)/g)].map(m=>m[1]))].sort(),["'/api/config'","'/api/evals/run'","'/evals.json'"]);
 assert.ok(!/https?:\/\//.test(src)||!/anthropic\.com|openai\.com/.test(src));assert.ok(!/X-(OpenAI|Anthropic)-Key/.test(src));
 // the page is built for a phone: a viewport meta, a 420-wide chart box under 850 px, single-column grids in the stylesheet
 assert.ok(page.includes('name="viewport" content="width=device-width, initial-scale=1"'));
 assert.ok(all(rate).some(e=>e.tag==='svg'&&e.viewBox==='0 0 420 200'));
 assert.match(css,/@media\(max-width:850px\)\{\s*#trend-charts,\.case-grid\{grid-template-columns:1fr\}/);
 // and the main page carries the badge
 assert.match(html,/<a id="evals-badge" class="evals-badge" href="\/evals" hidden><\/a>/);
 assert.equal(elements['evals-badge'].textContent,'160/160 golden · 12 ms p50 →');assert.equal(elements['evals-badge'].hidden,false);assert.equal(elements['evals-foot'].hidden,false);   // set by init() from /api/config
 assert.match(elements['evals-badge'].title,/last tracked run 2026-11-09T19:21Z at commit abc1234/);
 console.log('UI issue-26 checks passed: every case once, score table, trend, case run with the shared trail row, filter, Run all, no fetch beyond the three paths.');
})().catch(error=>{console.error(error);process.exit(1);});
