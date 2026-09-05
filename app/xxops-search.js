/* Lifted out of xxops.html unchanged.
 *
 * A classic script rather than a module, on purpose: these stay
 * global, so every caller left in the page keeps working.
 */

function askHaystack(e){
  /* every string value in the entry, so the three fix headings are covered
     without naming them - and a field added later is covered too */
  const parts = [];
  Object.keys(e || {}).forEach(k=>{
    const v = e[k];
    if(typeof v === "string") parts.push(v);
    else if(Array.isArray(v)) parts.push(v.filter(x=>typeof x==="string").join(" "));
  });
  return parts.join(" ").toLowerCase();
}

function askSection(q, names){
  /* typing the name of a thing should show you the things. "fixes" appears
     in no title or tag, so contents-only search returns silence for the most
     natural query anyone could make. */
  return names.some(n=>n.indexOf(q) === 0 || q.indexOf(n) === 0);
}

function askSearch(q){
  q = String(q || "").trim().toLowerCase();
  if(q.length < 2) return null;
  const out = {fixes: [], hosts: [], alerts: []};

  if(askSection(q, ["fix", "fixes", "solved", "solution"]))
    out.fixes = (typeof fixes !== "undefined" ? fixes : []).slice();
  if(askSection(q, ["host", "hosts", "machine", "machines", "server"]))
    out.hosts = (typeof hosts !== "undefined" ? hosts : []).slice();
  if(askSection(q, ["alert", "alerts", "firing", "alarm"]))
    out.alerts = (typeof alerts !== "undefined" ? alerts : []).slice();

  (typeof fixes !== "undefined" ? fixes : []).forEach(e=>{
    if(out.fixes.indexOf(e) < 0 && askHaystack(e).indexOf(q) >= 0)
      out.fixes.push(e);
  });

  (typeof hosts !== "undefined" ? hosts : []).forEach(h=>{
    if(out.hosts.indexOf(h) < 0 &&
       String(h.host || "").toLowerCase().indexOf(q) >= 0) out.hosts.push(h);
  });

  (typeof alerts !== "undefined" ? alerts : []).forEach(a=>{
    const l = a.labels || {};
    const hay = [l.alertname, l.instance, l.severity]
      .filter(Boolean).join(" ").toLowerCase();
    if(out.alerts.indexOf(a) < 0 && hay.indexOf(q) >= 0) out.alerts.push(a);
  });
  return out;
}

function askRender(q){
  const box = document.getElementById("askRes");
  if(!box) return;
  const r = askSearch(q);
  if(!r){ box.hidden = true; box.innerHTML = ""; return; }
  /* an empty result and a broken feature must not look identical */

  let h = "";
  if(r.fixes.length){
    h += '<div class="askgrp">Fixes</div>';
    r.fixes.slice(0, 5).forEach(e=>{
      h += '<button class="askitem" data-askfix="'+esc(e.id)+'">'+
        esc(e.title || "untitled")+
        '<small>'+(e.alertname ? esc(e.alertname)+" \u00b7 " : "")+
        (e.host ? esc(e.host)+" \u00b7 " : "")+
        esc((e.tags || []).join(", "))+'</small></button>';
    });
  }
  if(r.hosts.length){
    h += '<div class="askgrp">Hosts</div>';
    r.hosts.slice(0, 6).forEach(x=>{
      h += '<button class="askitem" data-askhost="'+esc(x.host)+'">'+esc(x.host)+
        '<small>'+(x.up ? "reporting" : "not reporting")+
        (x.role ? " \u00b7 "+esc(x.role) : "")+'</small></button>';
    });
  }
  if(r.alerts.length){
    h += '<div class="askgrp">Firing now</div>';
    r.alerts.slice(0, 6).forEach(a=>{
      const l = a.labels || {};
      h += '<button class="askitem" data-askalert="'+esc(l.instance || "")+'">'+
        esc(l.alertname || "?")+
        '<small>'+esc(l.instance || "")+
        (l.severity ? " \u00b7 "+esc(l.severity) : "")+'</small></button>';
    });
  }
  if(!h) h = '<div class="asknone">Nothing matches that.</div>';
  box.innerHTML = h;
  box.hidden = false;
}

function askClose(){
  const b = document.getElementById("askRes");
  if(b){ b.hidden = true; b.innerHTML = ""; }
}

function askGo(tab){
  view = tab;
  document.querySelectorAll(".nb").forEach(n=>
    n.setAttribute("aria-selected", n.dataset.v === tab));
  askClose();
  render();
  window.scrollTo({top: 0});
}

{
  const inp = document.getElementById("ask");
  if(inp){
    inp.oninput = e=>askRender(e.target.value);
    inp.onkeydown = e=>{
      if(e.key === "Escape"){ inp.value = ""; askClose(); inp.blur(); }
    };
  }
  const box = document.getElementById("askRes");
  if(box){
    box.addEventListener("click", e=>{
      const b = e.target.closest("button");
      if(!b) return;
      if(b.dataset.askfix !== undefined){
        fixOpen = b.dataset.askfix; askGo("fix"); return;
      }
      if(b.dataset.askhost !== undefined){
        openHost = b.dataset.askhost; askGo("host"); return;
      }
      if(b.dataset.askalert !== undefined){
        openHost = b.dataset.askalert; askGo("host"); return;
      }
    });
  }
  document.addEventListener("click", e=>{
    const w = document.getElementById("askWrap");
    if(w && !w.contains(e.target)) askClose();
  });
}
