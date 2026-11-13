'use strict';
// The data browser (issue #142): /data lists the tables every answer reads; /data/<table> shows a table's schema
// (each column's type, unit and meaning from the record contract, with a link to its row there) and its rows, 50 at a
// time, newest first, filtered by the address itself, so the URL is the state and a receipt on an answer can link
// straight to the rows behind its figure. The only requests are GET /data.json and GET /data/<table>.json; the
// filters are the table's own columns, bound as parameters by the server (datapages.py). Nothing here writes, and
// nothing here runs typed SQL.
const $=id=>document.getElementById(id);
function el(tag,text,cls){ const e=document.createElement(tag); if(text!==undefined)e.textContent=text; if(cls)e.className=cls; return e; }
function link(text,href,cls){ const a=el('a',text,cls); a.href=href; return a; }
// A column that names a row of another table links there (a setup's paper run, a lane's customer, a stop's setup).
const FK={setup_id:['setups','setup_id'],split_from:['setups','setup_id'],wet_end_id:['wet_ends','wet_end_id'],customer_id:['customers','customer_id']};
const LINKED_TYPES=['VARCHAR','INTEGER','BOOLEAN'];   // a cell of these is a link that adds its value as a filter
const LONG_TEXT=['comment','name'];                    // free text wraps and is never offered as a filter
let state=null;   // the table page on screen: the server's answer for this address

function route(path){ const m=/^\/data(?:\/([a-z_]+))?\/?$/.exec(path||''); return m?{table:m[1]||null}:null; }
async function getJson(url){
 const response=await fetch(url,{headers:{Accept:'application/json'}});
 let body={}; try{ body=await response.json(); }catch(e){}
 if(!response.ok)throw new Error(body.error||`The data browser answered ${response.status}`);
 return body;
}
function number(v){ return Number.isInteger(v)?String(v):String(Number(v.toFixed(4))); }
// A value as the page shows it: numbers to four decimals at most (the JSON and the CSV carry every digit), lists by
// their length (all of it on hover), a map as its pairs, null as a dash.
function shown(v){
 if(v===null||v===undefined)return {text:'—',title:'null'};
 if(typeof v==='boolean')return {text:String(v)};
 if(typeof v==='number')return {text:number(v),title:Number.isInteger(v)?undefined:String(v)};
 if(Array.isArray(v))return {text:v.length<=3?v.join(', ')||'[]':`${v.length} items`,title:v.join(', ')};
 if(typeof v==='object'){ const pairs=Object.entries(v).map(([k,x])=>`${k} ${typeof x==='number'?number(x):x}`); return {text:pairs.join(' · '),title:pairs.join('\n')}; }
 return {text:String(v)};
}
const valueText=v=>String(v);
const valuesOf=v=>Array.isArray(v)?v:[v];   // a filter given more than once is a list: any of its values matches
// The address of this table page with one filter set (or removed, value null; or one value of a list removed, `drop`),
// back on its first page. A day replaces a span and a span a day. The order is the server's canonical one, so a link
// built here is the one the server would write.
function withFilter(name,value,drop){
 const filters={...state.filters};
 if(drop!==undefined){ const left=valuesOf(filters[name]).filter(v=>v!==drop); if(left.length)filters[name]=left.length===1?left[0]:left; else delete filters[name]; }
 else if(value===null)delete filters[name];
 else{ filters[name]=value; if(name==='day'){delete filters.from;delete filters.to;} if(name==='from'||name==='to')delete filters.day; }
 const parts=state.filterable.filter(k=>k in filters).flatMap(k=>[...valuesOf(filters[k])].sort((a,b)=>valueText(a)<valueText(b)?-1:valueText(a)>valueText(b)?1:0).map(v=>`${k}=${encodeURIComponent(valueText(v))}`));
 return `/data/${state.table}`+(parts.length?'?'+parts.join('&'):'');
}

// ---- /data: the tables
function renderIndex(data){
 document.title='Data · Shift Investigator';
 $('data-title').textContent='The data';
 $('crumbs').replaceChildren(el('span','data'));
 const w=data.window||{};
 $('data-status').textContent=`${data.tables.length} tables · production days ${w.first_day} to ${w.last_day} · dataset ${data.dataset_hash} · read once, they do not change while the server runs`;
 const card=el('section',undefined,'card'), wrap=el('div',undefined,'table-scroll'), table=el('table',undefined,'data-index');
 wrap.setAttribute('tabindex','0'); wrap.setAttribute('role','region'); wrap.setAttribute('aria-label','The tables');
 const head=el('thead'), hr=el('tr'); for(const label of ['Table','What it is','Rows in the window','First day','Last day','Columns']){ const th=el('th',label); th.setAttribute('scope','col'); hr.append(th); } head.append(hr);
 const body=el('tbody');
 for(const t of data.tables){
  const tr=el('tr'), name=el('td'); name.append(link(t.name,t.url,'table-name'),el('div',t.label,'muted'));
  const meaning=el('td'); meaning.append(el('span',t.meaning+' '),link('contract ↗',t.contract,'contract-link'));
  tr.append(name,meaning,el('td',t.rows.toLocaleString('en-US'),'num'),el('td',t.first_day||'— (a dimension)'),el('td',t.last_day||'—'),el('td',String(t.columns),'num'));
  body.append(tr);
 }
 table.append(head,body); wrap.append(table); card.append(el('h2','Tables'),wrap,
  el('p',`Open a table for its schema and its rows, ${data.per_page} at a time, newest first. Filters live in the address (?day=2026-09-14, ?shift=2, ?order=SO-…, ?reason=…), so a link is the page; each table downloads as CSV, up to ${data.csv_limit.toLocaleString('en-US')} rows a filter.`,'muted'));
 $('data-body').replaceChildren(card);
}

// ---- /data/<table>: the schema and the rows
function filterBar(page){
 const bar=el('div',undefined,'data-filters');
 const chips=el('div',undefined,'data-chips');
 const applied=Object.entries(page.filters);
 if(!applied.length)chips.append(el('span','No filter: every row of the window, newest first.','muted'));
 for(const [k,v] of applied)for(const one of valuesOf(v)){ const chip=el('span',undefined,'chip'), x=link('×',Array.isArray(v)?withFilter(k,null,one):withFilter(k,null),'chip-x'); x.setAttribute('aria-label',`Remove the ${k} filter ${valueText(one)}`); chip.append(el('code',`${k} = ${valueText(one)}`),x); chips.append(chip); }
 if(applied.some(([,v])=>Array.isArray(v)))chips.append(el('span','A filter given more than once matches any of its values.','muted'));
 const form=el('form',undefined,'data-add'), pick=el('select'), input=el('input'), go=el('button','Filter');
 pick.setAttribute('aria-label','Column to filter'); input.setAttribute('aria-label','Value'); input.type='text'; input.maxLength=120; input.placeholder='value'; go.type='submit';
 for(const name of page.filterable){ const o=el('option',name); o.value=name; pick.append(o); }
 pick.value=page.filterable.includes('day')?'day':page.filterable[0];
 const submit=event=>{ if(event&&event.preventDefault)event.preventDefault(); const value=String(input.value||'').trim(); if(!value)return; location.assign(withFilter(pick.value,value)); };
 form.addEventListener('submit',submit); go.addEventListener('click',submit);
 form.append(pick,input,go);
 bar.append(chips,form);
 return bar;
}
function cell(page,column,value){
 const td=el('td'), s=shown(value);
 if(typeof value==='number')td.className='num'; else if(LONG_TEXT.includes(column.name))td.className='text';
 if(s.title&&s.title!==s.text)td.title=s.title;
 const fk=FK[column.name];
 if(value!==null&&value!==undefined&&fk&&!(fk[0]===page.table&&column.name===fk[1])){
  td.append(link(s.text,`/data/${fk[0]}?${fk[1]}=${encodeURIComponent(value)}`,'fk'));
 }else if(value!==null&&value!==undefined&&column.name==='production_date'&&page.filterable.includes('day')){
  td.append(link(s.text,withFilter('day',value),'facet'));
 }else if(value!==null&&value!==undefined&&column.filter&&LINKED_TYPES.includes(column.type)&&!LONG_TEXT.includes(column.name)&&String(value).length<=120){
  const a=link(s.text,withFilter(column.name,value),'facet'); a.title=`Only rows where ${column.name} = ${valueText(value)}`; td.append(a);
 }else td.textContent=s.text;
 return td;
}
function rowsCard(page){
 const card=el('section',undefined,'card'), headRow=el('div',undefined,'section-heading');
 headRow.append(el('h2','Rows'));
 const actions=el('div',undefined,'report-actions');
 actions.append(link(`CSV (up to ${page.csv_limit.toLocaleString('en-US')} rows)`,page.csv,'nav-btn'),link('JSON',page.url.replace(/^\/data\/([a-z_]+)/,'/data/$1.json'),'nav-btn'));
 headRow.append(actions); card.append(headRow,filterBar(page));
 const first=(page.page-1)*page.per_page+1, last=first+page.rows.length-1;
 card.append(el('p',page.count?`${page.count.toLocaleString('en-US')} rows match · showing ${first.toLocaleString('en-US')}–${last.toLocaleString('en-US')}, newest first · page ${page.page} of ${page.pages.toLocaleString('en-US')} · ${page.elapsed_ms} ms on the server`:'No rows match these filters.','muted data-count'));
 if(page.rows.length){
  const wrap=el('div',undefined,'table-scroll data-rows'), table=el('table');
  wrap.setAttribute('tabindex','0'); wrap.setAttribute('role','region'); wrap.setAttribute('aria-label',`Rows of ${page.table}`);
  const head=el('thead'), hr=el('tr');
  for(const c of page.columns){ const th=el('th',c.name); th.setAttribute('scope','col'); th.title=`${c.meaning} (${c.unit})`; hr.append(th); }
  head.append(hr);
  const body=el('tbody'); for(const r of page.rows){ const tr=el('tr'); for(const c of page.columns)tr.append(cell(page,c,r[c.name])); body.append(tr); }
  table.append(head,body); wrap.append(table); card.append(wrap);
 }
 const pager=el('div',undefined,'data-pager');
 if(page.newer)pager.append(link('← Newer',page.newer,'nav-btn'));
 if(page.older)pager.append(link('Older →',page.older,'nav-btn'));
 if(pager.children.length)card.append(pager);
 return card;
}
function schemaCard(page){
 const card=el('section',undefined,'card'), head=el('div',undefined,'section-heading');
 head.append(el('h2','Schema'),link('the record contract ↗',page.contract,'nav-btn'));
 card.append(head);
 const wrap=el('div',undefined,'table-scroll data-schema'), table=el('table');
 wrap.setAttribute('tabindex','0'); wrap.setAttribute('role','region'); wrap.setAttribute('aria-label',`Schema of ${page.table}`);
 const thead=el('thead'), hr=el('tr'); for(const label of ['Column','Type','Unit','Meaning','Contract']){ const th=el('th',label); th.setAttribute('scope','col'); hr.append(th); } thead.append(hr);
 const body=el('tbody');
 for(const c of page.columns){
  const tr=el('tr'), name=el('td'); name.append(el('code',c.name)); if(c.filter)name.append(el('span',' · filter','muted'));
  const row=el('td'); row.append(link('row ↗',c.contract,'contract-link'));
  tr.append(name,el('td',c.type,'mono'),el('td',c.unit),el('td',c.meaning),row); body.append(tr);
 }
 table.append(thead,body); wrap.append(table); card.append(wrap);
 return card;
}
function sqlCard(page){
 const box=el('details',undefined,'card data-sql'); box.append(el('summary','The query this page ran'));
 box.append(el('pre',`${page.sql}\n-- bound: ${JSON.stringify(page.parameters)}\n\n${page.count_sql}`,'sql'));
 box.append(el('p','A fixed SELECT over an allowlisted table and its columns: every value above is a bound parameter, with a LIMIT and the store\'s own time cap. Visitors cannot edit it.','muted'));
 return box;
}
function renderTable(page){
 state=page;
 document.title=`${page.label} · Data · Shift Investigator`;
 $('data-title').textContent=page.label;
 const crumbs=$('crumbs'); crumbs.replaceChildren(link('data','/data'),el('span',` / ${page.table}`));
 $('data-lede').textContent=page.meaning;
 $('data-status').textContent=`${page.table} · production days ${page.window.first_day} to ${page.window.last_day}`;
 $('data-body').replaceChildren(schemaCard(page),rowsCard(page),sqlCard(page));   // the schema, then the rows below it (the issue's order)
 try{ const here=location.pathname+(location.search||''); if(page.url&&page.url!==here&&history&&history.replaceState)history.replaceState(null,'',page.url); }catch(e){}   // the canonical spelling in the address bar
}
function renderError(message){
 $('data-status').textContent='';
 const note=el('p',message,'warning-note'); note.setAttribute('role','alert');
 const back=el('p'); back.append(link('All tables','/data'));
 $('data-body').replaceChildren(note,back);
}
async function init(){
 const where=route(location.pathname);
 if(!where){ renderError('Not a data address: /data or /data/<table>.'); return; }
 try{
  if(!where.table)renderIndex(await getJson('/data.json'));
  else renderTable(await getJson(`/data/${where.table}.json${location.search||''}`));
 }catch(e){ renderError(e.message); }
}
init();
