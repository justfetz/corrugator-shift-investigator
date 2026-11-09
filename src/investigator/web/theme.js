// Theme: light by default, whatever the OS prefers (issue #36: most visitors want charts on a light
// background). A visitor can pin light or dark with the header button; the choice lives in this
// browser's localStorage only (a convenience, never state the app relies on) and wins on later visits.
// Loaded synchronously in <head> so a pinned dark theme applies before first paint; the toggle wires up
// once the header button exists. No inline script: the server's CSP is script-src 'self'.
(function(){
 var root=document.documentElement;
 try{var saved=localStorage.getItem('csi-theme');if(saved==='dark'||saved==='light')root.dataset.theme=saved;}catch(e){}
 function dark(){return root.dataset.theme==='dark';}
 function wire(){
  var b=document.getElementById('theme'); if(!b)return;
  function paint(){var d=dark();b.textContent=d?'Light':'Dark';b.setAttribute('aria-pressed',String(d));b.setAttribute('aria-label',d?'Switch to the light theme':'Switch to the dark theme');}
  b.addEventListener('click',function(){var next=dark()?'light':'dark';root.dataset.theme=next;try{localStorage.setItem('csi-theme',next);}catch(e){}paint();});
  paint();
 }
 if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',wire);else wire();
})();
