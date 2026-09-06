'use strict';
const $ = id => document.getElementById(id);
let config, sessionId = null, latest = null, busy = false;
function node(tag, text, cls) { const e=document.createElement(tag); if(text!==undefined)e.textContent=text; if(cls)e.className=cls; return e; }
function message(label, paragraphs, cls='') { const e=node('div',undefined,'message '+cls);e.append(node('strong',label));for(const p of paragraphs)e.append(node('p',p));$('messages').append(e);$('messages').scrollTop=$('messages').scrollHeight; }
function svgNode(tag, attrs, text) { const e=document.createElementNS('http://www.w3.org/2000/svg',tag);for(const [k,v] of Object.entries(attrs))e.setAttribute(k,String(v));if(text!==undefined)e.textContent=text;return e; }
function showChart(chart) {
 const wrap=node('div',undefined,'chart');wrap.append(node('h3',`Shift ${chart.shift} · ${chart.unit === 'minutes' ? 'Recorded downtime' : chart.unit === 'ft/min' ? 'Wet-end speed (bars) / grade target (marks)' : 'Dry-end rejects'}`));
 if(chart.coverage!=='complete')wrap.append(node('p','Incomplete data: observed values only.','warning-note'));
 const rows=chart.rows; const max=Math.max(1,...rows.map(r=>Math.max(Number(r[chart.y])||0,Number(r.target_fpm)||0)));
 const height=rows.length*32+25; const svg=svgNode('svg',{viewBox:`0 0 600 ${height}`,role:'img','aria-label':`Shift ${chart.shift} ${chart.unit} chart`});
 rows.forEach((r,i)=>{const y=i*32+8;svg.append(svgNode('text',{x:0,y:y+16},String(r[chart.x])));svg.append(svgNode('rect',{x:140,y,width:Math.max(0,Number(r[chart.y]))/max*360,height:21,class:chart.coverage==='complete'?'bar':'incomplete'}));if(r.target_fpm){const x=140+r.target_fpm/max*360;svg.append(svgNode('line',{x1:x,x2:x,y1:y-2,y2:y+23,class:'target'}));}svg.append(svgNode('text',{x:515,y:y+16},Number(r[chart.y]).toFixed(1)));});
 if(!rows.length)wrap.append(node('p','No records match this filter.','muted'));else wrap.append(svg);$('charts').append(wrap);
}
function showResult(result) {
 latest=result; $('download').disabled=false; $('charts').replaceChildren(); $('metrics').replaceChildren(); $('trace').replaceChildren();
 for(const entry of result.trace) {
  if(entry.ok && entry.tool==='get_shift_kpis'){
   const r=entry.data.rows[0], incomplete=entry.data.coverage!=='complete';
   for(const [label,value,unit,bad] of [['Speed',r.observed_shift_fpm,'ft/min',false],['Maintenance',r.maintenance_pct,'%',r.maintenance_pct>2.5],['Operator',r.operator_pct,'%',r.operator_pct>2.5],['Dry-end waste',r.dry_end_pct,'%',r.dry_end_pct>1],['Trim',r.trim_pct,'%',false],['Shear',r.shear_pct,'%',false],['Throughput',r.throughput_in,'in',false],['Lineal / setup',r.lineal_per_setup,'ft',false]]){
    const card=node('div',undefined,'metric '+(bad||incomplete?'warning':''));card.append(node('div',`Shift ${entry.data.shift} · ${label}`,'label'));card.append(node('div',incomplete?'Incomplete':`${Number(value).toFixed(1)} ${unit}`,'value'));if(incomplete)card.append(node('small',`${entry.data.excluded_records.length} excluded record(s)`));$('metrics').append(card);
   }
  }
  const d=node('details',undefined,'trace-step'); d.append(node('summary',`${entry.tool} · ${entry.ok?'OK':'error'} · ${entry.elapsed_ms.toFixed(1)} ms`));
  d.append(node('pre',JSON.stringify(entry,null,2)));$('trace').append(d);
 }
 for(const chart of result.charts)showChart(chart);
 $('calls').textContent=` · ${result.usage.tool_calls} calls · $0 model cost`;
 $('context').replaceChildren(node('h3','Context carried to the next question'),node('pre',JSON.stringify(result.context,null,2)));
 $('shift').value=result.context.shift;
}
async function ask(question) {
 if(busy)throw new Error('An investigation is already running');
 if(typeof question!=='string'||!question.trim())throw new Error('Enter a question');
 busy=true; ['reset','day','shift','corrupt'].forEach(id=>$(id).disabled=true); $('submit').disabled=true; document.querySelectorAll('[data-question]').forEach(b=>b.disabled=true);$('status').textContent='Querying evidence…';
 const empty=$('messages').querySelector('.empty');if(empty)empty.remove();message('You',[question],'user');
 try{
  const response=await fetch('/api/ask',{method:'POST',headers:{'Content-Type':'application/json','X-Workbench-Token':config.token},body:JSON.stringify({question,day:$('day').value,shift:Number($('shift').value),corrupt:$('corrupt').checked,session_id:sessionId})});
  const result=await response.json();if(!response.ok)throw new Error(result.error||'Request failed');sessionId=result.session_id;
  message('Investigator · offline',result.sections);showResult(result);$('status').textContent=`${result.status} · ${result.usage.tool_calls} tool calls`;return {status:result.status,sections:result.sections,production_day:result.production_day,shifts:result.selected_shifts};
 }catch(error){message('Could not complete',[error.message],'error');$('status').textContent='Ready to retry';throw error;}
 finally{busy=false;['reset','day','shift','corrupt'].forEach(id=>$(id).disabled=false);$('submit').disabled=false;document.querySelectorAll('[data-question]').forEach(b=>b.disabled=false);}
}
$('ask').addEventListener('submit',event=>{event.preventDefault();ask($('question').value).catch(()=>{});});
document.querySelectorAll('[data-question]').forEach(button=>button.addEventListener('click',()=>{ $('question').value=button.dataset.question;ask(button.dataset.question).catch(()=>{}); }));
function reset(){sessionId=null;latest=null;$('messages').replaceChildren(node('p','Conversation reset. Choose a question.','empty'));$('metrics').replaceChildren();$('charts').replaceChildren();$('trace').replaceChildren();$('context').replaceChildren();$('calls').textContent='';$('download').disabled=true;$('status').textContent='Ready · no API costs';}
$('reset').addEventListener('click',reset);$('day').addEventListener('change',reset);$('corrupt').addEventListener('change',reset);
$('download').addEventListener('click',()=>{if(!latest)return;const url=URL.createObjectURL(new Blob([latest.report_markdown],{type:'text/markdown'}));const a=node('a');a.href=url;a.download=`synthetic-shift-report-${latest.production_day}.md`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);});
async function init(){
 $('submit').disabled=true;
 try{const response=await fetch('/api/config');if(!response.ok)throw new Error('Workbench unavailable');config=await response.json();for(const day of config.days){const option=node('option',day);option.value=day;$('day').append(option);} $('submit').disabled=false;
 const context=document.modelContext;
 if(context?.registerTool){const lifecycle=new AbortController();window.addEventListener('pagehide',()=>lifecycle.abort(),{once:true});await context.registerTool({name:'investigate_selected_shift',description:'Run an offline investigation for the selected day and update the visible answer, charts and trace.',inputSchema:{type:'object',properties:{question:{type:'string',maxLength:2000}},required:['question'],additionalProperties:false},annotations:{readOnlyHint:false,untrustedContentHint:true},execute:async input=>{if(!input||typeof input.question!=='string'||Object.keys(input).some(k=>k!=='question')||input.question.length>2000)throw new Error('Invalid question');$('question').value=input.question;return ask(input.question);}},{signal:lifecycle.signal});}
 }catch(error){$('status').textContent=error.message;}
}
init();
