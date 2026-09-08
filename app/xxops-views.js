/* Lifted out of xxops.html unchanged.
 *
 * A classic script rather than a module, on purpose: these stay
 * global, so every caller left in the page keeps working.
 */

/* ---------- views ---------- */
function rowHTML(r){
  return `<div class="row ${r.state}" data-node="${esc(r.node)}">
    <div class="idc">${identicon(r.node,26)}<i></i></div>
    <div><div class="name">${esc(r.node)}</div>
      <div class="sub ${r.state==="down"?"bad":r.state==="recovering"?"warn":""}">${r.muted?'<span class="tag" style="margin-right:6px">muted</span>':""}
      ${r.why||`<span class="gw ${r.gwUp?"":"off"}"><i></i>${esc(r.gw||"no gateway paired")}</span>`}</div>
      ${(()=>{const hint=fixHint(r); return hint?`<div class="seenbefore" data-seen="${esc(hint.entry.id)}"
        title="open the recorded fix">Seen before \u00b7 ${esc(hint.entry.title)}</div>`:"";})()}</div>
    <div class="spark">${(r.spark||[]).map(v=>`<b style="height:${Math.max(4,Math.min(26,v*1.3))}px"></b>`).join("")}</div>
    <div class="round"><div class="rl">Round</div>
      <div class="rn ${r.state==="down"?"stale":""}" data-n="${esc(r.node)}">${r.round??"—"}</div></div>
    <div class="chain"><u>Block</u>${r.height??"—"}${r.lag>0?` <span class="warn">−${r.lag}</span>`:""}</div>
  </div>${openRow===r.node?expandHTML(r):""}`;
}
// One link-speed row. Two modes, two different questions:
//   health   this host to ITS OWN GATEWAY - the production path. Capped by the
//            gateway's VPS uplink, which varies from ~80 to ~530 across the
//            fleet, so a low number here is NOT proof the node is at fault.
//   capacity this host to a PEER NODE, rotating daily. Both ends are fast
//            boxes, so this is the node's real link.
// Showing both is the point: only together do they say WHICH SIDE is slow.
// Absent means no test has run, which is not a fault.
function lsRow(h,mode){
  const d=h.link&&h.link[mode]; if(!d) return "";
  if(d.up==null||d.down==null) return "";
  const label = mode==="health" ? "Link \u00b7 to gateway" : "Link \u00b7 capacity";
  // Amber only on the production path, and only when the measurement was
  // direct: a relayed test measures a DERP server, not the link.
  // Amber on CAPACITY, not health, and at the same 100 the
  // NodeLinkSlow rule uses -- otherwise the panel and Needs
  // Attention can disagree about the same host. Health is capped by
  // the gateway's uplink, so a low figure there says nothing about
  // whether the node is at fault. Never amber on a relayed reading:
  // that measured a DERP server rather than the link.
  const slow = mode==="capacity" && d.direct!==0 && d.up<(cfg.linkMbps||100);
  const relay = d.direct===0 ? " \u00b7 via relay" : "";
  const age = d.age!=null ? " \u00b7 "+dur(d.age)+" ago" : "";
  return `<div class="kv"><span>${label}</span><em class="${slow?"warn":""}">${d.up.toFixed(0)} \u2191 / ${d.down.toFixed(0)} \u2193 Mbps${relay}${age}</em></div>`;
}
function specKV(h){
  if(!h) return `<div class="kv"><span>Not reporting</span><em>—</em></div>`;
  const mem=h.memTotal?`${(h.memTotal/1e9).toFixed(0)} GB`:"—";
  const free=h.memAvail?` · ${(h.memAvail/1e9).toFixed(1)} free`:"";
  const tight=h.memAvail&&h.memTotal&&(h.memAvail/h.memTotal)<0.15;
  return `
   <div class="kv"><span>CPU</span><em>${esc(h.cpuModel||"—")}</em></div>
   <div class="kv"><span>CPU</span><em>${h.cores??"—"} cores · ${h.cpu!=null?h.cpu.toFixed(0)+"%":"—"}</em></div>
   <div class="kv"><span>Memory</span><em class="${tight?"warn":""}">${mem}${free}</em></div>
   <div class="kv"><span title="the root filesystem, where the xx software and its data live">Disk (/)</span><em class="${h.disk>cfg.disk?"bad":""}">${h.disk!=null?h.disk.toFixed(0)+"% of "+(h.diskSize/1e9).toFixed(0)+" GB":"—"}</em></div>
   <div class="kv"><span title="projected from how fast / has grown over the last 24 hours">Disk full in</span><em class="${h.diskDays!=null&&h.diskDays<30?"warn":""}">${h.diskDays==null?"not growing":h.diskDays>365?"over a year":Math.round(h.diskDays)+" days"}</em></div>
   <div class="kv"><span>Logs</span><em class="${h.logs>cfg.logGb*1e9?"warn":""}">${gb(h.logs)}${h.topLog?" · "+esc(h.topLog):""}</em></div>
   ${lsRow(h,"health")}
   ${lsRow(h,"capacity")}
   <div class="kv"><span>GPU</span><em>${h.gpu?esc(h.gpu.model.replace("NVIDIA GeForce ","")):"none"}</em></div>
   ${h.gpu?`<div class="kv"><span>cMix on GPU</span><em class="${h.gpu.attached===0?"warn":h.gpu.attached===1?"ok":""}">${h.gpu.attached===1?(h.gpu.cmixMem?(h.gpu.cmixMem/1e6).toFixed(0)+" MB held":"attached"):h.gpu.attached===0?"not attached":"—"}</em></div><div class="kv"><span>GPU</span><em>${h.gpu.temp??"—"}°C · ${h.gpu.power?h.gpu.power.toFixed(0)+"W":"—"} · ${h.gpu.memUsed?(h.gpu.memUsed/1e9).toFixed(1)+"/"+(h.gpu.memTotal/1e9).toFixed(0)+" GB":"—"}</em></div>`:""}
   <div class="kv"><span>Board</span><em>${esc(h.board||"—")}</em></div>
   <div class="kv"><span>BIOS</span><em>${esc(h.bios||"—")}${h.biosDate?" · "+esc(h.biosDate):""}</em></div>
   <div class="kv"><span>OS</span><em>${esc(h.os||"—")}</em></div>
   <div class="kv"><span>Kernel</span><em>${esc(h.kernel||"—")}</em></div>
   <div class="kv"><span>Chain build</span><em>${esc((h.version||"—").split("-")[0])}</em></div>
   <div class="kv"><span>Uptime</span><em>${h.boot?dur(Date.now()/1000-h.boot):"—"}</em></div>
   <div class="kv"><span>Temp · load</span><em>${h.temp?h.temp.toFixed(0)+"°C":"—"} · ${h.load?h.load.toFixed(2):"—"}</em></div>`;
}
function expandHTML(r){
  return `<div class="exp">
    <div class="half"><b class="h">Node · ${esc(r.node)}</b>${specKV(r.nodeH)}</div>
    <div class="half"><b class="h">Gateway · ${esc(r.gw||"none")}</b>${specKV(r.gwH)}</div>
    <div class="mini">
      ${(()=>{
        const p = pendingAct;
        if (p && p.node === r.node) {
          return `<div class="acts armed">
            <div class="actq">Restart <b>${esc(p.label)}</b> on <b>${esc(p.host)}</b>?
              ${p.warn?`<span class="actwarn">${esc(p.warn)}</span>`:""}</div>
            ${actBusy?`<span class="chip warn">Working\u2026</span>`
              :`<button class="chip actgo" data-act2="go">Yes, do it</button>
                <button class="chip" data-act2="no">Cancel</button>`}
          </div>`;
        }
        const gw = r.gw, node = r.node;
        const opts = [];
        if (gw) opts.push(["restart-gateway", "the gateway", gw, ""]);
        if (gw) opts.push(["start-gateway", "the gateway", gw, ""]);
        // Stopping the gateway SERVICE does not stop cMix earning - the
        // node keeps processing rounds. It goes deaf to peers, and left
        // that way it eventually costs the validator. Saying it stops
        // earning would be false, and false warnings teach people to
        // ignore the true ones.
        if (gw) opts.push(["stop-gateway", "the gateway", gw,
          "it will go deaf to peers - cMix keeps running rounds for now, but do not leave it stopped"]);
        opts.push(["stop-cmix", "cMix", node, "it will stop earning until you start it again"]);
        opts.push(["start-cmix", "cMix", node, ""]);
        // Unlike restarting the gateway, this DOES interrupt earning -
        // rounds stop until cMix is back. Brief and self-recovering, so
        // the wording says rounds pause rather than earning stops.
        opts.push(["restart-cmix", "cMix", node,
          "rounds pause until it comes back"]);
        opts.push(["restart-chain", "the chain process", gw || node, ""]);
        return `<div class="acts">
          <span class="actlabel">Actions</span>
          <select class="msel actsel" data-actsel="${esc(r.node)}">
            <option value="">Choose an action\u2026</option>
            ${opts.map(([id,label,host,warn])=>
              `<option value="${esc(id)}|${esc(label)}|${esc(host)}|${esc(warn)}">${esc(id.replace("-"," "))}</option>`).join("")}
          </select>
        </div>`;})()}
      ${(()=>{const key=r.node, out=checkOut[key];
        const acts=[["gossip-status","Gossip","gw"],["cert-expiry","Certificate","gw"],
                    ["watchdog-state","Watchdog","gw"],["cmix-status","cMix","node"],
                    ["chain-health","Chain","node"],["producer-status","Producer","any"],
                    ["service-status","Services","any"],["disk","Disk","any"]];
        return `<div class="checks">
          <select class="msel" data-chksel="${esc(r.node)}" title="which machine">
            ${r.gw?`<option value="${esc(r.gw)}">${esc(r.gw)}</option>`:""}
            <option value="${esc(r.node)}">${esc(r.node)}</option>
          </select>
          ${acts.map(([id,label])=>`<button class="chip" data-chk="${esc(r.node)}" data-act="${id}" style="cursor:pointer">${label}</button>`).join("")}
          ${checking===key?`<span class="chip warn">Asking\u2026</span>`:""}
          ${out?`<pre class="chkout">${esc(out)}</pre>`:""}
        </div>`;})()}
      <span class="chip">Last round <b>${r.secs?r.secs.toFixed(1)+"s":"—"}</b></span>
      <span class="chip">Recovered failures <b>${r.fails}</b></span>
      <span class="chip">Blocks authored <b>${r.authored??"—"}</b></span>
      <span class="chip">Gateway round <b>${r.gwRound??"—"}</b></span>
      ${r.gwWarn!=null?`<span class="chip">Gateway warnings <b>${r.gwWarn}</b></span>`:""}
      ${!amUp?"":r.muted
        ? `<span class="chip warn">${esc(r.mutedWhat||"Muted")} muted until ${new Date(r.mutedTo).toLocaleString([],{month:"short",day:"numeric",hour:"2-digit",minute:"2-digit"})}</span>
           ${muting===r.node ? `<span class="chip warn">Working\u2026</span>`
             : `<button class="chip" data-unmute-node="${esc(r.node)}" style="cursor:pointer;border-color:var(--mute)">Unmute</button>`}`
        : muting===r.node ? `<span class="chip warn">Muting\u2026</span>`
        : `<select class="msel" data-msel="${esc(r.node)}" title="what to silence">
             ${r.gw?`<option value="gw">${esc(r.gw)} only</option>`:""}
             <option value="node">${esc(r.node)} only</option>
             ${r.gw?`<option value="both">both halves</option>`:""}
           </select>
           <button class="chip" data-mute="${esc(r.node)}" data-h="1" style="cursor:pointer">Mute 1h</button>
           <button class="chip" data-mute="${esc(r.node)}" data-h="24" style="cursor:pointer">Mute 24h</button>
           <button class="chip" data-mute="${esc(r.node)}" data-h="168" style="cursor:pointer">Mute 7d</button>`}
      ${r.authIdle>7200?`<span class="chip warn">No new block in <b>${dur(r.authIdle)}</b></span>`:""}
    </div></div>`;
}
function vFleet(only,state){
  /* The pills are NESTED, not exclusive.

     offline means not running rounds, full stop - which includes every host
     that is down, since an unreachable host is certainly not running rounds.
     down is the narrower case: unreachable rather than merely idle.

     not earning is the same set as offline today. It is kept separate because
     it answers a different question - one about money rather than about
     reachability - and because anything else that stops a validator earning
     would join it without touching what offline means.

     These filters must agree with the counts in head(), or a pill shows one
     number and produces a different quantity of rows. */
  const notRunningRounds = r => r.state==="down" || r.state==="offline";
  const items = (state==="notearning" || state==="offline")
    ? rows.filter(notRunningRounds)
    : state ? rows.filter(r=>r.state===state)
    : (only?rows.filter(r=>r.state!=="steady"&&!r.muted):rows);
  if(!items.length) return `<div class="empty"><b>${state?"None right now.":"Nothing needs you."}</b>
    ${state?"No validator is in that state.":"Every validator is completing rounds and following the chain."}</div>`;
  return `<div class="list">${items.map(rowHTML).join("")}</div>`;
}
function vHosts(){
  const f=hosts.filter(h=>h.host.includes(q$)||String(h.validator).includes(q$));
  const vs=[...new Set(hosts.map(h=>h.version).filter(Boolean))];
  const maj=vs.map(v=>[v,hosts.filter(h=>h.version===v).length]).sort((a,b)=>b[1]-a[1])[0]?.[0];
  const det=openHost?hosts.find(h=>h.host===openHost):null;
  const soon=hosts.filter(h=>h.diskDays!=null&&h.diskDays<cfg.diskDays).sort((a,b)=>a.diskDays-b.diskDays);
  const fat=hosts.filter(h=>h.logs>cfg.logGb*1e9).sort((a,b)=>b.logs-a.logs);
  const lowmem=hosts.filter(h=>h.memAvail&&h.memTotal&&(h.memAvail/h.memTotal)<0.15);
  return `<div class="tools"><input class="inp" id="search" placeholder="Filter hosts…" value="${esc(q$)}">
      <span class="tag">${f.length} of ${hosts.length}</span></div>
    ${(soon.length||fat.length||lowmem.length)?`<div class="mini" style="margin:0 0 14px">
      ${soon.length?`<span class="chip warn">Disk filling <b>${soon.length}</b> · soonest ${esc(soon[0].host)} in ${Math.round(soon[0].diskDays)}d</span>`:""}
      ${fat.length?`<span class="chip warn">Big logs <b>${fat.length}</b> · worst ${esc(fat[0].host)} at ${gb(fat[0].logs)}</span>`:""}
      ${lowmem.length?`<span class="chip warn">Low memory <b>${lowmem.length}</b> · ${esc(lowmem[0].host)}</span>`:""}
    </div>`:""}
    ${det?`<div class="exp" style="border-radius:var(--r);border-top:1px solid var(--line);margin:0 0 12px">
      <div class="half"><b class="h">${esc(det.host)} · ${det.role}</b>${specKV(det)}</div>
      <div class="half"><b class="h">&nbsp;</b>
        <div class="kv"><span>Validator</span><em>${esc(det.validator)}</em></div>
        <div class="kv"><span>Reporting</span><em class="${det.up?"ok":"bad"}">${det.up?"yes":"no"}</em></div>
        <div class="kv"><span>Block height</span><em>${det.height??"—"}</em></div>
      </div></div>`:""}
    <table><thead><tr><th>Host</th><th class="hide-s">Role</th><th class="r">CPU</th><th class="r hide-s">RAM</th>
      <th class="r">Disk (/)</th><th class="r hide-s">Disk full in</th><th class="r">Logs</th><th class="r hide-s">GPU</th>
      <th class="r">Build</th></tr></thead><tbody>
    ${f.sort((a,b)=>(b.logs??0)-(a.logs??0)).map(h=>`<tr data-host="${esc(h.host)}">
      <td><span class="dot ${h.up?"d1":"d3"}" style="display:inline-block;margin-right:8px"></span>${esc(h.host)}</td>
      <td class="hide-s"><span class="tag">${h.role}</span></td>
      <td class="r mono">${h.cpu!=null?h.cpu.toFixed(0)+"%":"—"}</td>
      <td class="r mono hide-s">${h.memTotal?(h.memTotal/1e9).toFixed(0)+"G":"—"}</td>
      <td class="r mono ${h.disk>cfg.disk?"bad":h.disk>cfg.disk-15?"warn":""}">${h.disk!=null?h.disk.toFixed(0)+"%":"—"}</td>
      <td class="r mono hide-s ${h.diskDays!=null&&h.diskDays<30?"warn":""}">${h.diskDays==null?"—":h.diskDays>365?"1y+":Math.round(h.diskDays)+"d"}</td>
      <td class="r mono ${h.logs>cfg.logGb*1e9?"warn":""}">${gb(h.logs)}</td>
      <td class="r mono hide-s" style="color:var(--faint)">${h.gpu?esc(h.gpu.model.replace(/NVIDIA GeForce (RTX |GTX )?/,"")):"—"}</td>
      <td class="r mono ${h.version&&maj&&h.version!==maj?"warn":""}">${esc((h.version||"—").split("-")[0])}</td>
    </tr>`).join("")}</tbody></table>
    ${vs.length>1?`<div class="note" style="margin-top:12px">Version drift: ${vs.length} chain builds in the fleet. Amber marks hosts off the most common build.</div>`:""}`;
}
function vInc(){
  const aff=new Set(incidents.map(i=>i.inst)).size;
  return `<div class="tools"><div class="seg">${Object.keys(RANGES).map(k=>
      `<button data-rg="${k}" aria-pressed="${k===incRange}">${k}</button>`).join("")}</div>
      <span class="tag">${incidents.length} incidents · ${aff} hosts</span></div>
    ${!incidents.length?`<div class="empty"><b>No incidents recorded.</b>
      Nothing has gone down or hit a round failure in this window.</div>`:
    `<table><thead><tr><th>Host</th><th>What happened</th><th class="r hide-s">Started</th>
      <th class="r">Lasted</th></tr></thead><tbody>
    ${incidents.slice(0,200).map(i=>`<tr><td>${esc(i.inst)}</td>
      <td><span class="${i.sev==="down"?"bad":"warn"}">${esc(i.kind)}</span>${i.open?' <span class="tag">ongoing</span>':""}</td>
      <td class="r mono hide-s" style="color:var(--mute)">${ago(i.from)}</td>
      <td class="r mono">${dur(Math.max(60,i.to-i.from))}</td></tr>`).join("")}</tbody></table>`}
    <div class="note" style="margin-top:12px">Rebuilt from stored metrics, so history reaches back only
      as far as xxOps has been collecting. Round failures that resolve on their own show amber — they don't need you.</div>`;
}

function vChanges(){
  const unseen=changes.filter(c=>c.t>chgSeen).length;
  return `<div class="tools"><div class="seg">${Object.keys(RANGES).map(k=>
      `<button data-cr="${k}" aria-pressed="${k===chgRange}">${k}</button>`).join("")}</div>
      <span class="tag">${changes.length} change${changes.length===1?"":"s"}${unseen?` \u00b7 ${unseen} new`:""}</span></div>
    ${!chgLoaded ? `<div class="empty">Looking back over the last ${chgRange}\u2026</div>`
    : !changes.length ? `<div class="empty"><b>Nothing has changed.</b>
        No reboots, restarts, service changes or version moves in this window.</div>`
    : changes.map(c=>`<div class="chg ${c.t>chgSeen?"new":""}">
        <i class="${c.kind}"></i>
        <b>${esc(c.host||"")}</b><small>${esc(c.what)}</small>
        <u>${ago(c.t)}</u></div>`).join("")}
    <div class="note" style="margin-top:12px">Rebuilt from stored metrics, so this reaches
      back only as far as xxOps has been collecting. Incidents covers up and down;
      this covers everything else that moved.</div>`;
}

function render(){
  const v=document.getElementById("view");
    // The form is a live text buffer, not a view of the data. A background
  // tick landing mid-sentence would wipe what someone is typing, so once
  // the form exists, leave it alone. Save and cancel both clear fixAdding
  // before rendering, so they pass straight through.
  if((fixAdding || fixEditing) && v.querySelector(".fixform")) return;
  v.innerHTML = !view?"":view==="pill"?vFleet(false,pillFilter):view==="att"?vFleet(true):view==="val"?vFleet(false)
              :view==="host"?vHosts():view==="chg"?vChanges():view==="rank"?vRanking()
              :view==="fix"?vFixes():view==="cmd"?vCommands():vInc();
  v.querySelectorAll(".row").forEach(el=>el.onclick=()=>{
    openRow=openRow===el.dataset.node?null:el.dataset.node; render()});
  v.querySelectorAll("tr[data-host]").forEach(el=>el.onclick=()=>{
    openHost=openHost===el.dataset.host?null:el.dataset.host; render()});
  const s=document.getElementById("search");
  if(s) s.oninput=e=>{const p=e.target.selectionStart;q$=e.target.value.trim();render();
    const n=document.getElementById("search"); if(n){n.focus();try{n.setSelectionRange(p,p)}catch(x){}}};
  v.querySelectorAll("[data-rg]").forEach(b=>b.onclick=()=>{incRange=b.dataset.rg;incidents=[];loadIncidents()});
  v.querySelectorAll("[data-seen]").forEach(b=>b.onclick=e=>{
    e.stopPropagation(); fixOpen=b.dataset.seen; view="fix";
    document.querySelectorAll(".nb").forEach(n=>n.setAttribute("aria-selected",n.dataset.v==="fix"));
    render(); window.scrollTo({top:0}); });
  v.querySelectorAll("[data-fixopen]").forEach(b=>b.onclick=()=>{
    fixOpen = fixOpen===b.dataset.fixopen ? null : b.dataset.fixopen; render(); });
  v.querySelectorAll("[data-fixdel]").forEach(b=>b.onclick=e=>{
    e.stopPropagation(); deleteFix(b.dataset.fixdel); });
  v.querySelectorAll("[data-fixedit]").forEach(b=>b.onclick=e=>{
    e.stopPropagation(); fixEditing=b.dataset.fixedit; fixAdding=false; render(); });
  { const n=document.getElementById("fx-new"); if(n) n.onclick=()=>{fixAdding=true;render();}; }
  { const s=document.getElementById("fx-save"); if(s) s.onclick=saveFix; }
  { const c=document.getElementById("fx-cancel"); if(c) c.onclick=()=>{fixAdding=false;fixEditing=null;render();}; }
  v.querySelectorAll("[data-cr]").forEach(b=>b.onclick=()=>{chgRange=b.dataset.cr;chgLoaded=false;loadChanges()});
  v.querySelectorAll("[data-mute]").forEach(b=>b.onclick=e=>{
    e.stopPropagation(); mute(b.dataset.mute, +b.dataset.h); });
  v.querySelectorAll("[data-unmute]").forEach(b=>b.onclick=e=>{
    e.stopPropagation(); unmute(b.dataset.unmute); });
  v.querySelectorAll("[data-unmute-node]").forEach(b=>b.onclick=e=>{
    e.stopPropagation(); unmuteNode(b.dataset.unmuteNode); });
  v.querySelectorAll("[data-actsel]").forEach(sel=>{
    sel.onclick=e=>e.stopPropagation();
    sel.onchange=e=>{
      e.stopPropagation();
      if(!sel.value) return;
      // hostnames are [a-z0-9_.-] so a pipe cannot appear in one
      const [action,label,host,warn] = sel.value.split("|");
      pendingAct = {node:sel.dataset.actsel, action, label, host, warn};
      sel.value = "";
      render(); }; });
  v.querySelectorAll("[data-act2]").forEach(b=>b.onclick=e=>{
    e.stopPropagation();
    if(b.dataset.act2==="go") runAction(); else { pendingAct=null; render(); } });
  v.querySelectorAll("[data-chk]").forEach(b=>b.onclick=e=>{
    e.stopPropagation(); runCheck(b.dataset.chk, b.dataset.act); });
  v.querySelectorAll("[data-chksel]").forEach(s=>s.onclick=e=>e.stopPropagation());
  rows.filter(r=>r.moved).forEach(r=>{const el=v.querySelector(`.rn[data-n="${CSS.escape(r.node)}"]`);
    if(el){el.classList.add("tick");setTimeout(()=>el.classList.remove("tick"),90)}});
}

function banners(){
  const b=[];
  if(demo) b.push(["demo","Demo data — not connected to Prometheus.",null]);
  // Blunt on purpose. Everything below this is the last thing
  // that was true, not the thing that is true.
  if(promDown) b.push(["stale",`Cannot reach Prometheus. Nothing on this page is current — this is the last known state, from ${lastGood?ago(lastGood):"earlier"}.`,null]);
  const cap=hosts.filter(h=>h.diskDays!=null&&h.diskDays<cfg.diskDays)
                 .sort((a,b)=>a.diskDays-b.diskDays);
  if(cap.length) b.push(["cap",`${cap.length} host${cap.length>1?"s":""} will run out of disk within `+
    `${cfg.diskDays} days: `+cap.slice(0,3).map(h=>esc(h.host)+" ("+Math.round(h.diskDays)+"d)").join(", ")+
    (cap.length>3?"…":""),"Show hosts"]);
  const un=(discovered?.unpaired||[]).filter(h=>!dismissed.includes(h));
  if(un.length) b.push(["new",`${un.length} host${un.length>1?"s":""} reporting but not paired to a validator: ${un.slice(0,4).map(esc).join(", ")}${un.length>4?"…":""}`,"Set up"]);
  document.getElementById("banners").innerHTML=b.map(([k,t,btn])=>
    `<div class="banner"><span>${t}</span>${btn?`<button data-b="${k}">${btn}</button>`:""}</div>`).join("");
  document.querySelectorAll("[data-b]").forEach(x=>x.onclick=()=>{
    if(x.dataset.b==="cap"){ view="host"; openHost=null;
      document.querySelectorAll(".nb").forEach(n=>n.setAttribute("aria-selected",n.dataset.v==="host"));
      render(); window.scrollTo({top:0}); }
    else openSettings("hosts"); });
}

function isOwner(){ return !!(acct && acct.role === "owner"); }

/* cfg.name and cfg.avatar come from settings.json, which is the OWNER's.
   A contact must see themselves, not him. */
function whoName(){
  if(isOwner()) return cfg.name || "Operator";
  /* the state endpoint calls it "user"; username kept as a fallback */
  return (acct && (acct.user || acct.username)) || "Signed in";
}
function whoRole(){ return isOwner() ? "Fleet owner" : "Contact"; }

function avatarInto(el, nm){
  if(!el) return;
  if(cfg.avatar && isOwner()) el.innerHTML='<img src="'+cfg.avatar+'" alt="" '+
    'style="width:100%;height:100%;object-fit:cover;border-radius:50%">';
  else el.textContent=(nm||"OP").slice(0,2).toUpperCase();
}

/* header and account menu both painted here - #wn/#wrole in the header,
   #mname/#mrole in the menu, and the avatar in both places */
function paintWho(){
  const nm = whoName(), rl = whoRole();
  ["wn","mname"].forEach(id=>{
    const n = document.getElementById(id); if(n) n.textContent = nm; });
  ["wrole","mrole"].forEach(id=>{
    const r = document.getElementById(id); if(r) r.textContent = rl; });
  ["av","mav"].forEach(id=>avatarInto(document.getElementById(id), nm));
}

function paintAvatar(el){
  paintWho();
  avatarInto(el, whoName());   // whatever element was handed in, e.g. the
}                              // picture preview in the Profile section
function head(){
  const s=rows.filter(r=>r.state==="steady").length,rc=rows.filter(r=>r.state==="recovering").length,
        d=rows.filter(r=>r.state==="down").length,
        /* nested, not exclusive: a down host is unreachable, so it is
           certainly not running rounds, so it counts as offline too. The
           filters in vFleet() must match this or a pill lies about itself. */
        off=rows.filter(r=>r.state==="down"||r.state==="offline").length,
        ne=off;     /* everything not running rounds is not earning */
  c1.textContent=s;c2.textContent=rc;c3.textContent=d;
  { const c4=document.getElementById("c4"); if(c4) c4.textContent=off;
    const c5=document.getElementById("c5"); if(c5) c5.textContent=ne; }
  /* all four stay visible so the states are learnable - a pill that
     disappears takes with it the fact that the state exists at all, and an
     interface that drops controls reads as broken rather than calm. Zeros
     just recede. */
  [["steady",s],["recovering",rc],["down",d],["offline",off],
   ["notearning",ne]].forEach(([n,v])=>{
    const el=document.querySelector('[data-pill="'+n+'"]');
    if(el){ el.hidden=false; el.classList.toggle("zero", v===0); }
  });
  document.getElementById("headline").textContent =
    !rows.length ? "No validators set up yet." :
    /* five states make too many combinations to spell out, so compose:
       one problem reads as a sentence, several read as a list */
    (()=>{
      /* ne is derived from these two, so listing it would repeat them */
      const p=[[off,"offline"],[d,"down"]].filter(x=>x[0]);
      if(p.length===1) return p[0][0]===1
        ? "One validator is "+p[0][1]+"."
        : p[0][0]+" validators are "+p[0][1]+".";
      if(p.length) return p.map(x=>x[0]+" "+x[1]).join(", ")+".";
      return rc ? "Nothing broken. Some noise." : "All steady.";
    })();
  document.getElementById("stamp").textContent="checked "+new Date().toLocaleTimeString([],{hour:"2-digit",minute:"2-digit",second:"2-digit"});
  document.getElementById("n-att").textContent=rc+d+off+ne;
  document.getElementById("n-val").textContent=rows.length;
  document.getElementById("n-host").textContent=hosts.length;
  const _unseen=changes.filter(c=>c.t>chgSeen).length;
  const _cb=document.getElementById("n-chg");
  if(_cb){ _cb.textContent=_unseen||changes.length; _cb.classList.toggle("hot",_unseen>0); }
  { const _fb=document.getElementById("n-fix"); if(_fb) _fb.textContent=fixes.length; }
  document.getElementById("f1").textContent=`xxOps ${VERSION} `;
  document.getElementById("f2").textContent=`${rows.length} validators · ${hosts.length} hosts · refreshing every 30s`;
  document.getElementById("wn").textContent=cfg.name;
  paintAvatar(document.getElementById("av"));
  banners();
}


/* One row per host: name, bar, number, and the single stat most likely to
 * explain a low row. A phone cannot show Grafana's seven columns, and does not
 * need to - a ranking has to order and expose outliers, nothing more. */
function rankList(items, unit, extraLabel){
  if(!items.length) return `<div class="empty"><b>Nothing to rank.</b>
    No ${esc(unit)} reported a round count for the last 24 hours.</div>`;
  const top = Math.max(...items.map(r=>r.v), 1);
  return `<div class="list">` + items.map((r,i)=>{
    const pct = Math.max(2, Math.round(r.v / top * 100));
    return `<div class="row" style="display:flex;align-items:center;gap:10px;
        padding:9px 12px">
      <span class="mono" style="width:2.1em;text-align:right;opacity:.5">${i+1}</span>
      <span style="flex:0 0 8.5em;overflow:hidden;text-overflow:ellipsis;
        white-space:nowrap">${esc(r.host)}</span>
      <span style="flex:1 1 auto;height:8px;border-radius:5px;
        background:var(--line);overflow:hidden;min-width:40px">
        <span style="display:block;height:100%;width:${pct}%;
          background:var(--brand);border-radius:5px"></span></span>
      <span class="mono" style="width:4.2em;text-align:right">${r.v}</span>
      <span class="mono" style="width:3.4em;text-align:right;opacity:.55"
        title="${esc(extraLabel)}">${r.extra==null?"\u2014":Math.round(r.extra)}</span>
    </div>`;
  }).join("") + `</div>`;
}

function vRanking(){
  if(!rankLoaded) return `<div class="empty"><b>Working it out\u2026</b>
    Counting rounds across the fleet for the last 24 hours.</div>`;
  if(ranking && ranking.error) return `<div class="empty">
    <b>Could not load the ranking.</b> ${esc(ranking.error)}</div>`;
  return `
    <div class="note" style="margin-bottom:10px">Ordered by rounds in the last
      24 hours. The network assigns work, so counts vary for reasons that are
      nobody's fault \u2014 a host mid-table is not underperforming. What is
      worth a look is an outlier, or a node whose failures climb while its
      rounds fall.</div>
    <h3 style="margin:14px 0 6px">Nodes \u00b7 cMix rounds</h3>
    <div class="note" style="margin-bottom:6px">Last column: recovered
      failures in the last 24 hours \u2014 counted over the window, because
      cMix resets the raw counter every time it restarts. High there with
      mid-table rounds means that host is working harder for the same
      output, and nothing alerts on it.</div>
    ${rankList(ranking.nodes, "node", "recovered failures in 24h")}
    <h3 style="margin:18px 0 6px">Gateways \u00b7 rounds relayed</h3>
    <div class="note" style="margin-bottom:6px">Last column: watchdog restarts
      in the last 24 hours, not the lifetime total \u2014 a gateway restarted
      during a fault that is long fixed would otherwise read as broken
      forever. Any number here means automated recovery is holding something
      up that nobody has looked at.</div>
    ${rankList(ranking.gateways, "gateway", "watchdog restarts in 24h")}`;
}
