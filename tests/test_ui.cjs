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
const context={document,fetch:async()=>({ok:true,json:async()=>({days:['2026-09-01','2026-09-30'],token:'test'})}),window:{addEventListener(){}},console,setTimeout,URL,Blob,AbortController};
vm.createContext(context);vm.runInContext(fs.readFileSync('src/investigator/web/app.js','utf8'),context);
vm.runInContext('updateScope()',context);assert.equal(elements['scope-help'].textContent,'Monday 14 Sep 2026 · shift 2 · 15:00–23:00 · compared with Friday 11 Sep');
elements.scope.value='day';vm.runInContext('updateScope()',context);assert.match(elements['scope-help'].textContent,/all three shifts · 24 hours from 07:00 · compared with Friday 11 Sep/);
elements.scope.value='shift';vm.runInContext('updateScope()',context);assert.match(elements['scope-help'].textContent,/shift 2/);
elements.mode.value='openai';vm.runInContext('updateScope()',context);assert.equal(elements['key-label'].hidden,false);
// A week, month or 4-4-5 period keeps the question box, the agent select and the live key: the period tools answer
// questions about the selected window; the deterministic report moves to its own button.
elements.period.value='month';elements.period.events.change();assert.equal(elements.mode.value,'openai');assert.equal(elements['key-label'].hidden,false);
elements.period.value='week';vm.runInContext('updateScope()',context);assert.equal(elements.question.disabled,false);assert.equal(elements.mode.disabled,false);assert.equal(elements.report.hidden,false);assert.equal(elements.submit.textContent,'Investigate');assert.match(elements['mode-help'].textContent,/^Ask about the selected week/);
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
vm.runInContext('showResult(result)',context);assert.equal(elements.metrics.children.length,11);assert.equal(elements.pdf.disabled,false);assert.equal(elements.tables.children.length,1);assert.equal(elements.charts.children.length,1);
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
vm.runInContext('reset()',context);assert.equal(elements.pdf.disabled,true);assert.equal(elements.tables.children.length,0);
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
console.log('UI unit checks passed: scope sentence, weekend rejection, segments, chip groups, KPI tiles, charts, trail row, note folding, heatmap, PDF state.');

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
for(const k of ['s1','s2','s3','s4','prev','target'])assert.ok(onPanel[k]>=3,`${k} ${lightTokens[k]} is ${onPanel[k]}:1 on the chart panel ${panel}`);
for(const k of ['ink','ink-2'])assert.ok(onPanel[k]>=4.5,`${k} is ${onPanel[k]}:1`);   // chart labels, ticks, captions and legend text
for(const k of ['s1','s2','s3'])assert.ok(contrast(lightTokens[k],'#000000')>=4.5,`black segment label on ${k}`);assert.ok(contrast(lightTokens.s4,'#ffffff')>=4.5,'white segment label on s4');   // .inseg inks
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
})().catch(error=>{console.error(error);process.exit(1);});
