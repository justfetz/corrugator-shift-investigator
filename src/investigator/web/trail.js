'use strict';
// Shared by the investigator (app.js) and the golden-evals page (evals.js): the DOM helper and the
// tool-trail row, so a case run on /evals is drawn with the same card a question gets on the main page.
function node(tag, text, cls) { const e=document.createElement(tag); if(text!==undefined)e.textContent=text; if(cls)e.className=cls; return e; }
// ---- The tool trail: one row per tool call. Arguments, row count, coverage and timing on the row;
// the exact SQL with its bound values and the full result JSON behind disclosures.
const compactJson=value=>JSON.stringify(value,(k,v)=>v===null?null:v)||'';
function traceRow(entry){
 const d=node('details',undefined,'trace-step '+(entry.ok?'ok':'failed')), data=entry.ok?entry.data:null;
 const rows=data?Array.isArray(data.rows)?data.rows.length:null:null;
 const sum=node('summary',undefined,'trace-sum');
 sum.append(node('code',entry.tool,'tool'));
 sum.append(node('span',compactJson(entry.arguments||{}),'args mono'));
 const facts=[];
 if(rows!==null)facts.push(`${rows} row${rows===1?'':'s'}`);
 if(data&&data.coverage)facts.push(data.coverage);
 if(typeof entry.elapsed_ms==='number')facts.push(`${entry.elapsed_ms.toFixed(1)} ms`);
 facts.push(entry.ok?'OK':'error');
 sum.append(node('span',facts.join(' · '),'facts mono'));
 d.append(sum);
 if(!entry.ok){d.append(node('p',`${entry.error?.code||'error'}: ${entry.error?.message||'tool call rejected'}`,'warning-note'));}
 else{
  const meta=node('dl',undefined,'trace-meta');const add=(k,v)=>{if(v===undefined||v===null||v==='')return;meta.append(node('dt',k),node('dd',String(v)));};
  if(data.window)add('Window',`${data.window.label} (${data.window.coverage})`+(data.previous_window?`; previous ${data.previous_window.label} (${data.previous_window.coverage})`:''));
  if(data.shift!==undefined)add('Shift',data.shift===null?'all shifts':data.shift);
  if(data.source_rows)add('Source rows',compactJson(data.source_rows));
  if(data.excluded_records?.length)add('Excluded records',data.excluded_records.map(r=>`${r.record_id}: ${r.error}`).join('; '));
  if(entry.tool==='render_chart')add('Chart',`${data.type}; x=${data.x}; y=${data.y}${data.y2?'; y2='+data.y2:''}; unit=${data.unit}; from ${data.source_result_id}`);
  if(data.dataset_version)add('Dataset',String(data.dataset_version).slice(0,16)+'…');
  if(meta.children.length)d.append(meta);
  const sql=data.trace?.sql||data.daily_sql; const params=data.trace?.parameters;
  if(sql){const q=node('details',undefined,'trace-sql');q.append(node('summary','SQL and bound values'));q.append(node('pre',String(sql).trim()+(params?`\n\n-- bound values\n${JSON.stringify(params)}`:'')));d.append(q);}
  else if(entry.tool!=='render_chart'){d.append(node('p','No SQL: this tool composes other results.','muted'));}
 }
 const full=node('details',undefined,'trace-json');full.append(node('summary','Full result JSON'));full.append(node('pre',JSON.stringify(entry,null,2)));d.append(full);
 return d;
}
