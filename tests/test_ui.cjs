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
const context={document,fetch:async()=>({ok:true,json:async()=>({days:['2026-09-01','2026-09-30'],token:'test',evals:{badge:'160/160 golden · 12 ms p50',ran_at:'2026-11-09T19:21Z',commit:'abc1234'}})}),window:{addEventListener(){}},console,setTimeout,URL,URLSearchParams,Blob,AbortController};
vm.createContext(context);vm.runInContext(fs.readFileSync('src/investigator/web/trail.js','utf8'),context);vm.runInContext(fs.readFileSync('src/investigator/web/app.js','utf8'),context);   // trail.js first, as index.html loads them
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
// Public demo with only the Anthropic server key: the Agent control is offline-only and Anthropic is what "Ask the analyst" uses.
const options={openai:new Element('option'),anthropic:new Element('option')};for(const [v,o] of Object.entries(options)){o.value=v;o.removed=false;o.remove=()=>{o.removed=true;};}
elements.mode.querySelector=sel=>{const v=sel.match(/value="(\w+)"/)[1];return options[v]&&!options[v].removed?options[v]:null;};
vm.runInContext("config={public:true,live_modes:['anthropic'],live_model_available:true,days:['2026-09-01','2026-09-30'],token:'t'};applyPublicMode()",context);
assert.equal(elements.mode.value,'offline');/* issue #42: offline is the default even with a server key *//* issue #23: the model is a button on each answer, so both live options leave the control */assert.equal(options.anthropic.removed,true);assert.equal(options.openai.removed,true);assert.equal(elements.mode.disabled,true);assert.equal(elements['key-label'].hidden,true);assert.match(elements['mode-help'].textContent,/Ask the analyst.*Anthropic/);assert.equal(vm.runInContext('analystMode()',context),'anthropic');
elements.mode.querySelector=()=>null;
// Public demo: with the server's model available the default stays offline (issue #42) and the model is a choice; without it the OpenAI option is gone and offline is forced.
vm.runInContext("config={public:true,live_model_available:true,days:['2026-09-01','2026-09-30'],token:'t'};applyPublicMode()",context);assert.equal(elements.mode.value,'offline');assert.equal(elements['key-label'].hidden,true);assert.match(elements['mode-badge'].textContent,/model available/);
elements.mode.value='openai';vm.runInContext("config={public:true,live_model_available:false,days:['2026-09-01','2026-09-30'],token:'t'};applyPublicMode()",context);assert.equal(elements.mode.value,'offline');assert.equal(elements.mode.disabled,true);
vm.runInContext("config={days:['2026-09-01','2026-09-30'],token:'test'}",context);elements.mode.disabled=false;elements.mode.value='offline';
const row={observed_shift_fpm:600,speed_to_target_pct:75,valid_setups:60,paper_changes:30,maintenance_pct:3,operator_pct:1,dry_end_pct:1.2,trim_pct:2,shear_pct:.5,throughput_in:94,lineal_per_setup:4800};
context.result={run_id:'test',session_id:'session',production_day:'2026-09-14',selected_shifts:[2],mode:'offline',status:'complete',context:{shift:2},usage:{tool_calls:1,model_calls:0},trace:[{tool:'get_shift_kpis',ok:true,elapsed_ms:1,data:{shift:2,coverage:'complete',rows:[row]}}],tables:[{title:'get_shift_notes',shift:2,coverage:'complete',rows:[{notes:'<script>untrusted</script>'}]}],charts:[{title:'Output heatmap',type:'calendar_heatmap',coverage:'complete',unit:'estimated good sq ft',rows:[{day:'2026-09-14',available:true,estimated_good_sqft:1000000,gross_sqft:1020000,speed_to_target_pct:75,downtime_pct:4,maintenance_pct:3,operator_pct:1,dry_end_pct:1.2,trim_pct:2,shear_pct:.5,paper_changes:27,valid_setups:60,heavy_hitter:'Upper knife / Knife jam',heavy_hitter_minutes:12,heavy_hitter_stops:3}]},{shift:2,coverage:'complete',unit:'ft/min',x:'wet_end_id',y:'actual_fpm',rows:[{wet_end_id:'W2-01',actual_fpm:600,target_fpm:800,speed_to_target_pct:75,grade:'200-C'}]}]};
const first=context.result.charts[0].rows[0];
context.result.charts[0].rows.push({...first,day:'2026-09-15',estimated_good_sqft:1500000},{...first,day:'2026-09-16',estimated_good_sqft:2000000},{day:'2026-09-17',available:false});
vm.runInContext('showResult(result)',context);assert.equal(elements.metrics.children.length,7);assert.equal(elements.tables.children.length,1);assert.equal(elements.charts.children.length,1);
// issue #63: the eleven numbers of the KPI row sit on seven tiles, one row of the card (value, unit, and a second number beneath)
assert.deepEqual(elements.metrics.children.map(t=>[t.children[0].textContent,t.children[1].children.map(c=>c.textContent).join(' '),t.children[2].textContent]),[['Speed','600.0 ft/min','75.0% of target'],['Maintenance','3.00 %','target 2.5%'],['Operator','1.00 %','target 2.5%'],['Dry-end waste','1.20 %','above 1%'],['Trim','2.00 %','shear 0.50%'],['Setups','60','30 paper changes'],['Throughput','94.0 in','4,800 ft / setup']]);
assert.deepEqual(elements.metrics.children.filter(t=>/flag/.test(t.children[2].className)).map(t=>t.children[0].textContent),['Speed','Maintenance','Dry-end waste']);   // under target, over 2.5%, over 1%
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
vm.runInContext('reset()',context);assert.equal(elements['card-foot'].hidden,true);assert.equal(elements.tables.children.length,0);   // no answer, no exports: the footer is hidden, never a disabled button
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

// ---- Segmented buttons mirror the hidden selects; the canned questions are a short list plus "More questions".
vm.runInContext('renderSegments();renderQuestionChips()',context);
const pressed=group=>elements[group+'-seg'].children.filter(b=>b['aria-pressed']==='true').map(b=>b.dataset.value);
assert.deepEqual(elements['period-seg'].children.map(b=>b.dataset.value),['day','week','month','accounting']);
assert.deepEqual(elements['shift-seg'].children.map(b=>b.textContent),['1 · 7–3','2 · 3–11','3 · 11–7','All']);   // the rail's shift buttons carry the hours (#63)
assert.deepEqual(pressed('period'),['day']);assert.deepEqual(pressed('shift'),['2']);
elements['shift-seg'].children.find(b=>b.dataset.value==='all').events.click();assert.equal(elements.scope.value,'day');assert.deepEqual(pressed('shift'),['all']);
elements['shift-seg'].children.find(b=>b.dataset.value==='1').events.click();assert.equal(elements.scope.value,'shift');assert.equal(elements.shift.value,'1');
elements['shift-seg'].children.find(b=>b.dataset.value==='2').events.click();
elements['period-seg'].children.find(b=>b.dataset.value==='month').events.click();assert.equal(elements.period.value,'month');assert.equal(elements.question.disabled,false);
// Under a period the questions follow the window word and the per-shift matrices are hidden; under Day everything shows.
const chipList=()=>[...elements.examples.children,...elements['examples-more'].children];
assert.ok(chipList().some(b=>b.textContent==='Costliest reason this month'&&b.dataset.question==='Which downtime reason cost the most minutes this month?'&&!b.hidden));
assert.ok(chipList().some(b=>b.textContent==='This month vs last'&&b.dataset.question==='How did this month go versus the previous one?'));
assert.deepEqual(chipList().filter(b=>b.hidden).map(b=>b.textContent),['Setup matrix','Read notes','Compare shifts','Rank losses','Orders + quantities']);
assert.equal(chipList().filter(b=>b.disabled).length,0);
assert.equal(elements.report.hidden,false);
elements['period-seg'].children.find(b=>b.dataset.value==='accounting').events.click();assert.ok(chipList().some(b=>b.dataset.question==='Stops per day this period'));
elements['period-seg'].children.find(b=>b.dataset.value==='day').events.click();assert.equal(elements.period.value,'day');assert.equal(chipList().filter(b=>b.hidden).length,0);assert.ok(chipList().some(b=>b.textContent==='Costliest reason this week'));
assert.deepEqual(elements.examples.children.map(b=>b.textContent),['Why was this shift slow?','Speed to target','Dry-end waste','Bond or warp?','Costliest reason this week','This week vs last','Summary + charts']);   // the short list; the other nine sit under "More questions"
assert.equal(elements['examples-more'].children.length,9);
const chips=chipList();
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

// ---- issue #40: the query box is the first thing below the question, step 1 open, built in the first paint; the
// per-step data ("What came back") stays below and keeps #33's two-frame deferral; the copy text is the SQL with its
// bound values; charts and tables link back to their query box; on a phone every box is one line with the SQL on tap.
{
 const at=id=>html.indexOf(`id="${id}"`);
 assert.ok(at('ask')<at('card-title')&&at('card-title')<at('queries')&&at('queries')<at('messages'),'the ask bar, the title line, the query box directly under it, then the narrative');
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
 assert.ok(walk(q[q.length-1]).some(e=>e.textContent==='3 queries · 4 ms in the tools'));   // the tools' own time; the title line carries the wall time (#63)
 assert.deepEqual(boxes.map(b=>b.id),['query-0','query-1','query-2']);       // one block per tool step, in order, right after the head
 assert.deepEqual(boxes.map(b=>b.open),[true,false,false]);                   // step 1 open; the others one line with the SQL on tap
 assert.equal(q[q.length-1].className,'queries-foot');
 boxes[1].open=true;boxes[1].events.toggle();assert.deepEqual(boxes.map(b=>b.open),[false,true,false]);boxes[1].open=false;boxes[0].open=true;boxes[0].events.toggle();   // one open at a time: at desktop width its SQL sits in the pane beside the list (#63)
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
 assert.deepEqual(elements.queries.children.filter(e=>/^query\b/.test(e.className)).map(b=>!!b.hidden),[false,true,true]);   // the mockup's phone shows one query line; the rest fold behind "2 more queries" (#63)
 assert.ok(walk(elements.queries).some(e=>e.textContent==='2 more queries'));
 delete context.matchMedia;
 const phoneCss=fs.readFileSync('src/investigator/web/style.css','utf8');const phoneBlock=phoneCss.slice(phoneCss.indexOf('@media(max-width:850px)'));
 assert.ok(phoneBlock.includes('.query>summary .args{display:none}')&&phoneBlock.includes('.query>summary{flex-wrap:nowrap;overflow:hidden}'),'at 390 px each step is tool · rows · ms on one line; the SQL is on tap');
 // #63: more than four steps fold behind "N more queries"; the button shows them
 vm.runInContext('showResult(Object.assign({},result,{trace:[...result.trace,...result.trace].map((e,i)=>Object.assign({},e,{result_id:"x:"+i}))}))',context);
 const six=elements.queries.children.filter(e=>/^query\b/.test(e.className));assert.deepEqual(six.map(b=>!!b.hidden),[false,false,false,false,true,true]);
 const moreQ=walk(elements.queries).find(e=>e.textContent==='2 more queries');moreQ.events.click();assert.ok(six.every(b=>!b.hidden));assert.equal(moreQ.textContent,'Show fewer');assert.equal(moreQ['aria-expanded'],'true');
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
const onPanel=Object.fromEntries(['s1','s2','s3','s4','prev','target','bar','bar-2','ink','ink-2'].map(k=>[k,Number(contrast(lightTokens[k],panel).toFixed(2))]));
for(const k of ['s1','s2','s3','s4','prev','target','bar','bar-2'])assert.ok(contrast(lightTokens[k],panel)>=3,`${k} ${lightTokens[k]} is ${onPanel[k]}:1 on the chart panel ${panel}`);
for(const k of ['ink','ink-2'])assert.ok(contrast(lightTokens[k],panel)>=4.5,`${k} is ${onPanel[k]}:1`);   // chart labels, ticks, captions and legend text
for(const k of ['s1','s2'])assert.ok(contrast(lightTokens[k],lightTokens['surface-3'])>=3,`${k} on the target band ${lightTokens['surface-3']}`);   // range-chart bars run from zero across the --surface-3 band
assert.match(css,/\.chart \.inseg\{fill:#000;/);assert.match(css,/\.chart \.inseg\.s4\{fill:#fff\}/);   // segment labels: black on s1..s3, white on s4
for(const k of ['s1','s2','s3'])assert.ok(contrast(lightTokens[k],'#000000')>=4.5,`black segment label on ${k}`);assert.ok(contrast(lightTokens.s4,'#ffffff')>=4.5,'white segment label on s4');
const blend=(a,[r,g,b],bg)=>'#'+[r,g,b].map((v,i)=>Math.round(a*v+(1-a)*parseInt(bg.slice(1+2*i,3+2*i),16)).toString(16).padStart(2,'0')).join('');
for(const a of [.96,.965]){const cell=blend(a,[8,127,117],panel);assert.ok(contrast(cell,'#000000')>=4.5&&contrast(cell,'#ffffff')>=4.5,`heatmap inks at alpha ${a} on the chart panel`);}   // the ink flip holds on the panel too
assert.match(css,/\.chart \.tick\{[^}]*fill:var\(--ink-2\)/);assert.match(css,/\.chart \.muted,\.heatmap \.muted\{color:var\(--ink-2\)\}/);
console.log('light-theme contrast on the chart panel (--surface-2):',JSON.stringify(onPanel));
console.log('UI issue-36 checks passed: light by default, pinned theme wins, toggle present, heatmap ink flip.');

// ---- issue #23 / #67 in the shell: every question is answered offline first; "Ask the analyst" (the ask bar's button, or
// the one under an offline answer) opens its own card, which fills from the stream: the planned queries with their
// progress, the paragraphs as they are written, a withdrawn paragraph gone, then the validated result. A refused
// plan keeps the offline evidence on the analyst's card; a mid-stream error says so and fabricates nothing.
const analystChecks=(async()=>{
 await new Promise(r=>setTimeout(r,20));
 const flat=e=>[e.textContent||'',...(e.children||[]).map(flat)].join(' ');
 const find=(e,cls)=>((e.className||'').split(' ').includes(cls)?[e]:[]).concat(...(e.children||[]).map(c=>find(c,cls)));
 const step={ok:true,tool:'get_shift_notes',arguments:{shift:2},result_id:'run9:1',elapsed_ms:1.5,data:{rows:[],shift:2,coverage:'complete',excluded_records:[]}};
 const answer=(extra)=>({session_id:'s1',run_id:'r1',mode:'offline rule-based planner; no language model',status:'complete',production_day:'2026-09-14',selected_shifts:[2],sections:['Shift 2: evidence line.'],trace:[step],tables:[],charts:[],context:{},usage:{tool_calls:1,model_calls:0,cost_usd:0},narrative:[],narrative_status:'not_requested',url:'/corr/1/day/2026-09-14/shift/2?measure=notes',...extra});
 const bodies=[], keys=[], gets=[]; let events=[], gated=false; const releases=[];
 const ev=e=>JSON.stringify(e)+'\n';
 context.TextDecoder=TextDecoder;
 context.fetch=async(path,init)=>{
  if(path==='/api/config')return {ok:true,status:200,json:async()=>({token:'t'})};
  if(!init||!init.body){ gets.push({path,headers:init&&init.headers}); return {ok:true,status:200,json:async()=>answer({})}; }   // offline: a GET of the question's address (#20)
  const body=JSON.parse(init.body); bodies.push(body); keys.push(init.headers['X-Anthropic-Key']);
  const chunks=events.map(e=>new TextEncoder().encode(e)), split=[]; for(const c of chunks)for(let i=0;i<c.length;i+=3)split.push([c.slice(i,i+3),i+3>=c.length]);   // three bytes at a time: events split across reads, as on the wire
  let at=0;
  return {ok:true,status:200,body:{getReader:()=>({read:async()=>{ if(at>=split.length)return {done:true}; const [value,last]=split[at++]; if(gated&&last)await new Promise(r=>releases.push(r)); return {done:false,value}; }})}};
 };
 const tick=()=>new Promise(r=>setTimeout(r,0)), release=async n=>{ for(let i=0;i<n;i++){ while(!releases.length)await tick(); releases.shift()(); await tick(); } await tick(); };
 vm.runInContext("config={public:true,live_modes:['anthropic'],live_model_available:true,days:['2026-09-01','2026-09-30'],token:'t',demo_guards:{rate_limit_per_min:30}};reset()",context);
 elements.mode.value='anthropic';   // even a stale live value in the control cannot make a question a model call
 await vm.runInContext("ask('Why was this shift slow?')",context);
 assert.equal(bodies.length,0);assert.match(gets[0].path,/^\/q\?day=.*&q=Why\+was\+this\+shift\+slow%3F/);assert.equal(gets[0].headers['X-Anthropic-Key'],undefined);
 const offlineCard=()=>find(elements.messages,'analyst-ask');
 assert.equal(offlineCard().length,1);assert.equal(offlineCard()[0].textContent,'Ask the analyst');assert.match(flat(elements.messages),/Anthropic picks the queries in one call.*site's key under a spend cap/);
 const offlineQueries=elements.queries.children.length;
 // the analyst under the answer: its card fills from the stream, event by event
 const done=answer({session_id:'s2',url:undefined,mode:'Anthropic (claude-haiku-5-5) analyst: one planning call, calculated evidence, streamed narrative',usage:{tool_calls:1,model_calls:2,cost_usd:0.0004,first_token_ms:1200},narrative_status:'available',narrative:[{kind:'finding',text:'Shift 2 ran at 512.34 ft/min.',citations:['Shift 2, observed_shift_fpm, run9:1, row 0'],charts:[]}]});
 events=[ev({type:'start',mode:'anthropic',session_id:'s2'}),ev({type:'plan',steps:[{tool:'get_shift_notes',arguments:{shift:2}}]}),ev({type:'step',phase:'start',index:0,tool:'get_shift_notes',arguments:{shift:2}}),ev({type:'step',phase:'finish',index:0,tool:'get_shift_notes',arguments:{shift:2},ok:true,rows:4,ms:1.5,result_id:'run9:1'}),
  ev({type:'delta',paragraph:0,kind:'finding',text:'Shift 2 ran at '}),ev({type:'delta',paragraph:0,kind:'finding',text:'512.34 '}),ev({type:'delta',paragraph:1,kind:'hypothesis',text:'It lost '}),ev({type:'retract',paragraph:1,reason:'typed number'}),
  ev({type:'paragraph',paragraph:0,kind:'finding',text:'Shift 2 ran at 512.34 ft/min.',citations:[]}),ev({type:'done',result:done})];
 gated=true; offlineCard()[0].events.click();
 assert.equal(elements.card.className,'card answer skeleton');assert.match(elements['card-meta'].textContent,/^Shift 2 · .* · analyst · asking…$/);   // its own card, in the task of the click
 await tick();assert.equal(elements.card.className,'card answer streaming');assert.match(flat(elements.messages),/Planning the queries…/);   // the stream is open
 await release(2);   // start, plan
 assert.equal(elements.card.className,'card answer streaming');
 assert.match(flat(elements.queries),/Queries the analyst planned.*1 query · running.*get_shift_notes.*· queued/);
 await release(2);   // the step starts and finishes
 assert.match(flat(elements.queries),/· 4 rows · 1\.5 ms/);assert.match(flat(elements.messages),/Writing the interpretation…/);
 await release(3);   // two deltas of paragraph 0, one of paragraph 1
 assert.ok(find(elements.messages,'streaming').some(p=>p.textContent==='Finding: Shift 2 ran at 512.34 '));assert.match(flat(elements.messages),/Hypothesis: It lost/);
 await release(1);   // paragraph 1 withdrawn
 assert.ok(!/It lost/.test(flat(elements.messages)));
 gated=false; await release(0); while(releases.length)releases.shift()(); await new Promise(r=>setTimeout(r,30));
 assert.equal(bodies[0].mode,'anthropic');assert.equal(bodies[0].session_id,null);assert.equal(keys[0],undefined);   // public: never a visitor key
 const text=flat(elements.messages);
 assert.equal(elements.card.className,'card answer');assert.match(elements['card-meta'].textContent,/ · analyst · 2 model calls · (\d+ ms|\d+\.\d s)$/);
 assert.match(text,/Finding: Shift 2 ran at 512\.34 ft\/min\./);assert.match(text,/Evidence: Shift 2, observed_shift_fpm, query 1, row 0/);
 assert.ok(!/It lost/.test(text));assert.match(text,/2 model calls/);assert.match(text,/first words after 1\.2 s/);
 assert.equal(vm.runInContext('sessionId',context),'s2');assert.equal(vm.runInContext('latest.session_id',context),'s2');
 assert.equal(offlineCard().length,0);                                 // the analyst's card offers no second round
 elements.earlier.children[0].children[1].events.click();assert.equal(offlineCard().length,0);   // and the offline card has had its one
 // a refused plan: the notes are shown, and the offline evidence it was asked from stays on the analyst's card
 await vm.runInContext("ask('Why was this shift slow?')",context);
 const source=vm.runInContext('latest',context);
 const rejected=answer({session_id:'s3',url:undefined,status:'plan_rejected',trace:[],sections:['Investigation stopped: plan_rejected. Any results below are partial.',"The analyst's plan was rejected (it names \"run_sql\", which is not one of the permitted tools); no query was run for it. The offline answer stands on its own."],usage:{tool_calls:0,model_calls:1,cost_usd:0.0001}});
 events=[ev({type:'start',mode:'anthropic',session_id:'s3'}),ev({type:'done',result:rejected})];
 offlineCard()[0].events.click(); await new Promise(r=>setTimeout(r,30));
 assert.equal(vm.runInContext('latest',context),source);assert.equal(elements.queries.children.length,offlineQueries);
 assert.match(flat(elements.messages),/plan was rejected .*run_sql.*offline answer stands/);
 // a mid-stream error from the ask bar's analyst: the card fails, says nothing was fabricated, and no control is left disabled
 events=[ev({type:'start',mode:'anthropic',session_id:'s3'}),ev({type:'plan',steps:[{tool:'get_shift_notes',arguments:{shift:2}},{tool:'get_shift_kpis',arguments:{shift:2}}]}),ev({type:'error',error:'Investigation failed; no answer was fabricated'})];
 await assert.rejects(vm.runInContext("ask('Why slow, really?',{mode:'anthropic'})",context),/no answer was fabricated/);
 assert.equal(elements.card.className,'card answer failed');assert.match(flat(elements.messages),/no answer was fabricated.*offline answer stands/);
 assert.deepEqual(Object.entries(elements).filter(([id,e])=>e.disabled&&!['rates-change','mode','day-prev','day-next'].includes(id)).map(([id])=>id),[]);
 // loopback: the provider beside the key field is the analyst, and the key travels with that one request only
 vm.runInContext("config={days:['2026-09-01','2026-09-30'],token:'test'}",context);
 elements.mode.value='offline';assert.equal(vm.runInContext('analystMode()',context),null);
 elements.mode.value='anthropic';elements['api-key'].value='sk-ant-visitor';events=[ev({type:'start',mode:'anthropic',session_id:'s3'}),ev({type:'done',result:rejected})];
 const sent=bodies.length;await vm.runInContext("ask('Why was this shift slow?')",context);assert.equal(bodies.length,sent);assert.equal(gets[gets.length-1].headers['X-Anthropic-Key'],undefined);assert.equal(elements['api-key'].value,'sk-ant-visitor');
 await vm.runInContext("ask('Why was this shift slow?',{mode:'anthropic'})",context);assert.equal(keys[keys.length-1],'sk-ant-visitor');assert.equal(elements['api-key'].value,'');
 elements.mode.value='offline';vm.runInContext('reset()',context);
 console.log('UI issue-23 checks passed: offline first, the analyst opens its own card, streamed plan/steps/narrative fill it, withdrawn paragraph gone, refused plan keeps the offline evidence, a stream error fabricates nothing.');
})().catch(error=>{console.error(error);process.exit(1);});

// ---- issue #17: post() refreshes /api/config once on a 403 and resends with the new token; a second 403 is returned, not retried forever.
const issue17=(async()=>{
 await analystChecks;
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
})();issue17.catch(error=>{console.error(error);process.exit(1);});

// ---- issue #63: the layout shell. A rail (scope, canned questions, rates, agent under a disclosure), an ask bar,
// one open answer card with a title line, earlier answers as one line each. A submit draws the skeleton card
// before the task yields; nothing is disabled while a question is out; the payload to /api/ask is unchanged.
const issue63=issue17.then(async()=>{
 const at=id=>html.indexOf(`id="${id}"`), text=e=>e.textContent;
 const order=['rail','day','shift-seg','period-seg','examples','rates-change','advanced','mode','reset','ask','question','submit','analyst','card','card-title','card-meta','queries','messages','charts','kpis','metrics','card-foot','earlier'];
 assert.deepEqual([...order].sort((a,b)=>at(a)-at(b)),order,'rail, then the ask bar, then the card (title line, queries, summary, charts, tiles, footer), then the earlier answers');
 assert.ok(order.every(id=>at(id)>0));
 for(const phrase of ['Looking at','Ask in one click','Every canned question is a fixed query. No model, no wait.','Your rates','$55 / MSF','$1,200 / hr','not set'])assert.ok(html.includes(phrase),phrase);
 assert.match(html,/<button id="rates-change"[^>]*disabled[^>]*>Change rates<\/button>/);          // the rates panel is a placeholder until #31
 assert.ok(html.indexOf('</details>',at('advanced'))>at('reset'),'agent choice and Advanced sit inside the disclosure');
 assert.match(html,/<select id="mode"><option value="offline">/);                                 // offline is the first option, so the default
 assert.ok(!/id="(examples-fold|scope-bar)"|class="grid"/.test(html),'the old form / help / examples block is gone');
 // the stylesheet: charts two-up and tiles seven-up at desktop width; on a phone the rail is a sheet behind the Questions button
 const desktopCss=css.slice(css.indexOf('@media(min-width:1180px)'),css.indexOf('}\n',css.indexOf('@media(min-width:1180px)')+200)+800);
 assert.ok(desktopCss.includes('#charts{grid-template-columns:repeat(2,minmax(0,1fr))}')&&desktopCss.includes('.metrics{grid-template-columns:repeat(7,minmax(0,1fr))}'));
 const phoneCss=css.slice(css.indexOf('@media(max-width:850px)'));
 assert.ok(phoneCss.includes('.rail{display:none}')&&phoneCss.includes('.rail.open{display:flex;position:fixed;inset:0'));
 assert.ok(/\n\.rail-toggle,\.rail-close\{display:none\}/.test(css)&&phoneCss.includes('.rail-toggle,.rail-close{display:inline-flex'));
 elements['rail-toggle'].events.click();assert.equal(elements.rail.className,'rail open');assert.equal(elements['rail-toggle']['aria-expanded'],'true');
 elements['rail-close'].events.click();assert.equal(elements.rail.className,'rail');

 const storeDays=['2026-09-11','2026-09-14','2026-09-15'];
 const answer=n=>({run_id:'run'+n,session_id:'s1',session_reset:false,mode:'offline rule-based planner; no language model',status:'complete',production_day:'2026-09-14',selected_shifts:[2],sections:[`Shift 2 answer ${n}.`],context:{},usage:{tool_calls:2,model_calls:0},report_markdown:'# r',
  trace:[{result_id:'a:1',tool:'get_shift_kpis',arguments:{shift:2},ok:true,elapsed_ms:3,data:{shift:2,coverage:'complete',rows:[row],trace:{sql:'SELECT 1',parameters:[2]}}},{result_id:'a:2',tool:'get_downtime_breakdown',arguments:{shift:2,group_by:'reason'},ok:true,elapsed_ms:2,data:{shift:2,coverage:'complete',rows:[{category:'Order change',down_minutes:6.4}]}}],tables:[],
  charts:[{type:'bar',source_result_id:'a:2',shift:2,coverage:'complete',unit:'minutes',x:'category',y:'down_minutes',rows:[{category:'Order change',down_minutes:6.4}]},{shift:2,coverage:'complete',unit:'ft/min',x:'wet_end_id',y:'actual_fpm',rows:[{wet_end_id:'W2-01',actual_fpm:600,target_fpm:800,speed_to_target_pct:75,grade:'200-C'}]}]});
 const bodies=[], gates=[]; let wall=1000;
 context.performance={now:()=>wall};
 // an offline question is a GET of its /q address (#20); a live one POSTs /api/ask. Each request waits on its gate.
 context.fetch=(path,init)=>new Promise(resolve=>{
  let body;if(init&&init.body)body=JSON.parse(init.body);else{const q=new URLSearchParams(path.split('?')[1]||'');body={get:path,question:q.get('q'),day:q.get('day'),shift:Number(q.get('shift')),corrupt:q.get('corrupt')==='1',after:q.getAll('after'),mode:'offline',scope:q.get('scope'),period:q.get('period')};}
  const n=bodies.push(body), url=body.get?`/corr/1/day/${body.day}/shift/${body.shift}?measure=m${n}`+body.after.map(x=>'&after='+x).join(''):undefined;   // the canonical url carries its priors unencoded, each with its own '?', as the server writes them
  gates.push({ok:()=>resolve(url?{ok:true,status:200,json:async()=>Object.assign(answer(n),{url})}:{ok:true,status:200,text:async()=>JSON.stringify({type:'start'})+'\n'+JSON.stringify({type:'done',result:answer(n)})+'\n'}),   // a live round is a stream (#67); no reader here, so its events arrive together
 fail:message=>resolve({ok:false,status:400,json:async()=>({error:message})})});});
 const tick=()=>new Promise(r=>setTimeout(r,0));
 vm.runInContext(`config={days:${JSON.stringify(storeDays)},first_day:'2026-09-11',token:'t',live_modes:[],demo_guards:{rate_limit_per_min:30}};acceptedDay='2026-09-14'`,context);   // a rate limit: no priming call
 elements.day.value='2026-09-14';elements.period.value='day';elements.scope.value='shift';elements.shift.value='2';elements.mode.value='offline';elements.corrupt.checked=false;
 vm.runInContext('renderQuestionChips();reset()',context);
 const card=elements.card, earlier=elements.earlier, canned=elements.examples.children;
 assert.equal(card.className,'card answer idle');assert.equal(elements['card-foot'].hidden,true);assert.equal(earlier.children.length,0);
 assert.equal(elements.analyst.hidden,true);                                   // no server key: Ask the analyst is not offered
 const disabled=()=>[...Object.entries(elements).filter(([,e])=>e.disabled).map(([id])=>id),...canned.filter(b=>b.disabled).map(text)];

 // 1. A canned question: the skeleton is in the DOM in the same task as the click, before any answer exists.
 canned[0].events.click();
 assert.equal(card.className,'card answer skeleton');assert.equal(card['aria-busy'],'true');
 assert.equal(elements['card-title'].textContent,'Why was this shift slow?');
 assert.equal(elements['card-meta'].textContent,'Shift 2 · Mon 14 Sep 2026 · offline · asking…');
 assert.ok(walk(elements.queries).some(e=>/^skel\b/.test(e.className))&&elements.charts.children.length===2&&elements.charts.children.every(e=>/skel-chart/.test(e.className)));
 assert.deepEqual(disabled(),[]);                                              // nothing is disabled while the question is out
 await tick();
 assert.equal(bodies[0].get,'/q?day=2026-09-14&shift=2&scope=shift&period=day&q=Why+was+this+shift+slow%3F+Chart+downtime+by+reason.');   // an offline question is a GET of its address (#20)
 wall=1093;gates[0].ok();await tick();
 assert.equal(card.className,'card answer');assert.equal(card['aria-busy'],'false');
 assert.equal(elements['card-meta'].textContent,'Shift 2 · Mon 14 Sep 2026 · offline · 2 queries · 93 ms');   // the measured wall time, submit to rendered
 assert.equal(elements['card-meta'].title,'93 ms from submit to the rendered card in this browser; 5 ms of it in the queries on the server. Planner: offline rule-based planner; no language model.');   // the server's own time is on the tooltip
 assert.equal(elements.queries.children.filter(e=>/^query\b/.test(e.className)).length,2);
 assert.ok(walk(elements.messages).some(e=>e.textContent==='Shift 2 answer 1.'));
 assert.equal(elements.charts.children.length,2);assert.equal(elements.metrics.children.length,7);assert.equal(elements['card-foot'].hidden,false);assert.equal(elements.pdf.hidden,false);
 assert.match(walk(elements.charts.children[0]).find(e=>/^bar\b/.test(e.class||'')).class,/minutes/);   // downtime minutes take the second bar colour

 // 2. Two more in a row, the third clicked while the second is still out: three cards, one open, two collapsed.
 canned[1].events.click();
 elements.mode.value='anthropic';canned[2].events.click();elements.mode.value='offline';
 await tick();assert.equal(bodies.length,2);                                   // the third waits for the second: one request at a time, so each carries the session the last one left
 assert.deepEqual(bodies[1].after,['/corr/1/day/2026-09-14/shift/2?measure=m1']);   // the thread continues the conversation: the prior view rides as after=
 assert.equal(card.className,'card answer skeleton');assert.equal(elements['card-title'].textContent,'Dry-end waste');
 assert.deepEqual(earlier.children.map(l=>l.children[0].children.map(text)),[['Speed to target','Shift 2 · asking…'],['Why was this shift slow?','Shift 2 · 93 ms']]);
 assert.deepEqual(disabled(),[]);
 wall=1200;gates[1].ok();await tick();
 assert.equal(bodies.length,3);assert.deepEqual(bodies[2].after,['/corr/1/day/2026-09-14/shift/2?measure=m1','/corr/1/day/2026-09-14/shift/2?measure=m2']);assert.deepEqual(bodies.map(b=>b.mode),['offline','offline','offline']);   // a canned question never calls a model, whatever the agent select says
 assert.equal(card.className,'card answer skeleton');assert.equal(earlier.children[0].children[0].children[1].textContent,'Shift 2 · 107 ms');   // the collapsed line fills in while the open card is still out
 wall=1300;gates[2].ok();await tick();
 assert.equal(elements['card-title'].textContent,'Dry-end waste');assert.equal(card.className,'card answer');
 assert.ok(walk(elements.messages).some(e=>e.textContent==='Shift 2 answer 3.'));   // each card keeps its own answer, whatever order they landed in
 assert.deepEqual(earlier.children.map(l=>l.children[0].children.map(text)),[['Speed to target','Shift 2 · 107 ms'],['Why was this shift slow?','Shift 2 · 93 ms']]);   // newest on top
 assert.ok(earlier.children.every(l=>l.children[1].tag==='button'&&l.children[1].textContent==='Open'&&/^card earlier/.test(l.className)));
 assert.deepEqual(disabled(),[]);

 // 3. Open an earlier answer: it becomes the open card, the other two are the lines; an addressed answer offers its PDF by address.
 earlier.children[1].children[1].events.click();
 assert.equal(elements['card-title'].textContent,'Why was this shift slow?');assert.ok(walk(elements.messages).some(e=>e.textContent==='Shift 2 answer 1.'));
 assert.equal(elements['card-meta'].textContent,'Shift 2 · Mon 14 Sep 2026 · offline · 2 queries · 93 ms');
 assert.deepEqual(earlier.children.map(l=>l.children[0].children[0].textContent),['Dry-end waste','Speed to target']);
 assert.equal(elements.pdf.hidden,false);
 earlier.children[1].children[1].events.click();assert.equal(elements['card-title'].textContent,'Speed to target');assert.equal(elements.pdf.hidden,false);
 earlier.children[0].children[1].events.click();assert.equal(elements['card-title'].textContent,'Dry-end waste');assert.equal(elements.pdf.hidden,false);

 // 3b. An answer that lands while an earlier card is open leaves that card open.
 canned[3].events.click();earlier.children[0].children[1].events.click();assert.equal(elements['card-title'].textContent,'Dry-end waste');
 await tick();gates[3].ok();await tick();assert.equal(elements['card-title'].textContent,'Dry-end waste');assert.equal(elements.pdf.hidden,false);
 elements.question.value='  ';elements.ask.events.submit({preventDefault(){}});assert.equal(bodies.length,4);assert.equal(elements.status.textContent,'Type a question first');   // an empty Ask says so and sends nothing

 // 4. A scope change keeps the thread and starts a new server conversation; the new card carries its own scope.
 elements['shift-seg'].children.find(b=>b.dataset.value==='1').events.click();
 assert.equal(earlier.children.length,3);assert.equal(elements['card-title'].textContent,'Dry-end waste');
 elements.question.value='How many setups?';elements.ask.events.submit({preventDefault(){}});
 await tick();assert.equal(bodies[4].get,'/q?day=2026-09-14&shift=1&scope=shift&period=day&q=How+many+setups%3F');   // a new scope: a new conversation, no after=
 assert.equal(elements['card-title'].textContent,'How many setups?');assert.equal(elements['card-meta'].textContent,'Shift 1 · Mon 14 Sep 2026 · offline · asking…');
 wall=1350;gates[4].fail('That question is not supported');await tick();
 assert.equal(card.className,'card answer failed');assert.match(elements['card-meta'].textContent,/ · failed · 50 ms$/);assert.equal(elements['card-foot'].hidden,true);
 assert.ok(walk(elements.messages).some(e=>e.textContent==='That question is not supported'));
 assert.equal(earlier.children.length,4);                                      // a failed question is a card too; the others are untouched

 // 5. Ask the analyst is the live mode, offered only when the server has a key for one; Ask stays offline.
 vm.runInContext("config.live_modes=['anthropic'];updateScope()",context);assert.equal(elements.analyst.hidden,false);
 elements.question.value='Why slow?';elements.analyst.events.click();
 assert.match(elements['card-meta'].textContent,/ · analyst · asking…$/);await tick();assert.equal(bodies[5].mode,'anthropic');
 elements.analyst.events.click();await tick();assert.equal(bodies.length,6);   // a double click on the analyst is the card already waiting, not a second paid round
 assert.equal(earlier.children.filter(l=>l.children[0].children[0].textContent==='Why slow?').length,0);
 elements.ask.events.submit({preventDefault(){}});await tick();assert.equal(bodies.length,6);
 gates[5].ok();await tick();assert.equal(bodies[6].mode,'offline');assert.ok(bodies[6].get);
 const analystCard=()=>earlier.children.find(l=>l.children[0].children[0].textContent==='Why slow?');
 analystCard().children[1].events.click();assert.equal(elements.pdf.hidden,false);   // a model's answer is not an address: its PDF is offered while it is the session's latest run
 gates[6].ok();await tick();assert.equal(elements.pdf.hidden,true);                   // the offline answer that landed after it took that place
 // a provider picked under "Agent and advanced" does not turn Ask into a model call; the key stays for the analyst
 elements.mode.value='anthropic';elements['api-key'].value='sk-visitor';elements.question.value='Why slow again?';elements.ask.events.submit({preventDefault(){}});await tick();
 assert.equal(bodies[7].mode,'offline');assert.match(elements['card-meta'].textContent,/ · offline · asking…$/);assert.equal(elements['api-key'].value,'sk-visitor');
 gates[7].ok();await tick();elements.mode.value='offline';elements['api-key'].value='';
 vm.runInContext("config.live_modes=[];updateScope()",context);assert.equal(elements.analyst.hidden,true);elements.mode.value='openai';vm.runInContext('updateScope()',context);assert.equal(elements.analyst.hidden,false);elements.mode.value='offline';vm.runInContext('updateScope()',context);   // locally, a provider chosen with your key is what offers the analyst
 // a question that names its own window: the title line says what the query ran over, in the tool's own words
 const september={period:'month',anchor:'2026-09-01',start:'2026-09-01',end:'2026-09-30',label:'September 2026 (2026-09-01 to 2026-09-30)',coverage:'complete'};
 const windowed=w=>({mode:'offline',trace:[{result_id:'w:1',tool:'get_downtime_pareto',ok:true,elapsed_ms:2,data:{window:w,shift:w===september?null:1,coverage:'complete',rows:[]}}]});
 const s1={day:'2026-09-14',period:'day',shift:1,scope:'shift'};
 assert.equal(vm.runInContext(`entryLabel({scope:${JSON.stringify(s1)},answered:answeredScope(${JSON.stringify(s1)},${JSON.stringify(windowed(september))})})`,context),'All shifts · September 2026 (2026-09-01 to 2026-09-30)');   // the rail says shift 1, 14 Sep; the query ran all shifts over the month
 assert.equal(vm.runInContext(`entryLabel({scope:${JSON.stringify(s1)},answered:answeredScope(${JSON.stringify(s1)},${JSON.stringify(windowed({period:'day',anchor:'2026-09-14',start:'2026-09-14',end:'2026-09-14',label:'2026-09-14 (Mon)'}))})})`,context),'Shift 1 · Mon 14 Sep 2026');   // the rail's own window keeps the rail's wording
 const s2={...s1,shift:2};
 assert.equal(vm.runInContext(`entryLabel({scope:${JSON.stringify(s2)},answered:answeredScope(${JSON.stringify(s2)},${JSON.stringify({trace:[{ok:true,data:{window:september,shift:2,rows:[]}}]})})})`,context),'Shift 2 · September 2026 (2026-09-01 to 2026-09-30)');   // the review's case: the shift kept, only the window is the tool's
 const sm={...s1,period:'month'};
 assert.equal(vm.runInContext(`answeredScope(${JSON.stringify(sm)},${JSON.stringify(windowed(september))}).window`,context),null);   // a month on the rail is the same month the tool ran

 // 6. "Clear the thread" empties it.
 elements.reset.events.click();assert.equal(earlier.children.length,0);assert.equal(card.className,'card answer idle');assert.equal(elements['card-foot'].hidden,true);
 delete context.performance;
 // The analyst card (.message.analyst) holds several paragraphs; a bare `.analyst{display:inline-flex}` rule (the
 // ask-bar button's) once laid them out side by side, one character per line. The button rule must stay scoped.
 assert.ok(!/(^|[\s}])\.analyst\s*\{[^}]*inline-flex/.test(css), 'the inline-flex rule must be scoped to button.analyst, not .analyst');
 assert.match(css, /button\.analyst\s*\{[^}]*inline-flex/);
 console.log('UI issue-63 checks passed: shell order, skeleton in the same task as the submit, measured ms on the title line, three cards from three questions with nothing disabled, payload unchanged, offline default.');
}).catch(error=>{console.error(error);process.exit(1);});

// ---- issue #20: the address bar is the state. Offline questions are GETs of /q (the server redirects to the canonical
// URL and answers it), the page pushes that URL, back/forward re-render a shown view without a request, a pasted
// address sets the controls and fetches its .json, and the example chips are links to their /q address.
issue63.then(async()=>{   // after #63: both drive the same stub elements
 for(const [id,value] of [['day','2026-09-14'],['period','day'],['shift','2'],['scope','shift'],['mode','offline']])elements[id].value=value;elements.corrupt.checked=false;
 const guard=setTimeout(()=>{console.error('UI issue-20 checks did not finish: a promise never settled');process.exit(1);},8000);
 await new Promise(r=>setTimeout(r,80));
 const history=[], fetched=[];
 const location={pathname:'/',search:''};   // the home address: init() replaces it with the scope's own
 const win={history:{pushState(state,_,url){history.push(['push',url]);const [p,q='']=url.split('?');location.pathname=p;location.search=q?'?'+q:'';},
                      replaceState(state,_,url){history.push(['replace',url]);const [p,q='']=url.split('?');location.pathname=p;location.search=q?'?'+q:'';}},
            location,listeners:{},addEventListener(name,fn){this.listeners[name]=fn;}};
 const answer=(url,extra={})=>({run_id:'r-'+url,production_day:'2026-09-14',selected_shifts:[2],mode:'offline',status:'complete',url,sections:['Shift 2: 600.0 ft/min.'],context:{history:[]},usage:{tool_calls:2,model_calls:0},trace:[],tables:[],charts:[],report_markdown:'# x',...extra});
 const fetch20=async(path,init)=>{fetched.push([path,init&&init.headers&&init.headers.Accept]);   // this page's own stub: the shared context's fetch belongs to the other blocks
  if(path==='/api/config')return {ok:true,status:200,json:async()=>({token:'t',machine:'1',days:['2026-09-11','2026-09-14','2026-09-15'],first_day:'2026-08-03',last_day:'2026-09-30',demo_guards:{max_turns:4,rate_limit_per_min:0}})};
  if(path.startsWith('/q?')){const q=new URLSearchParams(path.slice(3));const url=`/corr/1/day/${q.get('day')}/shift/${q.get('shift')}?measure=${q.get('q').includes('slow')?'overview':'fpm'}`+q.getAll('after').map(a=>'&after='+encodeURIComponent(a)).join('');return {ok:true,status:200,url,json:async()=>answer(url)};}
  if(path.startsWith('/corr/')){const page=path.replace('.json','');if(page.includes('measure=kpis'))return {ok:true,status:200,json:async()=>answer(page,{trace:[]})};return {ok:true,status:200,json:async()=>answer(page)};}
  return {ok:false,status:404,json:async()=>({error:'no'})};
 };
 // a fresh page with the window stubs: init() replaces the home address with the bare scope address and primes the tiles
 const ctx2={...context,fetch:fetch20,window:win,document:context.document,location,requestAnimationFrame:undefined};delete ctx2.requestAnimationFrame;
 vm.createContext(ctx2);vm.runInContext(fs.readFileSync('src/investigator/web/trail.js','utf8'),ctx2);vm.runInContext(fs.readFileSync('src/investigator/web/app.js','utf8'),ctx2);
 await new Promise(r=>setTimeout(r,80));
 assert.deepEqual(history,[['replace','/corr/1/day/2026-09-14/shift/2']]);
 assert.ok(fetched.some(f=>f[0]==='/corr/1/day/2026-09-14/shift/2.json?measure=kpis'&&f[1]==='application/json'),'the tiles are the kpis measure of the scope address: '+JSON.stringify(fetched));
 // chips are links to their /q address in the current scope, disabled ones say so
 const chip=elements.examples.children[0];
 assert.equal(chip.tag,'a');assert.equal(chip['role'],'button');
 assert.equal(chip.href,'/q?day=2026-09-14&shift=2&scope=shift&period=day&q=Why+was+this+shift+slow%3F+Chart+downtime+by+reason.');
 const compareChip=[...elements.examples.children,...elements['examples-more'].children].find(b=>b.dataset.question==='Compare all shifts');
 assert.equal(compareChip.href,'/q?day=2026-09-14&shift=2&scope=day&period=day&q=Compare+all+shifts');   // its own scope override
 // an offline question is a GET of /q with Accept: application/json; the answer's address is pushed and remembered
 fetched.length=0;history.length=0;
 const first=await vm.runInContext("ask('Why was this shift slow?')",ctx2);
 assert.equal(first.url,'/corr/1/day/2026-09-14/shift/2?measure=overview');
 assert.deepEqual(fetched[0],['/q?day=2026-09-14&shift=2&scope=shift&period=day&q=Why+was+this+shift+slow%3F','application/json']);
 assert.deepEqual(history,[['push','/corr/1/day/2026-09-14/shift/2?measure=overview']]);
 assert.equal(ctx2.location.search,'?measure=overview');
 assert.equal(elements.pdf.hidden,false);
 // the follow-up carries the prior view as after=, and the chips' links do too
 const second=await vm.runInContext("ask('Show speed to target')",ctx2);
 assert.equal(second.url,'/corr/1/day/2026-09-14/shift/2?measure=fpm&after=%2Fcorr%2F1%2Fday%2F2026-09-14%2Fshift%2F2%3Fmeasure%3Doverview');
 assert.ok(fetched.some(f=>f[0].startsWith('/q?')&&f[0].includes('q=Show+speed+to+target')&&f[0].includes('after=%2Fcorr%2F1%2Fday%2F2026-09-14%2Fshift%2F2%3Fmeasure%3Doverview')),JSON.stringify(fetched));
 assert.ok(chip.href.includes('after=%2Fcorr%2F1%2Fday%2F2026-09-14%2Fshift%2F2%3Fmeasure%3Doverview')&&chip.href.includes('after=%2Fcorr%2F1%2Fday%2F2026-09-14%2Fshift%2F2%3Fmeasure%3Dfpm'),chip.href);
 assert.equal(history.length,2);
 // the PDF of an addressed answer is the same address with format=pdf, no POST
 fetched.length=0;
 const links=[];context.document.createElement=tag=>{const e=new Element(tag);if(tag==='a')links.push(e);return e;};
 ctx2.document.createElement=context.document.createElement;
 await vm.runInContext("$('pdf').events.click()",ctx2);
 const pdfLink=links.find(l=>l.download&&l.download.endsWith('.pdf'));
 assert.ok(pdfLink&&pdfLink.href===second.url+'&format=pdf',pdfLink&&pdfLink.href);
 assert.equal(fetched.length,0);
 // Open on an earlier card of this scope puts its address in the bar, and the next question follows it (#63 merge)
 history.length=0;
 elements.earlier.children[0].children[1].events.click();
 assert.equal(elements['card-title'].textContent,'Why was this shift slow?');assert.deepEqual(history,[['push',first.url]]);
 assert.equal(vm.runInContext('JSON.stringify(conversation)',ctx2),JSON.stringify([first.url]));
 elements.earlier.children[0].children[1].events.click();assert.deepEqual(history.slice(-1),[['push',second.url]]);   // and back to the newest
 // back: the earlier view is restored from memory, the controls follow the address, no request is made
 fetched.length=0;
 location.pathname='/corr/1/day/2026-09-14/shift/2';location.search='?measure=overview';
 await win.listeners.popstate();await new Promise(r=>setTimeout(r,20));
 assert.equal(fetched.length,0,'a shown view is re-rendered without a request');
 assert.match(elements.status.textContent,/restored$/);
 assert.equal(elements['card-title'].textContent,'Why was this shift slow?');assert.equal(elements.card.className,'card answer');   // the card it was, opened again
 // forward to an address this page never showed: the controls follow it and its .json is fetched
 location.pathname='/corr/1/day/2026-09-15';location.search='?measure=fpm';
 await win.listeners.popstate();await new Promise(r=>setTimeout(r,20));
 assert.deepEqual(fetched.map(f=>f[0]),['/corr/1/day/2026-09-15?measure=fpm']);
 assert.equal(elements.day.value,'2026-09-15');assert.equal(elements.scope.value,'day');
 assert.equal(elements['card-title'].textContent,'/corr/1/day/2026-09-15?measure=fpm');assert.equal(elements.card.className,'card answer');   // a new card in the thread, through the same render entry
 assert.equal(vm.runInContext('JSON.stringify(conversation)',ctx2),JSON.stringify(['/corr/1/day/2026-09-15?measure=fpm']));   // the conversation is the new address's own, never the previous day's after= chain
 // an address that fails to load still leaves the conversation on its own priors (none here), so the next question is not refused
 const okFetch=ctx2.fetch; ctx2.fetch=async(path,init)=>{fetched.push([path]);if(path.startsWith('/corr/1/day/2026-09-11'))return {ok:false,status:429,json:async()=>({error:'too many questions, slow down'})};return okFetch(path,init);};
 location.pathname='/corr/1/day/2026-09-11';location.search='?measure=waste';
 await win.listeners.popstate();await new Promise(r=>setTimeout(r,20));
 assert.equal(elements.card.className,'card answer failed');assert.equal(vm.runInContext('JSON.stringify(conversation)',ctx2),JSON.stringify([]));
 ctx2.fetch=okFetch;
 // a bare scope address on popstate clears the view and primes; a control change pushes the new scope address
 fetched.length=0;history.length=0;
 location.pathname='/corr/1/week/2026-09-14/shift/1';location.search='';
 await win.listeners.popstate();await new Promise(r=>setTimeout(r,20));
 assert.equal(elements.period.value,'week');assert.equal(elements.shift.value,'1');assert.equal(history.length,0);
 assert.deepEqual(fetched.map(f=>f[0]),['/corr/1/week/2026-09-14/shift/1.json?measure=kpis']);
 vm.runInContext("selectShift('3')",ctx2);
 assert.deepEqual(history,[['push','/corr/1/week/2026-09-14/shift/3']]);
 // the period report is the report measure of the scope address
 fetched.length=0;
 await vm.runInContext("ask('Build week report',{report:true})",ctx2);
 assert.deepEqual(fetched[0],['/corr/1/week/2026-09-14/shift/3?measure=report','application/json']);
 // a fresh page opened on a pasted canonical address: the controls follow it and its .json is fetched on load (acceptance 1)
 fetched.length=0;history.length=0;
 const pasted={pathname:'/corr/1/month/2026-09-15/shift/3',search:'?measure=waste&corrupt=1'};
 const win3={history:win.history,location:pasted,listeners:{},addEventListener(name,fn){this.listeners[name]=fn;}};
 for(const [id,value] of [['day','2026-09-14'],['period','day'],['shift','2'],['scope','shift']])elements[id].value=value;elements.corrupt.checked=false;
 const ctx3={...context,fetch:fetch20,window:win3,location:pasted};delete ctx3.requestAnimationFrame;
 vm.createContext(ctx3);vm.runInContext(fs.readFileSync('src/investigator/web/trail.js','utf8'),ctx3);vm.runInContext(fs.readFileSync('src/investigator/web/app.js','utf8'),ctx3);
 await new Promise(r=>setTimeout(r,80));
 assert.deepEqual(fetched.map(f=>f[0]).filter(f=>f!=='/api/config'),['/corr/1/month/2026-09-15/shift/3?measure=waste&corrupt=1']);   // the address itself, as JSON; no priming, no push
 assert.equal(history.length,0);
 assert.deepEqual([elements.day.value,elements.period.value,elements.shift.value,elements.scope.value,elements.corrupt.checked],['2026-09-15','month','3','shift',true]);
 assert.equal(elements['card-title'].textContent,'/corr/1/month/2026-09-15/shift/3?measure=waste&corrupt=1');assert.equal(elements.card.className,'card answer');
 assert.equal(elements.pdf.hidden,false);
 // Codex review on #62: a modifier click on a chip is left to the browser (it opens the link); a plain click asks here
 fetched.length=0;
 const anyChip=elements.examples.children[1]; let prevented=false;
 anyChip.events.click({ctrlKey:true,preventDefault(){prevented=true;}});await new Promise(r=>setTimeout(r,20));
 assert.equal(prevented,false);assert.equal(fetched.length,0);
 anyChip.events.click({preventDefault(){prevented=true;}});await new Promise(r=>setTimeout(r,40));
 assert.equal(prevented,true);assert.ok(fetched.some(f=>f[0].startsWith('/q?')));
 // the retained priors never exceed the grammar's eight, whatever DEMO_MAX_TURNS says
 vm.runInContext("config.demo_guards.max_turns=50",ctx3);assert.equal(vm.runInContext('maxPriors()',ctx3),8);
 vm.runInContext("config.demo_guards.max_turns=4",ctx3);assert.equal(vm.runInContext('maxPriors()',ctx3),3);
 // a pasted alias is replaced in the address bar by the canonical url the server returns
 fetched.length=0;history.length=0;
 const alias={pathname:'/corr/1/week/2026-09-14',search:'?measure=downtime'};
 const win4={history:{pushState(state,_,url){history.push(['push',url]);},replaceState(state,_,url){history.push(['replace',url]);}},location:alias,listeners:{},addEventListener(name,fn){this.listeners[name]=fn;}};
 const ctx4={...context,window:win4,location:alias};delete ctx4.requestAnimationFrame;
 context.fetch=async(path,init)=>{fetched.push([path]);
  if(path==='/api/config')return {ok:true,status:200,json:async()=>({token:'t',machine:'1',days:['2026-09-14'],demo_guards:{max_turns:4,rate_limit_per_min:0}})};
  return {ok:true,status:200,json:async()=>answer('/corr/1/week/2026-09-14?measure=pareto')};};
 ctx4.fetch=context.fetch;
 vm.createContext(ctx4);vm.runInContext(fs.readFileSync('src/investigator/web/trail.js','utf8'),ctx4);vm.runInContext(fs.readFileSync('src/investigator/web/app.js','utf8'),ctx4);
 await new Promise(r=>setTimeout(r,80));
 assert.deepEqual(history,[['replace','/corr/1/week/2026-09-14?measure=pareto']]);
 assert.equal(elements['card-title'].textContent,'/corr/1/week/2026-09-14?measure=pareto');
 clearTimeout(guard);
 console.log('UI issue-20 checks passed: /q GET, pushState, back/forward from memory, pasted address, chips as links, PDF by address.');
}).catch(error=>{console.error(error);process.exit(1);});

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

// ---- issue #65: the reveal. The finished why-slow answer (tests/ui_fixtures/why_slow.json, the offline result of
// its golden URL; tests/test_ui_fixtures.py keeps it in step) lands, and the card reveals it in the order of the
// reasoning with a stubbed clock and frame scheduler: query lines tick in, the summary types word by word with its
// first figure at once, the charts bloom after the first sentence, a key ends it. The text it shows is the result's.
(async()=>{
 const whySlow=JSON.parse(fs.readFileSync('tests/ui_fixtures/why_slow.json','utf8'));
 const NOTE=/ - reported note \(synthetic, unverified\): /, visible=whySlow.sections.filter(s=>!NOTE.test(s)), notes=whySlow.sections.filter(s=>NOTE.test(s));
 const setup=({reduced=false}={})=>{
  const els=Object.fromEntries([...html.matchAll(/id="([^"]+)"/g)].map(m=>[m[1],new Element(m[1])]));
  Object.assign(els.day,{value:'2026-09-14'});els.period.value='day';els.shift.value='2';els.scope.value='shift';els.mode.value='offline';
  const frames=[], listeners={}; let wall=0;
  const doc={getElementById:id=>{assert.ok(els[id],`Unknown control ${id}`);return els[id];},createElement:tag=>new Element(tag),createElementNS:(_,tag)=>new Element(tag),querySelectorAll:()=>[],addEventListener:(name,fn)=>{listeners[name]=fn;}};
  let gate; const ctx={document:doc,fetch:()=>new Promise(resolve=>{gate=()=>resolve({ok:true,status:200,json:async()=>JSON.parse(JSON.stringify(whySlow))});}),window:{addEventListener(){}},console,setTimeout,URL,URLSearchParams,Blob,AbortController,
   performance:{now:()=>wall},requestAnimationFrame:f=>frames.push(f),matchMedia:q=>({matches:reduced&&q==='(prefers-reduced-motion: reduce)'})};
  vm.createContext(ctx);vm.runInContext(fs.readFileSync('src/investigator/web/trail.js','utf8'),ctx);vm.runInContext(fs.readFileSync('src/investigator/web/app.js','utf8'),ctx);
  vm.runInContext(`config={days:['2026-09-11','2026-09-14'],first_day:'2026-09-11',token:'t',live_modes:[],demo_guards:{rate_limit_per_min:30}};acceptedDay='2026-09-14'`,ctx);
  const at=async ms=>{ wall=ms; const due=frames.splice(0); for(const f of due)f(); await new Promise(r=>setTimeout(r,0)); };   // advance the clock and run one frame
  return {els,ctx,listeners,at,setWall:ms=>{wall=ms;},open:()=>gate()};
 };
 const tick=()=>new Promise(r=>setTimeout(r,0));
 const summaryOf=els=>els.messages.children.find(e=>/^message\b/.test(e.className));
 const typedText=els=>summaryOf(els).children.filter(e=>e.tag==='p').map(e=>e.textContent);
 const queryLines=els=>els.queries.children.filter(e=>/^query\b/.test(e.className));
 const waiting=e=>/\bpace-wait\b/.test(e.className);
 const ask=async t=>{ t.setWall(0); const done=vm.runInContext(`ask('Why was this shift slow? Chart downtime by reason.')`,t.ctx);
  assert.equal(t.els.card.className,'card answer skeleton');   // 1. the skeleton is in the DOM in the task of the submit: within the first frame
  assert.ok(walk(t.els.messages).some(e=>/^skel\b/.test(e.className)),'a pulsing line where the first sentence goes');
  await tick(); t.setWall(40); t.open(); await done; };   // the offline answer is complete 40 ms after the submit

 // Paced: at 40 ms the answer is in the DOM, the title line carries the real 40 ms, and the reveal starts.
 let t=setup(); await ask(t);
 assert.equal(t.els['card-meta'].textContent,'Shift 2 · Mon 14 Sep 2026 · offline · 6 queries · 40 ms');   // the time to the complete result, not to the end of the animation
 assert.equal(t.els.card['data-reveal'],'running');
 assert.deepEqual(queryLines(t.els).map(waiting),[false,true,true,true,true,true]);   // 2. query lines tick in one at a time
 assert.ok(t.els.metrics.children.length===7&&!t.els.metrics.children.some(waiting));   // the tiles land at once, like a gauge
 assert.equal(t.els.tables.children.length,1);assert.ok(!waiting(t.els.tables.children[0]));   // tables appear at once
 assert.ok(t.els.charts.children.length===2&&t.els.charts.children.every(waiting));   // 4. the charts wait for the first sentence
 assert.deepEqual(typedText(t.els),['','','']);
 await t.at(40+120);assert.deepEqual(queryLines(t.els).map(waiting),[false,false,true,true,true,true]);   // 80 ms apart
 // 3. the first figure of the first sentence is on screen within 400 ms of the submit (the bar is the 400 ms of the issue)
 await t.at(40+170);
 const firstFigure=/\d[\d,.]*/.exec(visible[0])[0];
 assert.ok(t.els.messages&&typedText(t.els)[0].includes(firstFigure)&&visible[0].startsWith(typedText(t.els)[0]),`the first figure "${firstFigure}" by 400 ms: "${typedText(t.els)[0]}"`);
 const measured=/\d[\d,]*\.\d+/.exec(visible[0])[0];assert.ok(typedText(t.els)[0].includes(measured),`the first measured figure "${measured}" too, not only the shift number`);
 assert.ok(typedText(t.els)[0].length<visible[0].length,'and the rest is still typing');
 // word-chunked: what is on screen always ends at a word boundary of the paragraph, never mid-word
 for(const ms of [300,450,600,800]){ await t.at(ms); typedText(t.els).forEach((s,i)=>{ assert.ok(visible[i].startsWith(s)); if(s&&s.length<visible[i].length)assert.match(visible[i][s.length],/\s/); }); }
 assert.ok(!t.els.charts.children.some(waiting)&&t.els.charts.children.every(e=>/\bbloom\b/.test(e.className)),'the charts bloomed once the first sentence was down');
 assert.equal(summaryOf(t.els).children.find(e=>e.tag==='details').className,'note-group pace-wait');   // the folded notes arrive with the last word
 // 5. the full summary is there by 1.5 s; the typed text equals the result's sections verbatim
 await t.at(1500);
 assert.equal(t.els.card['data-reveal'],'done');
 assert.deepEqual(typedText(t.els),visible);
 assert.deepEqual(summaryOf(t.els).children.find(e=>e.tag==='details').children.slice(1).map(e=>e.textContent),notes);
 assert.deepEqual(walk(summaryOf(t.els)).filter(e=>e.tag==='p').map(e=>e.textContent),whySlow.sections);   // every section, in order, nothing added or paraphrased
 assert.ok(![...queryLines(t.els),...t.els.charts.children,...walk(t.els.messages)].some(waiting));
 assert.ok(!walk(t.els.messages).some(e=>/\btyping\b/.test(e.className)));
 assert.equal(t.els['card-meta'].textContent,'Shift 2 · Mon 14 Sep 2026 · offline · 6 queries · 40 ms');   // the animation never moved the measured time

 // A keypress completes it immediately (and a click: the same handler on pointerdown).
 t=setup(); await ask(t); await t.at(40+100);
 assert.ok(typedText(t.els).join('').length<visible.join('').length);
 t.listeners.keydown({key:'x'});
 assert.deepEqual(typedText(t.els),visible);assert.equal(t.els.card['data-reveal'],'done');
 assert.ok(![...queryLines(t.els),...t.els.charts.children].some(waiting));
 assert.ok(!t.els.charts.children.some(e=>/\bbloom\b/.test(e.className)),'a skip shows the charts drawn: no bloom left to run');
 assert.equal(typeof t.listeners.pointerdown,'function');
 // Reopening a card shows it settled; it is revealed once.
 vm.runInContext('renderCard()',t.ctx);assert.deepEqual(typedText(t.els),visible);assert.notEqual(t.els.card['data-reveal'],'running');

 // Races: a pointerdown skips; a new question during a reveal settles the first card; it reopens settled, verbatim.
 t=setup(); await ask(t); await t.at(40+100);
 t.listeners.pointerdown({});assert.deepEqual(typedText(t.els),visible);assert.equal(t.els.card['data-reveal'],'done');
 t=setup(); await ask(t); await t.at(40+100);
 const firstCard=vm.runInContext('openId',t.ctx);
 t.setWall(200); const second=vm.runInContext("ask('Why was this shift slow?')",t.ctx); await tick();   // a second question while the first is still typing
 t.open(); await second;
 vm.runInContext(`openCard(thread.find(r=>r.id===${firstCard}))`,t.ctx);
 assert.deepEqual(typedText(t.els),visible);assert.notEqual(t.els.card['data-reveal'],'running');
 assert.ok(![...queryLines(t.els),...t.els.charts.children,...walk(t.els.messages)].some(waiting));
 // a card that landed while another was open opens settled too
 t=setup(); vm.runInContext("ask('Why was this shift slow? Chart downtime by reason.')",t.ctx); await tick();
 const background=vm.runInContext('openId',t.ctx); vm.runInContext("openId=null;renderCard()",t.ctx);
 t.setWall(40); t.open(); await vm.runInContext(`thread.find(r=>r.id===${background}).done`,t.ctx);
 vm.runInContext(`openCard(thread.find(r=>r.id===${background}))`,t.ctx);
 assert.deepEqual(typedText(t.els),visible);assert.notEqual(t.els.card['data-reveal'],'running');

 // prefers-reduced-motion: everything is present at the first frame.
 t=setup({reduced:true}); await ask(t);
 assert.deepEqual(typedText(t.els),visible);assert.equal(t.els.card['data-reveal'],undefined);
 assert.ok(![...queryLines(t.els),...t.els.charts.children,...walk(t.els.messages)].some(waiting));
 assert.equal(t.els.charts.children.length,2);
 console.log('UI issue-65 checks passed: skeleton in the submit task, query lines tick in, first figure by 400 ms, word-chunked typing, charts bloom after the first sentence, full summary by 1.5 s and verbatim, a key skips, reduced motion shows all at once, measured ms unchanged.');
})().catch(error=>{console.error(error);process.exit(1);});
