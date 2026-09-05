/* Lifted out of xxops.html unchanged.
 *
 * A classic script rather than a module, on purpose: these stay
 * global, so every caller left in the page keeps working.
 */

/* ---------- commands ---------- */
const CMD_CHANGING = ["restart-gateway","stop-cmix","start-cmix","restart-chain"];
let agentHosts=null, agentsLoading=false, cmdCat=null, cmdErr=null,
    cmdAction=null, cmdTargets=[], cmdArmed=false, cmdRunning=false, cmdResults=[],
    cmdStopReq=false, cmdKeepGoing=false;

function cmdRoleOf(h){
  const e = (agentHosts||{})[h];
  return (e && typeof e === "object") ? (e.role || null) : null;
}

async function loadAgents(){
  if(agentsLoading) return;
  agentsLoading = true; cmdErr = null;
  try{
    const d = await aget("/api/agent/hosts");
    if(!d.ok){ agentHosts = {}; cmdCat = {};
               cmdErr = d.message || "Could not list the agents."; return; }
    agentHosts = d.hosts || {};

    /* catalogues are role-uniform, so one probe per role is enough */
    const probe = {};
    Object.keys(agentHosts).forEach(h=>{
      const r = cmdRoleOf(h);
      if(r && !probe[r]) probe[r] = h;
    });
    if(!Object.keys(probe).length){
      cmdCat = {}; cmdErr = "No agents are cached yet. Run "+
        "sudo xxops-cmd discover on the monitor."; return;
    }
    const cat = {}, roles = Object.keys(probe);
    for(let i=0; i<roles.length; i++){
      const role = roles[i];
      const a = await apost("/api/agent/actions", {host: probe[role]});
      if(!a.ok){
        cmdErr = "Could not read the catalogue from "+probe[role]+
                 (a.message ? " \u2014 "+a.message : "");
        continue;
      }
      const acts = a.actions || {};
      Object.keys(acts).forEach(name=>{
        if(!cat[name]) cat[name] = {desc: String(acts[name]||""), roles: {}};
        cat[name].roles[role] = 1;
      });
    }
    cmdCat = cat;
    if(!Object.keys(cat).length && !cmdErr)
      cmdErr = "The agents reported no actions.";
  }catch(err){
    cmdCat = cmdCat || {};
    cmdErr = "Something went wrong loading commands: "+(err && err.message);
  }finally{
    agentsLoading = false;
    if(view === "cmd") render();
  }
}

function cmdHostsFor(action){
  const meta = (cmdCat||{})[action];
  if(!meta) return [];
  return Object.keys(agentHosts||{})
    .filter(h=>meta.roles[cmdRoleOf(h)]).sort();
}
function cmdIsChanging(name){
  /* the agent prefixes the description of anything in its CHANGES catalogue.
     the hardcoded list stays as a second opinion, so it takes both being
     wrong to get this wrong. */
  const meta = (cmdCat || {})[name] || {};
  return CMD_CHANGING.indexOf(name) >= 0 ||
         /^\s*CHANGES THINGS\b/i.test(meta.desc || "");
}

function cmdDesc(desc){
  /* the group heading already says "Makes changes" */
  return String(desc || "").replace(/^\s*CHANGES THINGS\s*[-\u2014:]\s*/i, "");
}

function csay(text, bad){
  const m = document.getElementById("cmdMsg");
  if(!m) return;
  m.hidden = false; m.textContent = text; m.classList.toggle("bad", !!bad);
}

function vCommands(){
  if(!(acct && acct.role === "owner"))
    return '<div class="note">Commands are available to the fleet owner only.</div>';
  if(cmdErr)
    return '<div class="note amsg bad">'+esc(cmdErr)+'</div>';
  if(agentHosts === null || cmdCat === null){
    loadAgents();
    return '<div class="note">Reading the catalogue from your agents\u2026</div>';
  }

  const all = Object.keys(cmdCat).sort();
  const changing = all.filter(cmdIsChanging);
  const readonly = all.filter(a=>!cmdIsChanging(a));
  const isChg = cmdAction && cmdIsChanging(cmdAction);
  const hosts = cmdAction ? cmdHostsFor(cmdAction) : [];

  const chip = a =>
    '<button class="chip'+(cmdIsChanging(a)?' danger':'')+
    (cmdAction===a?' on':'')+'" data-cmda="'+esc(a)+'">'+esc(a)+'</button>';

  let h = '<div class="note">Pick a command, then choose what it runs on. '+
    'Commands that change something ask first and run one machine at a time, '+
    'stopping if one fails.</div>';

  h += '<div class="cmdgrp"><h4>Read only</h4><div class="chips">'+
       readonly.map(chip).join('')+'</div></div>';
  if(changing.length)
    h += '<div class="cmdgrp"><h4>Makes changes</h4><div class="chips">'+
         changing.map(chip).join('')+'</div></div>';

  if(cmdAction){
    const meta = cmdCat[cmdAction] || {desc:"", roles:{}};
    h += '<div class="cmdgrp">';
    if(meta.desc) h += '<div class="cmddesc"><b>'+esc(cmdAction)+'</b> \u2014 '+
                       esc(cmdDesc(meta.desc))+'</div>';
    h += '<h4>'+(isChg?'Machine':'Machines')+'</h4>';
    if(!hosts.length){
      h += '<div class="note">No agent offers this one.</div>';
    } else {
      h += '<div class="tools" style="margin:0 0 9px">'+
        '<button class="btn ghost" id="cmdAll">Select all</button>'+
        '<button class="btn ghost" id="cmdNone">Clear</button></div>'+
        '<div class="hostgrid">'+hosts.map(x=>
          '<label class="hostpick"><input type="checkbox" data-cmdh="'+esc(x)+'"'+
          (cmdTargets.indexOf(x)>=0?' checked':'')+'>'+esc(x)+'</label>').join('')+'</div>';
      if(isChg && cmdTargets.length){
        h += '<label class="cmdconfirm"><input type="checkbox" id="cmdArm"'+
          (cmdArmed?' checked':'')+'> Yes \u2014 run <b>'+esc(cmdAction)+'</b> on <b>'+
          cmdTargets.length+'</b> machine'+(cmdTargets.length>1?'s':'')+
          ', one after another</label>'+
          '<label class="hostpick" style="margin-bottom:9px"><input type="checkbox" id="cmdKeep"'+
          (cmdKeepGoing?' checked':'')+'>Keep going if one fails</label>';
      }
    }
    if(hosts.length)
      h += '<div class="tools"><button class="btn" id="cmdRun"'+(cmdRunning?' disabled':'')+
        '>'+(cmdRunning?'Running\u2026':'Run')+'</button>'+
        (cmdRunning?'<button class="btn ghost" id="cmdStop">Stop</button>':'')+
        (cmdTargets.length?'<span class="note" style="padding:0 4px">'+
          cmdTargets.length+' selected</span>':'')+'</div>';
        '>'+(cmdRunning?'Running\u2026':'Run')+'</button>'+
        (cmdTargets.length?'<span class="note" style="padding:0 4px">'+
          cmdTargets.length+' selected</span>':'')+'</div>';
    h += '<div class="note amsg" id="cmdMsg" hidden></div></div>';
  }

  if(cmdResults.length)
    h += '<div class="cmdgrp"><h4>Result</h4>'+cmdResults.map(r=>
      '<div class="cmdres'+(r.ok?'':' bad')+'"><b>'+esc(r.host)+'</b>'+
      (r.meta?'<span class="cmdmeta">'+esc(r.meta)+'</span>':'')+
      '<pre>'+esc(r.text)+'</pre></div>').join('')+'</div>';

  return h;
}

function cmdClean(text){
  /* the hardened agent unit restricts address families, so sudo cannot reach
     the audit socket. Harmless, but it is the only thing most actions print. */
  return String(text||"").split("\n")
    .filter(l=>!/^sudo: unable to send audit message/.test(l.trim()))
    .join("\n").trim();
}

function cmdText(d){
  if(!d.ok) return d.message || "That did not work.";
  const r = d.result || {};
  if(typeof r === "string") return cmdClean(r) || r;
  const out = cmdClean(r.output);
  if(out) return out;
  if(typeof r.message === "string" && r.message.trim()) return r.message.trim();
  if(r.exit === 0 || r.exit === undefined) return "Done \u2014 nothing to report.";
  try{ return JSON.stringify(r, null, 1); }catch(e){ return String(r); }
}

function cmdMeta(d){
  const r = (d && d.result) || {};
  const bits = [];
  if(r.exit !== undefined) bits.push("exit "+r.exit);
  if(r.seconds !== undefined) bits.push(r.seconds+"s");
  return bits.join(" \u00b7 ");
}

async function cmdRunNow(){
  const isChg = cmdIsChanging(cmdAction);
  if(!cmdTargets.length) return csay("Choose at least one machine.", 1);
  if(isChg && !cmdArmed) return csay("Tick the confirmation before running this.", 1);

  cmdRunning = true; cmdStopReq = false; cmdResults = []; render();
  const queue = cmdTargets.slice();
  for(let i=0; i<queue.length; i++){
    if(cmdStopReq){
      cmdResults.push({host:"stopped", ok:false,
        text:"Stopped before "+queue[i]+". "+(queue.length-i)+" machine"+
             (queue.length-i>1?"s":"")+" not touched."});
      break;
    }
    const host = queue[i];
    const body = {host: host, action: cmdAction};
    if(isChg) body.confirm = true;
    const d = await apost("/api/agent/run", body);
    const r = d.result || {};
    const good = !!d.ok && !r.error && r.ok !== false &&
                 (r.exit === undefined || r.exit === 0);
    cmdResults.push({host: host, text: cmdText(d), meta: cmdMeta(d), ok: good});
    render();
    /* an upgrade that fails on the first machine is exactly when you do not
       want it to carry on to the other forty five */
    if(!good && !cmdKeepGoing && queue.length > 1){
      cmdResults.push({host:"stopped", ok:false,
        text:"Stopped after "+host+" failed. "+(queue.length-i-1)+" machine"+
             (queue.length-i-1>1?"s":"")+" not touched. Tick \u201ckeep going\u201d to run through failures."});
      break;
    }
  }
  cmdRunning = false; cmdArmed = false; cmdStopReq = false; render();
}

document.getElementById("view").addEventListener("click", async e=>{
  const b = e.target.closest("button");
  if(!b) return;
  if(b.dataset.cmda){
    cmdAction = (cmdAction === b.dataset.cmda) ? null : b.dataset.cmda;
    cmdTargets = []; cmdArmed = false; cmdResults = []; render(); return;
  }
  if(b.id === "cmdAll"){ cmdTargets = cmdHostsFor(cmdAction); render(); return; }
  if(b.id === "cmdNone"){ cmdTargets = []; render(); return; }
  if(b.id === "cmdStop"){ cmdStopReq = true; csay("Stopping after this machine\u2026"); return; }
  if(b.id === "cmdRun"){ await cmdRunNow(); return; }
});

document.getElementById("view").addEventListener("change", e=>{
  const t = e.target;
  if(t.dataset && t.dataset.cmdh){
    const host = t.dataset.cmdh;
    const at = cmdTargets.indexOf(host);
    if(t.checked && at < 0) cmdTargets.push(host);
    if(!t.checked && at >= 0) cmdTargets.splice(at, 1);
    const n = document.getElementById("cmdMsg");
    /* repaint, or the confirmation and the count sit stale until the next
       30s tick - which reads as the app being slow rather than idle */
    if(n) n.hidden = true; render();
    return;
  }
  if(t.id === "cmdKeep"){ cmdKeepGoing = !!t.checked; render(); return; }
  if(t.id === "cmdArm"){ cmdArmed = !!t.checked; render(); return; }
});
