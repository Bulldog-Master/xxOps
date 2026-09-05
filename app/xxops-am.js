/* Lifted out of xxops.html unchanged.
 *
 * A classic script rather than a module, on purpose: these stay
 * global, so every caller left in the page keeps working.
 */

const AM=()=>"/am";
function parseReceivers(cfg){
  const out={}; let inSec=false, cur=null;
  for(const raw of (cfg||"").split("\n")){
    if(/^receivers:/.test(raw)){ inSec=true; continue; }
    if(inSec && raw && !/^[\s-]/.test(raw)) break;
    if(!inSec) continue;
    const m=raw.match(/^-\s*name:\s*(.+)$/);
    if(m){ cur=m[1].trim(); out[cur]=[]; continue; }
    const k=raw.match(/^\s+([a-z_]+)_configs:/);
    if(k&&cur) out[cur].push(k[1]);
  }
  return out;
}
function parseSeverityRoutes(cfg){
  const out=[], lines=(cfg||"").split("\n");
  for(let i=0;i<lines.length;i++){
    const m=lines[i].match(/^\s*-\s*severity="(\w+)"/);
    if(!m) continue;
    for(let j=i;j>Math.max(0,i-6);j--){
      const r=lines[j].match(/receiver:\s*(\S+)/);
      if(r){ out.push([m[1],r[1]]); break; }
    }
  }
  return out;
}
async function loadAlertmanager(){
  try{
    const r=await fetch(`${AM()}/api/v2/status`,{cache:"no-store"});
    if(!r.ok) throw 0;
    const d=await r.json(), cfg=(d.config||{}).original||"";
    amInfo={ version:(d.versionInfo||{}).version, uptime:d.uptime,
             receivers:parseReceivers(cfg), routes:parseSeverityRoutes(cfg),
             defaultReceiver:(cfg.match(/^route:\n(?:\s+.*\n)*?\s*receiver:\s*(\S+)/m)||[])[1] };
  }catch(e){ amInfo=null; }
}
async function loadAlerts(){
  try{
    const r=await fetch(`${AM()}/api/v2/alerts?silenced=false&inhibited=false`,{cache:"no-store"});
    alerts = r.ok ? await r.json() : [];
  }catch(e){ alerts=[]; }
}

// what has fired on either half of this validator, and have we solved it before
function fixHint(r){
  if(!fixes.length || !alerts.length) return null;
  const mine = alerts.filter(a=>{
    const i=(a.labels||{}).instance;
    return i===r.node || (r.gw && i===r.gw);
  });
  for(const a of mine){
    const name=(a.labels||{}).alertname;
    if(!name) continue;
    const m = fixes.find(f=>f.alertname===name);
    if(m) return {alert:name, entry:m};
  }
  return null;
}

async function loadSilences(){
  try{
    const r=await fetch(`${AM()}/api/v2/silences`,{cache:"no-store"});
    if(!r.ok) throw 0;
    silences=(await r.json()).filter(x=>x.status&&
      (x.status.state==="active"||x.status.state==="pending"));
    amUp=true;
  }catch(e){ silences=[]; amUp=false; }
}
