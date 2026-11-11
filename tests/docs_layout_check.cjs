// Real-browser check of the case study page (issue #116), as layout_check.cjs is for the shell: at 1280x720 every
// section, the title block included, fits one screen height under the sticky header; the diagram keeps its lines;
// a phone never scrolls sideways; the print layout is the document alone, in the light palette under either theme;
// and the app header links to the page. Against a running server:
//   python -m investigator.server --port 7860 &   node tests/docs_layout_check.cjs [origin] [output-dir]
// With an output dir it writes case-study-1280x720.png, case-study-390.png and case-study.pdf there.
// Skips (exit 0) when Playwright is not installed; CI does not run it.
const assert=require('node:assert/strict');
let chromium; try{({chromium}=require('playwright'));}catch(e){console.log('docs layout check skipped: Playwright is not installed');process.exit(0);}
const origin=(process.argv[2]||'http://127.0.0.1:7860').replace(/\/+$/,''), out=process.argv[3]||null, url=origin+'/docs/case-study';
const measure=()=>{
 const header=document.querySelector('header.app').getBoundingClientRect().height, article=document.querySelector('.docs-page article');
 const heads=[...article.querySelectorAll('h1,h2')], y=e=>e.getBoundingClientRect().top+scrollY, end=article.getBoundingClientRect().bottom+scrollY;
 const diagram=article.querySelector('pre code.language-diagram'), pre=diagram&&diagram.parentElement;
 return {screen:Math.round(innerHeight-header),
  sections:heads.map((h,k)=>{const next=k+1<heads.length?y(heads[k+1])-parseFloat(getComputedStyle(heads[k+1]).marginTop):end;
   return {title:h.textContent,height:Math.round(next-y(h))};}),
  diagram:diagram?{white_space:getComputedStyle(diagram).whiteSpace,overflows:pre.scrollWidth>pre.clientWidth}:null,
  unfilled:/\{\{|\}\}/.test(article.textContent),page_scrolls_sideways:document.scrollingElement.scrollWidth>innerWidth};
};
(async()=>{
 const browser=await chromium.launch({args:['--no-proxy-server']}), errors=[];
 const watch=p=>{p.on('pageerror',e=>errors.push(String(e)));p.on('console',m=>{if(m.type()==='error')errors.push(m.text());});return p;};
 const desktop=watch(await browser.newPage({viewport:{width:1280,height:720},deviceScaleFactor:1}));
 await desktop.goto(url);
 const wide=await desktop.evaluate(measure);
 assert.equal(wide.unfilled,false,'every {{figure}} is filled');
 assert.deepEqual(wide.diagram,{white_space:'pre',overflows:false},'the diagram renders with its lines kept, inside the column');
 assert.equal(wide.page_scrolls_sideways,false);
 assert.ok(wide.sections.length===7&&wide.sections[0].title.startsWith('Case study'),JSON.stringify(wide.sections));
 for(const s of wide.sections)assert.ok(s.height<=wide.screen,`"${s.title}" is ${s.height}px, over one screen of ${wide.screen}px at 1280x720`);
 if(out)await desktop.screenshot({path:`${out}/case-study-1280x720.png`,fullPage:true});
 const toc=await desktop.evaluate(()=>[...document.querySelectorAll('.docs-toc .docs-section-link')].map(a=>a.textContent));
 assert.equal(toc[0],'Case study','first in the table of contents');
 // Print: the document alone, dark text on white even when the visitor pinned the dark theme.
 await desktop.evaluate(()=>{try{localStorage.setItem('csi-theme','dark');}catch(e){}});await desktop.reload();
 await desktop.emulateMedia({media:'print'});
 const print=await desktop.evaluate(()=>({hidden:['header.app','.docs-toc','.docs-pager'].map(s=>getComputedStyle(document.querySelector(s)).display),
  theme:document.documentElement.dataset.theme,ink:getComputedStyle(document.querySelector('.docs-page p')).color,bg:getComputedStyle(document.body).backgroundColor}));
 assert.deepEqual(print.hidden,['none','none','none']);assert.equal(print.theme,'dark');
 assert.equal(print.ink,'rgb(34, 34, 34)');assert.equal(print.bg,'rgb(255, 255, 255)');
 const pdf=await desktop.pdf({format:'Letter',margin:{top:'0.6in',bottom:'0.6in',left:'0.6in',right:'0.6in'},...(out?{path:`${out}/case-study.pdf`}:{})});
 const pages=(pdf.toString('latin1').match(/\/Type\s*\/Page[^s]/g)||[]).length;
 assert.ok(pages>=1&&pages<=6,`the PDF is ${pages} pages`);
 await desktop.evaluate(()=>{try{localStorage.removeItem('csi-theme');}catch(e){}});
 // A phone: one column, nothing wider than the screen (the diagram scrolls inside its own box).
 const phone=watch(await browser.newPage({viewport:{width:390,height:844},deviceScaleFactor:1}));
 await phone.goto(url);
 const narrow=await phone.evaluate(measure);
 assert.equal(narrow.page_scrolls_sideways,false,'the page never scrolls sideways at 390px');
 if(out)await phone.screenshot({path:`${out}/case-study-390.png`,fullPage:true});
 // The app header links to the page.
 const app=watch(await browser.newPage({viewport:{width:1280,height:720},deviceScaleFactor:1}));
 await app.goto(origin+'/');
 const link=await app.evaluate(()=>{const a=document.querySelector('header.app a.top-link[href="/docs/case-study"]'),r=a&&a.getBoundingClientRect();return a?{text:a.textContent,visible:r.width>0&&r.bottom<=innerHeight}:null;});
 assert.deepEqual(link,{text:'Case study',visible:true});
 assert.deepEqual(errors,[]);
 console.log(JSON.stringify({screen_1280x720:wide.screen,sections:wide.sections,diagram:wide.diagram,print,pdf_pages:pages,phone_scrolls_sideways:narrow.page_scrolls_sideways,app_link:link},null,1));
 console.log(`docs layout check passed: at 1280x720 each of the ${wide.sections.length} sections fits one screen (${wide.screen}px under the header); the diagram keeps its lines; a 390px phone never scrolls sideways; print is the document alone in the light palette (${pages} PDF pages); the app header links to the page.`);
 await browser.close();
})().catch(error=>{console.error(error);process.exit(1);});
