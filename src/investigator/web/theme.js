// Theme: the stylesheet follows prefers-color-scheme by default. A visitor can pin light or dark;
// the choice lives in this browser's localStorage only (a convenience, never state the app relies on).
// Loaded synchronously in <head> so a pinned theme applies before first paint; the toggle wires up
// once the header button exists. No inline script: the server's CSP is script-src 'self'.
(function(){
 var root=document.documentElement;
 try{var saved=localStorage.getItem('csi-theme');if(saved==='dark'||saved==='light')root.dataset.theme=saved;}catch(e){}
 function dark(){return root.dataset.theme==='dark'||(!root.dataset.theme&&window.matchMedia&&window.matchMedia('(prefers-color-scheme: dark)').matches);}
 function wire(){
  var b=document.getElementById('theme'); if(!b)return;
  function paint(){var d=dark();b.textContent=d?'Light':'Dark';b.setAttribute('aria-pressed',String(d));b.setAttribute('aria-label',d?'Switch to the light theme':'Switch to the dark theme');}
  b.addEventListener('click',function(){var next=dark()?'light':'dark';root.dataset.theme=next;try{localStorage.setItem('csi-theme',next);}catch(e){}paint();});
  paint();
 }
 if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',wire);else wire();
})();
