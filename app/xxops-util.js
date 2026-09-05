/* Formatting helpers, lifted out of xxops.html unchanged.
 *
 * Bytes to a string, seconds to a duration, a name to an avatar. No
 * application state is read here and none should be: everything in this file
 * takes arguments and returns a value.
 *
 * A classic script rather than a module, on purpose - these stay global, so
 * every caller left in xxops.html keeps working without being touched. */

const gb=b=>b==null?"—":b>=1e9?(b/1e9).toFixed(2)+" GB":b>=1e6?(b/1e6).toFixed(0)+" MB":(b/1e3).toFixed(0)+" KB";
const dur=s=>s<60?Math.round(s)+"s":s<3600?Math.round(s/60)+"m":s<86400?(s/3600).toFixed(1)+"h":(s/86400).toFixed(1)+"d";
const ago=t=>{const d=Date.now()/1000-t;return d<3600?Math.round(d/60)+"m ago":d<86400?Math.round(d/3600)+"h ago":Math.round(d/86400)+"d ago"};
const esc=s=>String(s??"").replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
function hash(s){let h=0;for(let i=0;i<s.length;i++){h=((h<<5)-h+s.charCodeAt(i))|0}return Math.abs(h)}
function identicon(nm,px){
  const h=hash(nm),hue=h%360,c1=`hsl(${hue} 62% 58%)`,c2=`hsl(${(hue+40)%360} 55% 32%)`;
  let cells="";
  for(let y=0;y<5;y++)for(let x=0;x<3;x++){
    if((hash(nm+x+y)>>3)%2){const mx=4-x;
      cells+=`<rect x="${x}" y="${y}" width="1" height="1"/><rect x="${mx}" y="${y}" width="1" height="1"/>}`}
  }
  cells=cells.replace(/}/g,"");
  return `<svg width="${px}" height="${px}" viewBox="0 0 5 5" style="border-radius:7px;background:${c2}">
    <g fill="${c1}">${cells}</g></svg>`;
}

/* ---------- notification config (via the backend) ---------- */
async function loadNotifyCfg(){
  try{
    const r = await fetch("/api/notify", {cache:"no-store"});
    notify = r.ok ? await r.json() : null;
  }catch(e){ notify = null; }
}
async function saveNotifyCfg(){
  notifyMsg = "Saving…"; paintSettings();
  try{
    const r = await fetch("/api/notify", {method:"POST",
      headers:{"Content-Type":"application/json"}, body: JSON.stringify(notify)});
    const d = await r.json();
    notifyMsg = (d.ok ? "\u2713 " : "") + (d.message || (d.ok ? "Saved." : "Could not save."));
    if(d.ok) await loadNotifyCfg();
  }catch(e){ notifyMsg = "Could not reach the server: " + e.message; }
  paintSettings();
}
