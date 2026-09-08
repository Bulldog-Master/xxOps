/* Lifted out of xxops.html unchanged.
 *
 * A classic script rather than a module, on purpose: these stay
 * global, so every caller left in the page keeps working.
 */

/* ---------- settings ---------- */
document.getElementById("gear").onclick=async()=>{ if(!notify) await loadNotifyCfg(); openSettings(); };
/* ---------- settings sections, one open at a time ---------- */
let grpOpen = null;   // the heading text of the open section, or null

function grpHead(g){
  const h = g.firstElementChild;
  return (h && h.tagName === "H3") ? h : null;
}

function applyGrpCollapse(){
  const d = document.getElementById("drawer");
  if(!d) return;
  /* every one of these writes to an owner-only endpoint, so a contact
     seeing them can only be confused by them */
  const OWNER_ONLY = ["Profile","Validators","Contacts","Alertmanager",
                      "Thresholds","Muted","Data source"];
  const owner = !acct || acct.role === "owner";
  d.querySelectorAll(".grp").forEach(g=>{
    const h = grpHead(g);
    if(!h) return;                       // not a section we can collapse
    const title = h.textContent.trim();
    g.hidden = !owner && OWNER_ONLY.indexOf(title) >= 0;
    const open = title === grpOpen;
    g.classList.toggle("closed", !open);
    h.setAttribute("aria-expanded", open ? "true" : "false");
  });
  /* nothing left for it to save */
  const bar = d.querySelector(".savebar");
  if(bar) bar.hidden = !owner;
}

document.addEventListener("click", e=>{
  const h = e.target.closest("h3");
  if(!h) return;
  const g = h.parentElement;
  if(!g || !g.classList.contains("grp") || g.firstElementChild !== h) return;
  const d = document.getElementById("drawer");
  if(!d || !d.contains(h)) return;
  const t = h.textContent.trim();
  grpOpen = (grpOpen === t) ? null : t;   // tapping the open one closes it
  applyGrpCollapse();
});

{
  /* openSettings rebuilds the drawer's innerHTML and is called from several
     places, so watching the drawer is more reliable than hooking one of them */
  const d = document.getElementById("drawer");
  if(d && window.MutationObserver){
    let queued = false;
    new MutationObserver(()=>{
      if(queued) return;
      queued = true;
      requestAnimationFrame(()=>{ queued = false; applyGrpCollapse(); });
    }).observe(d, {childList:true, subtree:true});
  }
}

function paintSettings(){ openSettings(notifyOpen?"notify":null); }
function openSettings(focus){
  const d=document.getElementById("drawer");
  const pairs=Object.entries(cfg.pairs||{});
  const freeGw=(discovered?.all||[]).filter(h=>!Object.keys(cfg.pairs||{}).includes(h));
  d.innerHTML=`<div class="scrim" id="scrim"></div><aside class="drawer">
    <div class="dh"><button class="ib" id="closeS" aria-label="Close">✕</button><h2>Settings</h2></div>

    <div class="grp"><h3>Profile</h3>
      <div class="fld"><label for="nm">Display name</label><input class="inp" id="nm" value="${esc(cfg.name)}"></div>
      <div class="fld"><label>Profile picture</label>
        <div style="display:flex;gap:10px;align-items:center">
          <div class="av" id="avPrev" style="width:44px;height:44px;font-size:15px"></div>
          <input type="file" id="avFile" accept="image/*" hidden>
          <button class="btn ghost" id="avPick" style="height:34px">Choose image</button>
          <button class="btn ghost" id="avIcon" style="height:34px">Use pattern</button>
          <button class="btn ghost" id="avClear" style="height:34px">Remove</button>
        </div></div></div>

    ${acctPanel()}

    <div class="grp" id="hosts"><h3>Validators</h3>
      <button class="acc" id="valToggle" aria-expanded="${valOpen}">
        <div><b>${pairs.length}</b> validators paired
          <small>${valOpen?"Tap to close":"Tap to pair gateways, add or remove"}</small></div>
        <i>&#9656;</i></button>
      ${!valOpen?"":`
      <div class="note" style="margin-bottom:10px">Hosts are found automatically — anything reporting to
        Prometheus shows up here. Pair each node with its gateway, or remove what you don't want to watch.</div>
      ${pairs.length?pairs.map(([n,g])=>`<div class="prow">
        ${identicon(n,26)}
        <div class="pn">${esc(n)}<small>${esc(g||"no gateway")}</small></div>
        <select class="inp" data-pair="${esc(n)}" style="flex:0 1 150px;height:32px">
          <option value="">no gateway</option>
          ${[...new Set([g,...freeGw].filter(Boolean))].map(o=>
            `<option value="${esc(o)}" ${o===g?"selected":""}>${esc(o)}</option>`).join("")}
        </select>
        <button class="x" data-del="${esc(n)}" title="Stop watching">✕</button></div>`).join("")
      :`<div class="note">Nothing set up yet. Add a validator below.</div>`}
      <div class="tools" style="margin:10px 0 0">
        <input class="inp" id="newN" placeholder="node name">
        <input class="inp" id="newG" placeholder="gateway name">
        <button class="btn" id="addP">Add</button></div>
      ${discovered?`<div class="note" style="margin-top:10px">Seen reporting: ${discovered.all.length} hosts
        (${discovered.nodes.length} validating, ${discovered.gws.length} following).
        <button class="btn ghost" id="redisc" style="height:30px;margin-top:8px">Re-detect from Prometheus</button></div>`:""}
      `}
    </div>

    <div class="grp"><h3>Contacts</h3>
      ${!notify ? `<div class="note">The xxOps service isn't answering, so notification
        settings can't be loaded. Check that xxops-app is running.</div>` : `
      <button class="acc" id="notifyToggle" aria-expanded="${notifyOpen}">
        <div><b>${(notify.contacts||[]).length}</b> contacts
          <small>${notifyOpen?"Tap to close":"Tap to set up who is alerted about which validators"}</small></div>
        <i>&#9656;</i></button>
      ${!notifyOpen?"":`
        <div class="fld"><label for="btok">Telegram bot token</label>
          <input class="inp" id="btok" value="${esc(notify.telegram.bot_token||"")}"
            placeholder="from @BotFather"></div>

        <h3 style="margin-top:18px">People</h3>
        ${(notify.contacts||[]).map(c=>{
          const chans=[]; if(c.emails) chans.push("email"); if(c.telegram_chat_id) chans.push("telegram");
          if(c.webhook) chans.push("webhook");
          const nv=(c.validators||[]).length;
          return `<div class="prow" style="${contactOpen===c.id?"border-color:var(--brand)":""}">
            <div class="pn" data-contact="${esc(c.id)}" style="cursor:pointer">${esc(c.name||"New contact — tap to edit")}
              <small>${chans.join(", ")||"no channel yet"} · ${nv} validator${nv===1?"":"s"}</small></div>
            ${contactOpen===c.id?"":`<button class="btn ghost" data-invite="${esc(c.id)}"
              style="height:30px;font-size:12px;white-space:nowrap;margin-right:6px">Invite</button>`}
            <button class="x" data-delc="${esc(c.id)}" title="Remove">&#10005;</button></div>
          ${contactOpen===c.id||!inviteState||inviteState.contactId!==c.id?"":
            inviteState.pending?`<div class="note">Making a code\u2026</div>`:
            inviteState.error?`<div class="note amsg bad">${esc(inviteState.error)}</div>`:
            `<div class="code">${esc(inviteState.code)}</div>
             <div class="note">${inviteState.sent
               ? "Sent to their Telegram."
               : "No Telegram paired for them, so pass this on yourself."}
               It works once and expires in 24 hours. They open <b>/redeem</b>,
               enter the code, and choose their own username and password.</div>`}
          ${contactOpen!==c.id?"":`<div class="exp" style="border-radius:0 0 var(--r) var(--r);
              grid-template-columns:1fr;margin:-8px 0 8px">
            <div class="fld"><label>Name</label>
              <input class="inp" data-f="name" data-c="${esc(c.id)}" value="${esc(c.name||"")}"></div>
            <div class="fld"><label>Email addresses (separate with commas)</label>
              <input class="inp" data-f="emails" data-c="${esc(c.id)}" value="${esc(c.emails||"")}"
                placeholder="owner@example.com, ops@example.com"></div>
            <div class="fld"><label>Telegram</label>
              <div class="row2">
                <input class="inp" data-f="telegram_chat_id" data-c="${esc(c.id)}"
                  value="${esc(c.telegram_chat_id||"")}" placeholder="chat ID">
                <button class="btn ghost" id="pairBtn" style="height:38px;white-space:nowrap">Pair</button>
              </div></div>
            ${pairState?`<div class="code">${esc(pairState.code)}</div>
              <div class="note">Ask them to send exactly that to your bot. It will appear here
                on its own — they never need to find a chat ID.</div>`:""}
            <div class="fld"><label>App access</label>
              <button class="btn ghost" data-invite="${esc(c.id)}"
                style="height:38px;white-space:nowrap">Invite to the app</button></div>
            ${!inviteState||inviteState.contactId!==c.id?"":
              inviteState.pending?`<div class="note">Making a code\u2026</div>`:
              inviteState.error?`<div class="note amsg bad">${esc(inviteState.error)}</div>`:
              `<div class="code">${esc(inviteState.code)}</div>
               <div class="note">${inviteState.sent
                 ? "Sent to their Telegram."
                 : "No Telegram paired for them, so pass this on yourself."}
                 It works once and expires in 24 hours. They open <b>/redeem</b>,
                 enter the code, and choose their own username and password.</div>`}
            <div class="fld"><label>Webhook (optional)</label>
              <input class="inp" data-f="webhook" data-c="${esc(c.id)}" value="${esc(c.webhook||"")}"
                placeholder="https://…"></div>
            <div class="fld"><label>Alerts for these validators</label>
              <div class="vpick">${Object.keys(cfg.pairs||{}).sort().map(v=>
                `<label><input type="checkbox" data-v="${esc(v)}" data-c="${esc(c.id)}"
                  ${(c.validators||[]).includes(v)?"checked":""}>${esc(v)}</label>`).join("")}</div></div>
          </div>`}`;
        }).join("")}
        <button class="btn ghost" id="addContact" style="margin-top:6px">Add a contact</button>
        ${(notify.contacts||[]).length?"":`<div class="note">Add someone here first.
          A contact is who gets told when their validators need attention - and once
          they exist, you can invite them to sign in to the app as well.</div>`}

        <h3 style="margin-top:20px">Mail server</h3>
        <div class="note" style="margin-bottom:8px">Needed only for email. Paste the SMTP details
          from your provider once.</div>
        <div class="fld"><label>Host and port</label>
          <div class="row2">
            <input class="inp" id="smtph" value="${esc(notify.smtp.host||"")}" placeholder="smtp.resend.com">
            <input class="inp" id="smtpp" type="number" style="flex:0 0 90px"
              value="${notify.smtp.port||587}"></div></div>
        <div class="fld"><label>From address</label>
          <input class="inp" id="smtpf" value="${esc(notify.smtp.from||"")}" placeholder="alerts@yourdomain"></div>
        <div class="fld"><label>SMTP username</label>
          <input class="inp" id="smtpu" value="${esc(notify.smtp.username||"")}"></div>
        <div class="fld"><label>SMTP password</label>
          <input class="inp" id="smtpw" type="password" value="${esc(notify.smtp.password||"")}"></div>

        <h3 style="margin-top:20px">Anything unassigned</h3>
        <div class="note" style="margin-bottom:8px">Where alerts go for validators no contact covers.</div>
        <div class="fld"><label>Your Telegram chat ID</label>
          <input class="inp" id="fbtg" value="${esc(notify.fallback.telegram_chat_id||"")}"></div>
        <div class="fld"><label>Your email addresses</label>
          <input class="inp" id="fbem" value="${esc(notify.fallback.emails||"")}"></div>

        ${notifyMsg?`<div class="msg ${notifyMsg.indexOf("\u2713")===0?"ok":"bad"}">${esc(notifyMsg)}</div>`:""}
        <div style="display:flex;gap:8px;margin-top:12px">
          <button class="btn ghost" id="testNotify" style="flex:1">Send a test alert</button></div>
        <div class="note" style="margin-top:8px">Changes here are applied by
          <b>Save settings</b> at the bottom of this panel.</div>
      `}`}
    </div>

    <div class="grp"><h3>Alertmanager</h3>
      ${!amInfo ? `<div class="note">Alertmanager isn't reachable at
          <b>${esc(AM().replace(/^https?:\/\//,""))}</b>, so nothing can be sent.
          Install it on the monitor and this panel fills in by itself.</div>`
      : (()=>{
          const rec=amInfo.receivers||{}, names=Object.keys(rec);
          const live=names.filter(n=>rec[n].length);
          const label={telegram:"Telegram",email:"Email",webhook:"Webhook",
                       pagerduty:"PagerDuty",slack:"Slack",opsgenie:"Opsgenie",
                       victorops:"VictorOps",pushover:"Pushover",wechat:"WeChat",sns:"SNS",msteams:"Teams"};
          const amber=(amInfo.routes||[]).find(r=>r[0]==="amber");
          const amberQuiet = amber && !(rec[amber[1]]||[]).length;
          return `
      <div class="opt"><div>Alertmanager<small>${esc(amInfo.version||"")} · connected</small></div>
        <span class="dot d1" style="width:10px;height:10px"></span></div>
      ${live.length ? live.map(n=>`<div class="opt">
          <div>${rec[n].map(k=>esc(label[k]||k)).join(", ")}
            <small>receiver &ldquo;${esc(n)}&rdquo;${n===amInfo.defaultReceiver?" · default":""}</small></div>
          <span class="tag ok">on</span></div>`).join("")
        : `<div class="note">Alertmanager is running but no receiver has a channel configured,
             so alerts have nowhere to go.</div>`}
      ${names.filter(n=>!rec[n].length).length?`<div class="opt">
          <div>Silent receivers<small>${names.filter(n=>!rec[n].length).map(esc).join(", ")}</small></div>
          <span class="tag">quiet</span></div>`:""}
      <div class="note">
        ${amberQuiet?"Amber is routed to a silent receiver, so warnings never reach you — only a stopped node, a silent gateway or an unreachable host does."
                    :"<b>Warning:</b> amber alerts are not routed away. Self-healing round failures may page you."}
        <br><br>Channels are configured in <b>/etc/alertmanager/alertmanager.yml</b> on the monitor.
        In-app editing needs a small service alongside the app — that's the next build.
      </div>`;
        })()}
    </div>

    <div class="grp"><h3>Thresholds</h3>
      <div class="fld"><label for="st">Call cMix stopped after (seconds without a round)</label>
        <input class="inp" id="st" type="number" min="60" value="${cfg.stall}"></div>
      <div class="fld"><label for="dk">Flag disk above (%)</label>
        <input class="inp" id="dk" type="number" min="50" max="99" value="${cfg.disk}"></div>
      <div class="fld"><label for="lg">Flag logs above (GB)</label>
        <input class="inp" id="lg" type="number" min="0.5" step="0.5" value="${cfg.logGb}"></div>
      <div class="fld"><label for="lgg">Flag chain lag above (blocks)</label>
        <input class="inp" id="lgg" type="number" min="5" value="${cfg.lag}"></div>
      <div class="fld"><label for="dd">Warn when a disk will fill within (days)</label>
        <input class="inp" id="dd" type="number" min="1" value="${cfg.diskDays}"></div></div>

    <div class="grp"><h3>Muted</h3>
      ${!amUp?`<div class="note">Alertmanager isn't reachable, so muting is unavailable.
        Set it up and these controls appear on every validator.</div>`
      : silences.length?silences.map(sl=>`<div class="prow">
          <div class="pn">${esc((sl.matchers[0]||{}).value||"?")}
            <small>until ${new Date(sl.endsAt).toLocaleString()} · ${esc(sl.comment||"")}</small></div>
          <button class="x" data-unmute="${sl.id}" title="Unmute">✕</button></div>`).join("")
      : `<div class="note">Nothing is muted. Alerts for every validator will reach you.</div>`}
    </div>

    <div class="grp"><h3>Data source</h3>
      <div class="fld"><label for="pu">Prometheus address</label>
        <input class="inp" id="pu" value="${esc(cfg.prom)}"></div>
      <div class="fld"><label for="am">Alertmanager address (blank = same host, port 9093)</label>
        <input class="inp" id="am" value="${esc(cfg.alertmgr)}"></div></div>

    <div class="grp"><h3>Access</h3>
      <div class="note">Only devices on your tailnet can open xxOps. Sign-in with a password, passkey or 2FA
        arrives with the backend, and matters once other people need their own logins.</div></div>

    <div class="grp"><h3>About</h3>
      <div class="kv"><span>Version</span><em>${VERSION}</em></div>
      <div class="kv"><span>Watching</span><em>${pairs.length} validators</em></div></div>

    <div class="savebar">
    ${notifyMsg?`<div class="msg saveResult ${notifyMsg.indexOf("\u2713")===0?"ok":"bad"}">${esc(notifyMsg)}</div>`:""}
    <button class="btn wide" id="saveS">Save settings</button>
    </div></aside>`;

  const close=()=>d.innerHTML="";
  document.getElementById("scrim").onclick=close;
  document.getElementById("closeS").onclick=close;
  if(focus==="hosts") document.getElementById("hosts")?.scrollIntoView({block:"start"});
  if(focus==="notify") document.getElementById("notifyToggle")?.scrollIntoView({block:"start"});

  const prev=document.getElementById("avPrev");
  paintAvatar(prev);
  document.getElementById("nm").oninput=e=>{
    cfg.name=e.target.value.trim()||"Operator"; saveCfg();
    if(!cfg.avatar) prev.textContent=(e.target.value||"OP").slice(0,2).toUpperCase();
    document.getElementById("wn").textContent=cfg.name;
    if(!cfg.avatar) document.getElementById("av").textContent=cfg.name.slice(0,2).toUpperCase(); };
  document.getElementById("avPick").onclick=()=>document.getElementById("avFile").click();
  document.getElementById("avIcon").onclick=()=>{
    const nm=(document.getElementById("nm").value||cfg.name||"Operator");
    const svg=identicon(nm,128);
    cfg.avatar="data:image/svg+xml;base64,"+btoa(unescape(encodeURIComponent(svg)));
    saveCfg(); paintAvatar(prev); head(); };
  document.getElementById("avClear").onclick=()=>{
    cfg.avatar=null; saveCfg(); paintAvatar(prev); head(); };
  document.getElementById("avFile").onchange=e=>{
    const f=e.target.files&&e.target.files[0]; if(!f) return;
    const rd=new FileReader();
    rd.onload=()=>{ const img=new Image();
      img.onload=()=>{ const S=128,c=document.createElement("canvas");
        c.width=c.height=S; const g=c.getContext("2d");
        const m=Math.min(img.width,img.height);
        g.drawImage(img,(img.width-m)/2,(img.height-m)/2,m,m,0,0,S,S);
        cfg.avatar=c.toDataURL("image/jpeg",0.85);
        saveCfg(); paintAvatar(prev); head(); };
      img.src=rd.result; };
    rd.readAsDataURL(f); };

  d.querySelectorAll("[data-unmute]").forEach(b=>b.onclick=async()=>{
    await unmute(b.dataset.unmute); openSettings("hosts"); });
  const vt=document.getElementById("valToggle");
  if(vt) vt.onclick=()=>{ valOpen=!valOpen; openSettings(valOpen?"hosts":null); };

  const nt=document.getElementById("notifyToggle");
  if(nt) nt.onclick=()=>{ notifyOpen=!notifyOpen; notifyMsg=""; paintSettings(); };
  d.querySelectorAll("[data-contact]").forEach(b=>b.onclick=()=>{
    contactOpen = contactOpen===b.dataset.contact ? null : b.dataset.contact;
    pairState=null; clearInterval(pairTimer); paintSettings(); });
  d.querySelectorAll("[data-delc]").forEach(b=>b.onclick=()=>{
    notify.contacts = notify.contacts.filter(c=>c.id!==b.dataset.delc);
    if(contactOpen===b.dataset.delc) contactOpen=null; paintSettings(); });
  d.querySelectorAll("[data-f]").forEach(el=>el.oninput=()=>{
    const c=notify.contacts.find(x=>x.id===el.dataset.c);
    if(c) c[el.dataset.f]=el.value; });
  d.querySelectorAll(".vpick input").forEach(el=>el.onchange=()=>{
    const c=notify.contacts.find(x=>x.id===el.dataset.c); if(!c) return;
    c.validators = c.validators||[];
    if(el.checked){ if(!c.validators.includes(el.dataset.v)) c.validators.push(el.dataset.v); }
    else c.validators = c.validators.filter(v=>v!==el.dataset.v); });
  const ac=document.getElementById("addContact");
  if(ac) ac.onclick=()=>{
    const id = "c" + Date.now().toString(36);
    notify.contacts = notify.contacts||[];
    notify.contacts.push({id, name:"", emails:"", telegram_chat_id:"", webhook:"", validators:[]});
    contactOpen = id; paintSettings(); };
  const pb=document.getElementById("pairBtn"); if(pb) pb.onclick=startPair;
  const tn=document.getElementById("testNotify"); if(tn) tn.onclick=()=>{ collectNotify(); sendTest(); };
  d.querySelectorAll("[data-del]").forEach(b=>b.onclick=()=>{
    const n=b.dataset.del, g=cfg.pairs[n];
    delete cfg.pairs[n];
    cfg.ignore=[...new Set([...cfg.ignore,n,...(g?[g]:[])])];
    saveCfg(); openSettings("hosts");
  });
  d.querySelectorAll("[data-pair]").forEach(s=>s.onchange=()=>{
    cfg.pairs[s.dataset.pair]=s.value; saveCfg();
  });
  const _ap=document.getElementById("addP"); if(_ap) _ap.onclick=()=>{
    const n=document.getElementById("newN").value.trim(), g=document.getElementById("newG").value.trim();
    if(!n) return;
    cfg.pairs=cfg.pairs||{}; cfg.pairs[n]=g;
    cfg.ignore=cfg.ignore.filter(x=>x!==n&&x!==g);
    saveCfg(); openSettings("hosts");
  };
  document.getElementById("redisc")?.addEventListener("click",async()=>{
    cfg.pairs=null; cfg.ignore=[]; saveCfg();
    discovered=null; await discover(); dismissed=[]; store.set("xxops.dismiss",[]);
    openSettings("hosts"); tick();
  });
  document.getElementById("saveS").onclick=()=>{
    cfg.name=document.getElementById("nm").value.trim()||"Operator";
    cfg.stall=+document.getElementById("st").value||240;
    cfg.disk=+document.getElementById("dk").value||85;
    cfg.logGb=+document.getElementById("lg").value||2;
    cfg.lag=+document.getElementById("lgg").value||30;
    cfg.diskDays=+document.getElementById("dd").value||30;
    cfg.prom=document.getElementById("pu").value.trim()||DEF.prom;
    cfg.alertmgr=document.getElementById("am").value.trim();
    saveCfg();
    /* the result is rendered from notifyMsg next to the button - setting
       a DOM node here does not survive the paintSettings() repaint */
    const hadNotify = collectNotify();
    if(hadNotify){ saveNotifyCfg(); tick(); }   // stay open to show the result
    else { close(); tick(); }
  };
}
