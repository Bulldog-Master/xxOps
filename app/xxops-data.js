let fixEditing=null;
/* Lifted out of xxops.html unchanged.
 *
 * A classic script rather than a module, on purpose: these stay
 * global, so every caller left in the page keeps working.
 */

/* ---------- prometheus ---------- */
// Both go through this origin now. A page served over https cannot reach
// http://host:9090 - the browser blocks it as mixed content - and routing
// through the backend also puts them behind the login.
const P=()=>"/prom";
async function q(expr,label){
  const r=await fetch(`${P()}/api/v1/query?query=${encodeURIComponent(expr)}`,{cache:"no-store"});
  if(!r.ok) throw new Error(r.status);
  const j=await r.json(),o={};
  for(const s of j.data.result) o[s.metric.instance]=label?s.metric[label]:parseFloat(s.value[1]);
  return o;
}
async function qRaw(expr){
  const r=await fetch(`${P()}/api/v1/query?query=${encodeURIComponent(expr)}`,{cache:"no-store"});
  if(!r.ok) throw new Error(r.status);
  return (await r.json()).data.result;
}

/* ---------- discovery: who is reporting, and what are they ---------- */
async function discover(){
  const [seen,roles]=await Promise.all([q("max by (instance) (up)"),q("substrate_node_roles")]);
  const all=Object.keys(seen).filter(h=>h&&!cfg.ignore.includes(h)&&!/^[\d.]+:\d+$/.test(h));
  const nodes=all.filter(h=>roles[h]===4).sort(), gws=all.filter(h=>roles[h]!==4).sort((a,b)=>a.length-b.length);
  const auto={},used=new Set();
  for(const n of nodes){
    const m=gws.find(g=>g!==n&&!used.has(g)&&g.startsWith(n));
    if(m){auto[n]=m;used.add(m)} else auto[n]="";
  }
  if(!cfg.pairs){ cfg.pairs=auto; saveCfg(); }
  // a host is only unpaired if it is missing from the pairing you saved —
  // not merely absent from this round of discovery, since a node that is
  // simply offline would otherwise orphan its gateway in the banner
  const known=new Set();
  for(const [n,g] of Object.entries(cfg.pairs||{})){ known.add(n); if(g) known.add(g); }
  discovered={all,nodes,gws,auto,
    unpaired:all.filter(h=>!known.has(h))};
  return discovered;
}

/* ---------- slow tier: specs, refreshed every 5 min ---------- */
async function loadSpecs(){
  try{
    const [cores,cpu,mt,ma,fs,fa,fd,un,os,dmi,boot,load,temp,gt,gu,gm,gx,gp,ga,gc,cpumodel,lsu,lsd,lsa,lsp]=await Promise.all([
      q('count by (instance) (node_cpu_seconds_total{mode="idle"})'),
      q('100 - (avg by (instance) (rate(node_cpu_seconds_total{mode="idle"}[5m])) * 100)'),
      q("node_memory_MemTotal_bytes"), q("node_memory_MemAvailable_bytes"),
      q('max by (instance) (node_filesystem_size_bytes{mountpoint="/"})'),
      q('max by (instance) (node_filesystem_avail_bytes{mountpoint="/"})'),
      q('deriv(node_filesystem_avail_bytes{mountpoint="/"}[7d])'),
      q("node_uname_info","release"), q("node_os_info","pretty_name"),
      qRaw("node_dmi_info"), q("node_boot_time_seconds"), q("node_load1"),
      q("max by (instance) (node_hwmon_temp_celsius)"),
      q("xx_gpu_temp_celsius"), q("xx_gpu_utilization_percent"),
      q("xx_gpu_memory_used_bytes"), q("xx_gpu_memory_total_bytes"),
      q("xx_gpu_power_watts"), q("xx_gpu_compute_attached"), q("xx_gpu_compute_memory_bytes"),
      // node_exporter has no cpu-model metric of any kind, so this one
      // comes from the producer. Nodes only; absent on gateways.
      q("xx_cpu_info","model"),
      // qRaw, not q: these carry a mode label so there are two series
      // per host and q would collapse them to one.
      qRaw("xx_linkspeed_up_mbps"), qRaw("xx_linkspeed_down_mbps"),
      qRaw("xx_linkspeed_age_seconds"), qRaw("xx_linkspeed_path_direct")
    ]);
    const board={},model=await qRaw("xx_gpu_present");
    for(const s of dmi) board[s.metric.instance]=s.metric;
    const gname={};
    for(const s of model) if(parseFloat(s.value[1])===1) gname[s.metric.instance]=s.metric.model;
    // instance -> mode -> {up,down,age,direct}. Absent for gateways
    // and for a node that has not run a test yet, which is not a fault.
    const ls={};
    const lsPut=(arr,key)=>{ for(const s of arr||[]){
      const i=s.metric.instance, m=s.metric.mode; if(!i||!m) continue;
      ls[i]=ls[i]||{}; ls[i][m]=ls[i][m]||{};
      ls[i][m][key]=parseFloat(s.value[1]);
    } };
    lsPut(lsu,"up"); lsPut(lsd,"down");
    lsPut(lsa,"age"); lsPut(lsp,"direct");
    const out={};
    for(const h of Object.keys({...cores,...mt})){
      const d=fd[h], av=fa[h];
      out[h]={cores:cores[h],cpu:cpu[h],cpuModel:cpumodel[h],memTotal:mt[h],memAvail:ma[h],
        diskSize:fs[h],diskAvail:av,
        // slopes smaller than ~10 MB/day are noise, not a trend
        diskDays: (d!=null&&d<-120&&av!=null) ? av/(-d)/86400 : null,
        kernel:un[h],os:os[h],boot:boot[h],load:load[h],temp:temp[h],
        board:board[h]?.board_name,boardVendor:board[h]?.board_vendor,
        bios:board[h]?.bios_version,biosDate:board[h]?.bios_date,
        link: ls[h]||null,
        gpu: gname[h] ? {model:gname[h],temp:gt[h],util:gu[h],memUsed:gm[h],memTotal:gx[h],power:gp[h],attached:ga[h],cmixMem:gc[h]} : null};
    }
    specs=out; lastSpec=Date.now();
  }catch(e){/* keep previous specs */}
}

/* ---------- fast tier ---------- */
async function load(){
  try{
    if(!discovered) await discover();
    const [round,secs,fails,errf,up,height,gwsvc,ver,authored,roles,gwround,gwwarn,logs,
           chg30,gwchg30]=await Promise.all([
      q("xx_cmix_last_round"), q("xx_cmix_last_round_seconds"),
      q("xx_cmix_recoverable_failures"), q("xx_cmix_err_file_present"),
      q("max by (instance) (up)"), q('substrate_block_height{status="best"}'),
      q('xx_service_up{service="xxnetwork-gateway"}'), q("substrate_build_info","version"),
      q("substrate_proposer_block_constructed_count"), q("substrate_node_roles"),
      q("xx_gateway_last_round"), q("xx_gateway_warn_lines"),
      qRaw("xx_log_bytes"),
      // 0 means the round number has not moved for the whole window.
      // Same expression NodeNotInRounds uses, so the app and the alerts
      // agree instead of measuring different things.
      q("changes(xx_cmix_last_round[30m])"),
      q("changes(xx_gateway_last_round[30m])")
    ]);
    demo=false; promDown=false; everLoaded=true; lastGood=Date.now()/1000;
    if(Date.now()-lastSpec>300000) loadSpecs();
    const bl={};
    for(const s of logs){
      const i=s.metric.instance,v=parseFloat(s.value[1]);
      bl[i]=bl[i]||{total:0,top:"",topv:0}; bl[i].total+=v;
      if(v>bl[i].topv){bl[i].topv=v;bl[i].top=s.metric.file}
    }
    return build({round,secs,fails,errf,up,height,gwsvc,ver,authored,roles,gwround,gwwarn,bl,
                  chg30,gwchg30});
  }catch(e){
    // Never seen the fleet: this is someone trying xxOps out, and demo data
    // is what it is for.
    if(!everLoaded){ demo=true; return fake(); }
    // Seen it before, cannot see it now. Keep what was true and say loudly
    // that it is not current - do not invent, and do not blank the page,
    // because an empty fleet reads as calm.
    promDown = true;
    return rows;
  }
}

/* Whether the fleet has ever been seen.
 *
 * demo data is for someone who has not connected Prometheus yet. Once real
 * hosts have been loaded once, a later failure is an OUTAGE, and inventing a
 * healthy fleet to fill the gap is the worst thing the app could do with it.
 */
let everLoaded = false, promDown = false, lastGood = 0;

/* What each situation is called. Keys are the reason codes build() sets;
 * values are either a sentence or a function of the numbers that sentence
 * needs. Changing how something reads is a change HERE, not in the logic
 * that decided it - which matters because this wording ships to other
 * operators and is still being refined as we learn what the network
 * actually does.
 */
const REASONS = {
  "node-unreachable": "Host unreachable — no metrics arriving at all.",
  "no-rounds-reported": "The host is reporting but cMix is not producing rounds at all — no round number is arriving, so cMix is not running even if its service looks active. Check the wrapper log, and check the validator in the xx network wallet: a chilled or slashed validator never reaches the ready state the wrapper waits for.",
  "cmix-stuck":       "cMix is stuck — rounds stopped and error file present.",
  "not-in-rounds":    "Up but not in rounds — cMix is running and the chain role is right, but no rounds are landing. Usually the waiting state: check the validator in the xx network wallet. If you have already re-validated it rejoins at the next era.",
  "gw-unreachable":   "Gateway host unreachable — rounds are still landing, but they will stop until it is back.",
  "gw-stalled":       v => `Gateway stopped processing — no gossip round for ${v.gwIdle}s.`,
  "round-failure":    "cMix hit a round failure — recovering, rounds still advancing.",
  "gw-service-off":   "Gateway service is not running.",
  "chain-lag":        v => `Chain is ${v.lag} blocks behind the fleet.`,
  "peer-failure":     "Peer round failure — cMix recovered, rounds still advancing.",
};

function reasonText(code, v){
  if(!code) return "";
  const r = REASONS[code];
  // An unknown code is a bug, but a validator row is the wrong place to
  // discover it - say nothing rather than render "undefined" at someone.
  if(r == null){ console.warn("no wording for reason", code); return ""; }
  return typeof r === "function" ? r(v || {}) : r;
}

function build(m){
  const now=Date.now()/1000, tip=Math.max(...Object.values(m.height||{}).filter(Number.isFinite),0);
  const pairs=cfg.pairs||{};
  hosts=[];
  const seen=new Set();
  const addHost=(h,role,val)=>{
    if(!h||seen.has(h)) return; seen.add(h);
    const sp=specs[h]||{};
    hosts.push({host:h,role,validator:val,up:m.up[h]===1,version:m.ver[h],height:m.height[h],
      logs:m.bl[h]?.total??null,topLog:m.bl[h]?.top??"",...sp,
      disk: sp.diskSize&&sp.diskAvail!=null ? (1-sp.diskAvail/sp.diskSize)*100 : null});
  };
  for(const [n,g] of Object.entries(pairs)){ addHost(n,"node",n); addHost(g,"gateway",n); }
  for(const h of Object.keys(m.up)) if(!seen.has(h)&&!cfg.ignore.includes(h)&&!/^[\d.]+:\d+$/.test(h))
    addHost(h,m.roles[h]===4?"node":"gateway","—");

  return Object.entries(pairs).map(([node,gw])=>{
    const nodeUp=m.up[node]===1, gwUp=gw?m.up[gw]===1:true, gwOn=gw?m.gwsvc[gw]===1:true;
    const gwr = gw ? (m.gwround?.[gw] ?? null) : null;
    const gwLast = prev[node];
    const gwMoved = (gwLast===undefined || gwLast.gwr==null || gwr==null) ? true : gwr>gwLast.gwr;
    const gwStill = (gwLast && !gwMoved) ? (gwLast.gwStill ?? now) : now;
    const gwFrozen30 = gw ? m.gwchg30?.[gw] === 0 : false;
    const gwStalled = (gwr!==null && (now-gwStill)>cfg.stall) || gwFrozen30;
    const round=m.round[node]??null, last=prev[node];
    const moved = last===undefined ? true : round>last.round;
    const still = last&&!moved ? (last.still??now) : now;
    // session memory starts empty on every load, so it cannot see a stall
    // that began before the page opened - Prometheus can. Either counts.
    const frozen30 = m.chg30?.[node] === 0;
    const stalled = (round!==null && (now-still)>cfg.stall) || frozen30;
    const err=m.errf[node]===1, h=m.height[node]??null, lag=h&&tip?Math.round(tip-h):0;
    const auth=m.authored[node]??null;
    const authStalled = last&&auth!=null&&last.auth===auth ? (last.authStill??now) : now;

    // The branch decides WHICH situation this is. What to CALL it lives in
    // REASONS, so wording can change without touching classification.
    let state="steady",reason=null;
    if(!nodeUp){state="down";reason="node-unreachable"}
    else if(round===null){state="offline";reason="no-rounds-reported"}
    else if(err&&stalled){state="offline";reason="cmix-stuck"}
    else if(stalled){state="offline";reason="not-in-rounds"}
    else if(gw&&!gwUp){state="recovering";reason="gw-unreachable"}
    else if(gwStalled){state="recovering";reason="gw-stalled"}
    else if(err){state="recovering";reason="round-failure"}
    else if(gw&&!gwOn){state="recovering";reason="gw-service-off"}
    else if(lag>cfg.lag){state="recovering";reason="chain-lag"}
    else if(last&&m.fails[node]>last.fails){state="recovering";reason="peer-failure"}
    const why = reasonText(reason, {gwIdle: Math.round(now-gwStill), lag});

    prev[node]={round,fails:m.fails[node]??0,still,auth,authStill:authStalled,gwr,gwStill,
                spark:((last?.spark)||[]).concat(m.secs[node]??0).slice(-9)};
    const sl=mutedFor(node);
    return {node,gw,state,reason,why,round,muted:sl?sl.id:null,mutedTo:sl?sl.endsAt:null,mutedWhat:mutedScope(sl),gwRound:gwr,gwWarn:m.gwwarn?.[gw],gwUp:gwUp&&gwOn,height:h,lag,secs:m.secs[node],
      spark:prev[node].spark,moved,fails:m.fails[node]??0,authored:auth,
      authIdle: auth!=null ? now-authStalled : null,
      nodeH:hosts.find(x=>x.host===node), gwH:hosts.find(x=>x.host===gw)};
  });
}

function fake(){
  const base=445822900,tip=24423140,names=["alpha","bravo","charlie","delta","echo","foxtrot"];
  hosts=[];discovered=discovered||{all:[],nodes:[],gws:[],auto:{},unpaired:[]};
  const pairs=cfg.pairs&&Object.keys(cfg.pairs).length?cfg.pairs:
    Object.fromEntries(names.map(n=>[n,n+"_gt"]));
  return Object.entries(pairs).map(([node,gw],i)=>{
    for(const [h,role] of [[node,"node"],[gw,"gateway"]])
      hosts.push({host:h,role,validator:node,up:true,disk:35+(i*7)%50,
        logs:(role==="gateway"?1.1e9:4e7)+i*3e7,topLog:role==="gateway"?"gateway.log":"cmix.log",
        version:"0.2.6-617b35d134c",height:tip-(i%3),cores:16,cpu:22+i,
        memTotal:6.7e10,memAvail:4.9e10,diskSize:9.8e11,diskAvail:3e11,diskDays:40+i*9,
        kernel:"5.15.0-186-generic",os:"Ubuntu 20.04.6 LTS",boot:Date.now()/1000-86400*(3+i),
        load:1.4,temp:52,board:"TUF GAMING B550M-PLUS",boardVendor:"ASUSTeK COMPUTER INC.",
        bios:"0321",biosDate:"05/13/2020",
        gpu:role==="node"?{model:i%2?"NVIDIA GeForce RTX 3080 Ti":"NVIDIA GeForce RTX 2080 SUPER",
          temp:60+i,util:i%3?34:0,memUsed:2.3e9,memTotal:1.28e10,power:104}:null});
    let state="steady",why="",lag=i%3;
    if(i===1){state="recovering";why="cMix hit a round failure — recovering, rounds still advancing."}
    const spark=[...Array(9)].map((_,k)=>9+((i*3+k*5)%14));
    return {node,gw,state,why,round:base+i*41+Math.floor(Date.now()/9000),gwUp:true,height:tip-lag,lag,
      secs:spark[8],spark,moved:true,fails:6+i%9,authored:500+i*13,authIdle:600,
      nodeH:hosts.find(x=>x.host===node),gwH:hosts.find(x=>x.host===gw)};
  });
}

/* ---------- incidents ---------- */
const RANGES={"24h":[86400,300],"7d":[604800,1800],"30d":[2592000,7200]};
async function loadFixes(){
  try{
    const r=await fetch("/api/resolutions",{cache:"no-store"});
    const d=await r.json();
    fixes = d.ok ? (d.entries||[]) : [];
  }catch(e){ fixes=[]; }
  fixLoaded=true; render();
}

function vFixes(){
  const rows = fixes.map(e=>{
    const open = fixOpen===e.id;
    const when = new Date((e.created||0)*1000)
      .toLocaleDateString([],{month:"short",day:"numeric",year:"numeric"});
    return `<div class="card fixcard">
      <div class="fixhead" data-fixopen="${esc(e.id)}">
        <div>
          <div class="fixtitle">${esc(e.title||"untitled")}</div>
          <div class="fixmeta">${e.alertname?`<span class="chip">${esc(e.alertname)}</span>`:""}
            ${e.host?`<span class="chip">${esc(e.host)}</span>`:""}
            ${(e.tags||[]).map(t=>`<span class="chip">${esc(t)}</span>`).join("")}
            <span class="fixdate">${when}</span></div>
        </div>
        <span class="chev">${open?"\u2013":"+"}</span>
      </div>
      ${open?`<div class="fixbody">
        ${e.symptom?`<h4>What it looked like</h4><p>${esc(e.symptom)}</p>`:""}
        ${e.diagnosis?`<h4>What it turned out to be</h4><p>${esc(e.diagnosis)}</p>`:""}
        ${e.fix?`<h4>What fixed it</h4><p>${esc(e.fix)}</p>`:""}
        <button class="chip" data-fixedit="${esc(e.id)}" style="cursor:pointer">Edit</button>
        <button class="chip" data-fixdel="${esc(e.id)}" style="cursor:pointer;border-color:var(--mute)">Delete</button>
      </div>`:""}
    </div>`;}).join("");

  // Editing reuses this same form rather than a second one, so there is a
  // single place where a fix is written. fixEditing holds the id being
  // changed, or null when recording a new one.
  const ed = fixEditing ? (fixes.find(f=>f.id===fixEditing) || null) : null;
  const va = s => esc(String(s==null?"":s));
  const form = (fixAdding || ed) ? `<div class="card fixform">
      <input class="inp row2" id="fx-title" value="${ed?va(ed.title):""}" placeholder="Short title, e.g. Gateway deaf to peers after a certificate expired">
      <div class="frow"><input class="inp row2" id="fx-host" value="${ed?va(ed.host):""}" placeholder="host, optional">
      <input class="inp row2" id="fx-alert" value="${ed?va(ed.alertname):""}" placeholder="alert name, optional"></div>
      <textarea class="inp fxta" id="fx-symptom" placeholder="What it looked like - the symptom you actually saw">${ed?va(ed.symptom):""}</textarea>
      <textarea class="inp fxta" id="fx-diag" placeholder="What it turned out to be - the real cause">${ed?va(ed.diagnosis):""}</textarea>
      <textarea class="inp fxta" id="fx-fix" placeholder="What fixed it - and what did not">${ed?va(ed.fix):""}</textarea>
      <div class="frow"><button class="chip" id="fx-save" style="cursor:pointer">${ed?"Save changes":"Save"}</button>
      <button class="chip" id="fx-cancel" style="cursor:pointer">Cancel</button></div>${ed&&ed.bundled?`<div class="note">This one ships with xxOps. Saving your own version means you stop receiving updates to it - your copy wins from now on.</div>`:""}
    </div>` : `<button class="chip" id="fx-new" style="cursor:pointer">Record a fix</button>`;

  return `<div class="lead">What has broken before, and what actually fixed it.
    Worth writing one the moment you solve something - it is the part nobody
    remembers six months later.</div>
    ${form}
    ${fixes.length?rows:`<div class="card"><div class="muted">Nothing recorded yet.</div></div>`}`;
}

async function saveFix(){
  const g=id=>{const el=document.getElementById(id);return el?el.value.trim():"";};
  const body={title:g("fx-title"),host:g("fx-host"),alertname:g("fx-alert"),
              symptom:g("fx-symptom"),diagnosis:g("fx-diag"),fix:g("fx-fix")};
  if(!body.title){ alert("Give it a title at least."); return; }
  if(fixEditing) body.id = fixEditing;
  try{
    const r=await fetch(fixEditing ? "/api/resolutions/edit"
                                   : "/api/resolutions/add",{method:"POST",
      headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
    const d=await r.json();
    if(!d.ok) throw new Error(d.message||"the server refused");
    fixAdding=false; fixEditing=null; await loadFixes();
  }catch(e){ alert("Could not save: "+e.message); }
}

async function deleteFix(id){
  try{
    await fetch("/api/resolutions/delete",{method:"POST",
      headers:{"Content-Type":"application/json"},body:JSON.stringify({id})});
    await loadFixes();
  }catch(e){ alert("Could not delete: "+e.message); }
}

async function loadIncidents(){
  const [span,step]=RANGES[incRange],end=Math.floor(Date.now()/1000),start=end-span;
  const u=e=>`${P()}/api/v1/query_range?query=${encodeURIComponent(e)}&start=${start}&end=${end}&step=${step}`;
  try{
    const [a,b]=await Promise.all([
      fetch(u("max by (instance) (up)"),{cache:"no-store"}).then(r=>r.json()),
      fetch(u("xx_cmix_err_file_present"),{cache:"no-store"}).then(r=>r.json())]);
    const out=[];
    const walk=(res,test,kind,sev)=>{for(const s of res.data.result){let run=null;
      for(const [t,v] of s.values){ if(test(parseFloat(v))){run?run.to=+t:run={from:+t,to:+t}}
        else if(run){out.push({inst:s.metric.instance,kind,sev,...run});run=null} }
      if(run) out.push({inst:s.metric.instance,kind,sev,...run,open:true});}};
    walk(a,v=>v===0,"Host unreachable","down");
    walk(b,v=>v===1,"cMix round failure","rec");
    incidents=out.sort((x,y)=>y.from-x.from);
  }catch(e){
    const n=Math.floor(Date.now()/1000);
    incidents=[["alpha","cMix round failure","rec",n-3600,n-3540],
               ["bravo","cMix round failure","rec",n-9000,n-8940]]
      .map(([inst,kind,sev,from,to])=>({inst,kind,sev,from,to}));
  }
  render();
}

/* ---------- what changed ---------- */
async function loadChanges(){
  const [span,step]=RANGES[chgRange], end=Math.floor(Date.now()/1000), start=end-span;
  const u=e=>`${P()}/api/v1/query_range?query=${encodeURIComponent(e)}&start=${start}&end=${end}&step=${step}`;
  const grab=async e=>{ try{ const r=await fetch(u(e),{cache:"no-store"});
    return r.ok ? ((await r.json()).data.result||[]) : []; }catch(x){ return []; } };
  const out=[];
  const walk=(res,fn)=>{ for(const s of res){ let prev=null;
    for(const [t,v] of s.values){ const n=parseFloat(v);
      if(prev!==null && n!==prev){ const e=fn(s.metric,prev,n); if(e) out.push({t:+t,...e}); }
      prev=n; } } };

  walk(await grab("node_boot_time_seconds"),
    (m,a,b)=> b>a+300 ? {host:m.instance, kind:"boot", what:"rebooted"} : null);
  walk(await grab("xx_gateway_watchdog_restarts_total"),
    (m,a,b)=> b>a ? {host:m.instance, kind:"wd", what:"gateway restarted by the watchdog"} : null);
  walk(await grab("xx_logrotate_rule_present"),
    (m,a,b)=> ({host:m.instance, kind:"lr",
                what: b ? "log rotation configured" : "log rotation removed"}));
  walk(await grab('xx_service_up'),
    (m,a,b)=> ({host:m.instance, kind:"svc",
                what: (b?"started ":"stopped ") + (m.service||"a service")}));

  // the chain build lives in a label, so a change means one series ends and another begins
  const seen={};
  for(const s of await grab("substrate_build_info")){
    const i=s.metric.instance, v=s.metric.version;
    if(!i||!v||!s.values.length) continue;
    (seen[i]=seen[i]||[]).push({v, first:+s.values[0][0]});
  }
  for(const [i,list] of Object.entries(seen)){
    list.sort((a,b)=>a.first-b.first);
    for(let k=1;k<list.length;k++)
      out.push({t:list[k].first, host:i, kind:"ver",
        what:`chain build ${list[k-1].v.split("-")[0]} \u2192 ${list[k].v.split("-")[0]}`});
  }

  // pyOpenSSL lives in a label too, exactly like the chain build above
  const pyseen={};
  for(const s of await grab("xx_pyopenssl_version_info")){
    const i=s.metric.instance, v=s.metric.version;
    if(!i||!v||!s.values.length) continue;
    (pyseen[i]=pyseen[i]||[]).push({v, first:+s.values[0][0]});
  }
  for(const [i,list] of Object.entries(pyseen)){
    list.sort((a,b)=>a.first-b.first);
    for(let k=1;k<list.length;k++)
      out.push({t:list[k].first, host:i, kind:"ver",
        what:`pyOpenSSL ${list[k-1].v} \u2192 ${list[k].v}`});
  }

  changes=out.sort((a,b)=>b.t-a.t).slice(0,300);
  chgLoaded=true;
  head(); render();
}


/* ---------- ranking ----------
 *
 * Ordered by the work each host actually does: cMix rounds for nodes, gossip
 * rounds for gateways. Ranking them together would put every gateway at zero,
 * since a gateway never processes a cMix round.
 *
 * Loaded when the tab is first opened, not on the 30s tick - these are range
 * queries over 24 hours across the whole fleet, and a ranking that reorders
 * itself while you read it would be worse than one that holds still.
 */
async function loadRanking(){
  try{
    const [nr, nf, gr, gw] = await Promise.all([
      q("changes(xx_cmix_last_round[24h])"),
      // increase(), not the raw counters. cMix RESETS its failure count on
      // restart, so the bare value means "since this process last crashed";
      // and the watchdog total is LIFETIME, so a gateway restarted during a
      // fault that is long fixed carries that scar forever. Both made the
      // column point at the wrong host.
      q("increase(xx_cmix_recoverable_failures[24h])"),
      q("changes(xx_gateway_local_round[24h])"),
      q("increase(xx_gateway_watchdog_restarts_total[24h])"),
    ]);
    const mk = (main, extra) => Object.entries(main)
      .map(([host, v]) => ({host, v: Math.round(v), extra: extra[host]}))
      .sort((a, b) => b.v - a.v);
    ranking = {nodes: mk(nr, nf), gateways: mk(gr, gw)};
  }catch(e){
    ranking = {error: e && e.message ? e.message : String(e)};
  }
  rankLoaded = true;
  render();
}
