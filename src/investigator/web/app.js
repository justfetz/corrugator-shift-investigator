'use strict';
const $ = id => document.getElementById(id);
let config, sessionId = null, latest = null, busy = false;
function node(tag, text, cls) { const e=document.createElement(tag); if(text!==undefined)e.textContent=text; if(cls)e.className=cls; return e; }
function message(label, paragraphs, cls='') { const e=node('div',undefined,'message '+cls);e.append(node('strong',label));for(const p of paragraphs)e.append(node('p',p));$('messages').append(e);$('messages').scrollTop=$('messages').scrollHeight; }
function svgNode(tag, attrs, text) { const e=document.createElementNS('http://www.w3.org/2000/svg',tag);for(const [k,v] of Object.entries(attrs))e.setAttribute(k,String(v));if(text!==undefined)e.textContent=text;return e; }
function showChart(chart) {
 const wrap=node('div',undefined,'chart');wrap.append(node('h3',chart.title || `Shift ${chart.shift} · ${chart.unit === 'minutes' ? 'Recorded downtime' : chart.unit === 'ft/min' ? 'Wet-end speed (bars) / grade target (marks)' : 'Dry-end rejects'}`));
 if(chart.coverage!=='complete')wrap.append(node('p','Incomplete data: observed values only.','warning-note'));
 if(chart.unit==='ft/min')wrap.append(node('p','Value = ft/min / % of target. Hover or focus a value for grade and target details. 100% meets target; elapsed time includes downtime.','muted'));
 const rows=chart.rows; const max=Math.max(1,...rows.map(r=>Math.max(Number(r[chart.y])||0,Number(r.target_fpm)||0)));
 const height=rows.length*32+25; const svg=svgNode('svg',{viewBox:`0 0 690 ${height}`,role:'img','aria-label':`Shift ${chart.shift} ${chart.unit} chart`});
 rows.forEach((r,i)=>{const y=i*32+8;svg.append(svgNode('text',{x:0,y:y+16},String(r[chart.x])));svg.append(svgNode('rect',{x:140,y,width:Math.max(0,Number(r[chart.y]))/max*360,height:21,class:chart.coverage==='complete'?'bar':'incomplete'}));if(r.target_fpm){const x=140+r.target_fpm/max*360;svg.append(svgNode('line',{x1:x,x2:x,y1:y-2,y2:y+23,class:'target'}));}const detail=r.target_fpm?`${r[chart.x]} / ${r.grade||'grade-weighted'}: ${Number(r[chart.y]).toFixed(1)} ft/min; target ${r.target_fpm.toFixed(1)}; ${r.speed_to_target_pct.toFixed(1)}% of target. Includes downtime.`:`${r[chart.x]}: ${Number(r[chart.y]).toFixed(1)} ${chart.unit}`;const value=svgNode('text',{x:515,y:y+16,tabindex:0,'aria-label':detail},r.target_fpm?`${Number(r[chart.y]).toFixed(1)} / ${r.speed_to_target_pct.toFixed(1)}%`:Number(r[chart.y]).toFixed(1));value.append(svgNode('title',{},detail));svg.append(value);});
 if(!rows.length)wrap.append(node('p','No records match this filter.','muted'));else wrap.append(svg);$('charts').append(wrap);
}
function updateScope(){
 const ranged=$('period').value!=='day', live=$('mode').value==='openai';
 $('scope-help').textContent=`${$('day').value} / ${ranged?$('period').selectedOptions[0].textContent:'07:00 to next day 07:00'} / ${$('scope').value==='shift'?'Shift '+$('shift').value+' only. Other shifts are blocked.':'All three shifts available for comparison.'}`;
 $('mode').disabled=busy||ranged;$('question').disabled=ranged;$('key-label').hidden=!live;
 $('mode-badge').textContent=live?'Synthetic data / OpenAI selected':'Synthetic data / Offline planner';
 if(ranged)$('mode-help').textContent='Build a free deterministic period summary, recurring downtime comparison, daily matrix and notes. Weeks end on the selected production date. Missing comparison days are disclosed.';
 else $('mode-help').textContent=live?'OpenAI receives your question, recent context and synthetic evidence. Live calls incur charges. Your key is used for this request only.':'Ask about performance, setups, rankings or notes within the selected scope. No model calls or API charges.';
 $('submit').textContent=ranged?'Build period report':'Investigate';
 document.querySelectorAll('[data-question]').forEach(b=>b.disabled=busy||ranged);
}
const labels={setup_id:'Setup',wet_end_id:'Wet-end / paper change',grade:'Grade',start:'Start',end:'End',width_in:'Width (in)',lineal_ft:'Lineal (ft)',actual_fpm:'Speed (ft/min)',target_fpm:'Target (ft/min)',speed_to_target_pct:'% of target',stop_count:'Stops',down_minutes:'Down (min)',rejected_sqft:'Rejects (sq ft)',notes:'Reported note',record_id:'Record',record_type:'Type',rank:'Rank',item:'Item',value:'Value',unit:'Units / order',day:'Production day',shift:'Shift',current_count:'Stops now',previous_count:'Stops before',current_minutes:'Minutes now',previous_minutes:'Minutes before',minutes_change:'Change (min)',speed_rank:'Speed rank',maintenance_rank:'Maint. rank',waste_rank:'Waste rank',valid_setups:'Dry-end setups',paper_changes:'Paper changes',observed_shift_fpm:'Speed (ft/min)',maintenance_pct:'Maintenance %',operator_pct:'Operator %',dry_end_pct:'Dry-end %'};
function showTable(data){
 const titles={get_setup_matrix:'Setup matrix',get_shift_notes:'Shift and downtime notes (synthetic, unverified)',get_rankings:`${data.metric||''} ranking / largest losses first`};
 const box=node('details');box.open=data.title==='get_setup_matrix'||data.title==='get_rankings';box.append(node('summary',`${data.shift?'Shift '+data.shift+' / ':''}${titles[data.title]||data.title} (${data.rows.length} rows)`));
 if(data.coverage!=='complete')box.append(node('p','Incomplete evidence. Rankings are withheld; any other rows are observed records only.','warning-note'));
 if(!data.rows.length){box.append(node('p','No eligible rows.'));$('tables').append(box);return;}
 const omitted=new Set(['source_ids','gross_sqft','observed_lineal_ft','throughput_in','lineal_per_setup','lineal_per_wet_end','setups_above_trim_target']);
 const columns=Object.keys(data.rows[0]).filter(k=>!omitted.has(k)&&!Array.isArray(data.rows[0][k]));
 const wrap=node('div',undefined,'table-scroll');wrap.tabIndex=0;wrap.setAttribute('role','region');wrap.setAttribute('aria-label',titles[data.title]||data.title);const table=node('table'),head=node('thead'),tr=node('tr');
 for(const key of columns){const th=node('th',labels[key]||key.replaceAll('_',' '));th.scope='col';tr.append(th);}head.append(tr);table.append(head);const body=node('tbody');
 for(const row of data.rows){const tr=node('tr');for(const key of columns){const value=row[key];tr.append(node('td',value===null?'Unavailable':typeof value==='number'?Number.isInteger(value)?String(value):value.toFixed(2):String(value??'')));}body.append(tr);}table.append(body);wrap.append(table);box.append(wrap);$('tables').append(box);
}
function showResult(result) {
 latest=result; $('download').disabled=false; $('pdf').disabled=false; $('tables').replaceChildren(); $('charts').replaceChildren(); $('metrics').replaceChildren(); $('trace').replaceChildren();
 for(const entry of result.trace) {
  if(entry.ok && (entry.tool==='get_shift_kpis'||entry.tool==='get_period_report')){
   const r=entry.data.rows[0], incomplete=entry.data.coverage!=='complete';
   for(const [label,value,unit,bad] of [['Speed',r.observed_shift_fpm,'ft/min',false],['Speed / target',r.speed_to_target_pct,'%',r.speed_to_target_pct<100],['Dry-end setups',r.valid_setups,'',false],['Paper changes',r.paper_changes,'',false],['Maintenance',r.maintenance_pct,'%',r.maintenance_pct>2.5],['Operator',r.operator_pct,'%',r.operator_pct>2.5],['Dry-end waste',r.dry_end_pct,'%',r.dry_end_pct>1],['Trim',r.trim_pct,'%',false],['Shear',r.shear_pct,'%',false],['Throughput',r.throughput_in,'in',false],['Lineal / setup',r.lineal_per_setup,'ft',false]]){
    const card=node('div',undefined,'metric '+(bad||incomplete?'warning':''));card.append(node('div',`${entry.data.shift?'Shift '+entry.data.shift:result.period_label} · ${label}`,'label'));if(label==='Speed / target')card.title='Lineal feet / sum(grade target ft/min × elapsed minutes) × 100. Includes downtime; weighted by grade duration.';if(label==='Paper changes')card.title='Count of distinct wet-end runs, as defined by the plant expert.';card.append(node('div',incomplete?'Incomplete':`${Number(value).toFixed(1)} ${unit}`,'value'));if(incomplete)card.append(node('small',`${entry.data.excluded_records?.length||'Missing/invalid'} excluded record(s)`));$('metrics').append(card);
   }
  }
  const d=node('details',undefined,'trace-step'); d.append(node('summary',`${entry.tool} · ${entry.ok?'OK':'error'} · ${entry.elapsed_ms.toFixed(1)} ms`));
  d.append(node('pre',JSON.stringify(entry,null,2)));$('trace').append(d);
 }
 for(const table of result.tables||[])showTable(table);
 for(const chart of result.charts)showChart(chart);
 $('calls').textContent=result.usage.model_calls ? ` · ${result.usage.tool_calls} tool calls · estimated $${result.usage.cost_usd.toFixed(4)} · reserved $${result.usage.reserved_usd.toFixed(2)}` : ` · ${result.usage.tool_calls} tool calls · $0 model cost`;
 $('context').replaceChildren(node('h3','Context carried to the next question'),node('pre',JSON.stringify(result.context,null,2)));
 updateScope();
}
async function ask(question) {
 if(busy)throw new Error('An investigation is already running');
 if(typeof question!=='string'||!question.trim())throw new Error('Enter a question');
 busy=true; ['reset','day','period','scope','shift','corrupt','mode','api-key'].forEach(id=>$(id).disabled=true); $('submit').disabled=true; document.querySelectorAll('[data-question]').forEach(b=>b.disabled=true);$('status').textContent=$('period').value==='day'?'Querying evidence…':'Preparing validated daily records; the first period report can take a minute…';
 const empty=$('messages').querySelector('.empty');if(empty)empty.remove();message('You',[question],'user');
 try{
  const headers={'Content-Type':'application/json','X-Workbench-Token':config.token};
  if($('mode').value==='openai' && $('api-key').value)headers['X-OpenAI-Key']=$('api-key').value;
  $('api-key').value='';
  const pending=fetch('/api/ask',{method:'POST',headers,body:JSON.stringify({question,day:$('day').value,shift:Number($('shift').value),corrupt:$('corrupt').checked,session_id:sessionId,mode:$('mode').value,scope:$('scope').value,period:$('period').value})});
  delete headers['X-OpenAI-Key'];
  const response=await pending;
  const result=await response.json();if(!response.ok)throw new Error(result.error||'Request failed');sessionId=result.session_id;
  message(`Investigator · ${result.mode}`,result.sections);showResult(result);$('status').textContent=`${result.status} · ${result.usage.tool_calls} tool calls`;return {status:result.status,sections:result.sections,production_day:result.production_day,shifts:result.selected_shifts};
 }catch(error){message('Could not complete',[error.message],'error');$('status').textContent='Ready to retry';throw error;}
 finally{busy=false;['reset','day','period','scope','shift','corrupt','mode','api-key'].forEach(id=>$(id).disabled=false);$('submit').disabled=false;document.querySelectorAll('[data-question]').forEach(b=>b.disabled=false);updateScope();}
}
$('ask').addEventListener('submit',event=>{event.preventDefault();ask($('period').value==='day'?$('question').value:`Build ${$('period').value} report`).catch(()=>{});});
document.querySelectorAll('[data-question]').forEach(button=>button.addEventListener('click',()=>{ if(button.dataset.scope){$('scope').value=button.dataset.scope;reset();} $('question').value=button.dataset.question;ask(button.dataset.question).catch(()=>{}); }));
function reset(){$('api-key').value='';sessionId=null;latest=null;$('messages').replaceChildren(node('p','Conversation reset. Choose a question.','empty'));$('tables').replaceChildren();$('metrics').replaceChildren();$('charts').replaceChildren();$('trace').replaceChildren();$('context').replaceChildren();$('calls').textContent='';$('download').disabled=true;$('pdf').disabled=true;$('status').textContent=$('mode').value==='openai'?'Ready · OpenAI calls incur charges':'Ready · no API costs';updateScope();}
$('mode').addEventListener('change',()=>{reset();const live=$('mode').value==='openai';$('key-label').hidden=!live;$('api-key').value='';$('mode-badge').textContent=live?'Synthetic data / OpenAI selected':'Synthetic data / Offline planner';$('mode-help').textContent=live?'OpenAI receives your question, bounded history and synthetic tool results. Your key passes through this local server to OpenAI for this request, is not saved, and the field clears on submit. Live calls incur API charges.':'Offline mode uses a rule-based planner for the supported example questions. No model calls or API charges.';});
$('reset').addEventListener('click',reset);$('day').addEventListener('change',reset);$('corrupt').addEventListener('change',reset);$('shift').addEventListener('change',reset);$('scope').addEventListener('change',reset);$('period').addEventListener('change',()=>{if($('period').value!=='day')$('mode').value='offline';reset();});
$('download').addEventListener('click',()=>{if(!latest)return;const url=URL.createObjectURL(new Blob([latest.report_markdown],{type:'text/markdown'}));const a=node('a');a.href=url;a.download=`synthetic-shift-report-${latest.production_day}.md`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);});
$('pdf').addEventListener('click',async()=>{if(!latest||busy)return;$('pdf').disabled=true;try{const response=await fetch('/api/report',{method:'POST',headers:{'Content-Type':'application/json','X-Workbench-Token':config.token},body:JSON.stringify({session_id:latest.session_id,run_id:latest.run_id})});if(!response.ok){const error=await response.json();throw new Error(error.error||'Report unavailable');}const url=URL.createObjectURL(await response.blob());const a=node('a');a.href=url;a.download=`synthetic-report-${latest.production_day}.pdf`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}catch(error){$('status').textContent=error.message;}finally{$('pdf').disabled=!latest;}});
async function init(){
 $('submit').disabled=true;
 try{const response=await fetch('/api/config');if(!response.ok)throw new Error('Workbench unavailable');config=await response.json();$('day').min=config.days[0];$('day').max=config.days[config.days.length-1];updateScope(); $('submit').disabled=false;
 const context=document.modelContext;
 if(context?.registerTool){const lifecycle=new AbortController();window.addEventListener('pagehide',()=>lifecycle.abort(),{once:true});await context.registerTool({name:'investigate_selected_shift',description:'Run an offline investigation for the selected day and update the visible answer, charts and trace.',inputSchema:{type:'object',properties:{question:{type:'string',maxLength:2000}},required:['question'],additionalProperties:false},annotations:{readOnlyHint:false,untrustedContentHint:true},execute:async input=>{if($('mode').value!=='offline'||$('period').value!=='day')throw new Error('Browser question tool is available only for offline daily investigations');if(!input||typeof input.question!=='string'||Object.keys(input).some(k=>k!=='question')||input.question.length>2000)throw new Error('Invalid question');$('question').value=input.question;return ask(input.question);}},{signal:lifecycle.signal});}
 }catch(error){$('status').textContent=error.message;}
}
init();
