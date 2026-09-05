#!/usr/bin/env python3
"""xxops-cmd - discover agents and send them signed requests.

Runs on the monitor. Holds the private key; the agents hold only the public
half, so nothing else on the tailnet can issue a command.

Tailscale knows hosts by machine name (__MONITOR_HOST__) while Prometheus and the app
know them by label (example_gt), and there is no reliable mapping between the
two. Rather than maintain one by hand, discovery asks each agent who it thinks
it is - the same self-describing approach the producer and the app already use.

  xxops-cmd discover                 scan the tailnet, cache what was found
  xxops-cmd list                     show the cache
  xxops-cmd actions <host>           what that host can do
  xxops-cmd run <host> <action>      do it on one host
  xxops-cmd run <host> <action> --yes  for actions that change things
  xxops-cmd all <action>             do it everywhere it applies
  xxops-cmd gateways <action>        gateways only
  xxops-cmd nodes <action>           nodes only

Asking one question of the whole fleet is the point of this layer: a
certificate sweep across every gateway used to mean one ssh session each.
"""
import json, os, secrets, subprocess, sys, tempfile, time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

KEY   = os.environ.get("XXOPS_CMD_KEY", "/etc/xxops/cmd_key")
CACHE = os.environ.get("XXOPS_AGENT_CACHE", "/var/lib/xxops/agents.json")
PORT  = 8181
NAMESPACE = "xxops"
TTL   = 120        # generous, because a fleet sweep signs many requests up front


# ----------------------------------------------------------------- discovery
def tailnet_addresses():
    try:
        out = subprocess.run(["tailscale", "status", "--json"],
                             capture_output=True, text=True, timeout=20).stdout
        d = json.loads(out)
    except Exception as e:
        print(f"could not read tailscale status: {e}")
        return []
    ips = []
    for section in ("Peer", "Self"):
        peers = d.get(section) or {}
        if section == "Self":
            peers = {"self": peers}
        for p in peers.values():
            for ip in (p or {}).get("TailscaleIPs") or []:
                if ":" not in ip:
                    ips.append(ip)
    return sorted(set(ips))


def ask(ip):
    try:
        with urllib.request.urlopen(f"http://{ip}:{PORT}/health", timeout=4) as r:
            d = json.load(r)
        if not d.get("host"):
            return None
        return d["host"], {"ip": ip, "role": d.get("role"),
                           "actions": d.get("actions")}
    except Exception:
        return None


def discover():
    ips = tailnet_addresses()
    print(f"asking {len(ips)} tailnet addresses...")
    found = {}
    with ThreadPoolExecutor(max_workers=24) as ex:
        for r in ex.map(ask, ips):
            if r:
                found[r[0]] = r[1]
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    with open(CACHE, "w") as f:
        json.dump(found, f, indent=2, sort_keys=True)
    gw = sum(1 for v in found.values() if v.get("role") == "gateway")
    print(f"found {len(found)} agents: {gw} gateways, {len(found)-gw} nodes")
    return found


def load():
    """Read the cache, tolerating the older label -> ip shape."""
    try:
        raw = json.load(open(CACHE))
    except Exception:
        return {}
    out = {}
    for k, v in raw.items():
        out[k] = {"ip": v, "role": None, "actions": None} if isinstance(v, str) else v
    return out


# ------------------------------------------------------------------- signing
def sign(payload):
    tmp = None
    try:
        with tempfile.NamedTemporaryFile("w", delete=False) as f:
            f.write(payload)
            tmp = f.name
        r = subprocess.run(["ssh-keygen", "-Y", "sign", "-f", KEY,
                            "-n", NAMESPACE, tmp],
                           capture_output=True, text=True, timeout=15)
        if r.returncode != 0:
            return None, r.stderr.strip()
        return open(tmp + ".sig").read(), None
    except Exception as e:
        return None, str(e)
    finally:
        for p in (tmp, (tmp or "") + ".sig"):
            try: os.unlink(p)
            except Exception: pass


# Which actions change things is the AGENT's business, and it already says so -
# applicable() prefixes those descriptions with "CHANGES THINGS -". Keeping a
# copy here is what let `all stop-chain` through: the list was written once and
# the catalogue kept growing.
CHANGING_PREFIX = "CHANGES THINGS"


def catalogue(host, agents):
    """What that agent says it can do: {name: description}, or None if it
    would not answer. /actions ignores the action argument."""
    _h, ok, d = call(host, agents[host], "actions", "disk")
    return (d.get("actions") or {}) if ok else None


def is_changing(desc):
    return str(desc or "").strip().upper().startswith(CHANGING_PREFIX)



def call(host, info, endpoint, action, confirm=False):
    """Returns (host, ok, result_or_error)."""
    body = {"action": action, "host": host,
            "nonce": secrets.token_hex(12),
            "expires": int(time.time()) + TTL}
        # Always sent, so an action the caller does not recognise cannot silently
    # fail to ask. The agent decides whether it matters.
    body["confirm"] = True if confirm else False
    payload = json.dumps(body, sort_keys=True, separators=(",", ":"))
    sig, err = sign(payload)
    if sig is None:
        return host, False, f"could not sign: {err}"
    body = json.dumps({"payload": payload, "sig": sig}).encode()
    url = f"http://{info['ip']}:{PORT}/{endpoint}"
    try:
        req = urllib.request.Request(url, data=body,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=45) as resp:
            d = json.load(resp)
    except urllib.error.HTTPError as e:
        try:
            d = json.load(e)
        except Exception:
            return host, False, f"HTTP {e.code}"
    except Exception as e:
        return host, False, str(e)
    if not d.get("ok"):
        return host, False, d.get("error", "refused")
    return host, True, d


def show(host, d):
    out = (d.get("output") or "").rstrip()
    lines = out.splitlines()
    if len(lines) <= 1:
        print(f"  {host:<17} {lines[0] if lines else '(no output)'}")
    else:
        print(f"  {host}")
        for l in lines:
            print(f"      {l}")


def one(host, endpoint, action, confirm=False):
    agents = load()
    if host not in agents:
        print(f"no agent known for {host} - run: xxops-cmd discover")
        return 1
    if endpoint == "run":
        cat = catalogue(host, agents)
        if cat is None:
            print(f"{host}: no answer - is the agent up? try: xxops-cmd discover")
            return 1
        if action not in cat:
            print(f"{host} has no action called {action}")
            return 1
        if is_changing(cat[action]) and not confirm:
            print(f"{action} changes things on {host}. Add --yes if you mean it.")
            return 1
    _h, ok, d = call(host, agents[host], endpoint, action, confirm)
    if not ok:
        print(f"{host}: {d}")
        return 1
    if endpoint == "actions":
        for k, v in sorted(d.get("actions", {}).items()):
            print(f"   {k:<18} {v}")
    else:
        print(f"--- {host} · {d.get('action')} · exit {d.get('exit')} "
              f"· {d.get('seconds')}s")
        print((d.get("output") or "").rstrip())
    return 0


def fleet(action, role=None):
    agents = load()
    # Ask the first agent that HAS this action. Distinguishing "has it and it
    # is safe" from "does not have it at all" is the whole guard - treat the
    # second as the first and it reopens.
    for h in sorted(agents):
        cat = catalogue(h, agents)
        if cat and action in cat:
            if is_changing(cat[action]):
                print(f"{action} changes things. Running it everywhere is not")
                print("allowed - do it one host at a time so you can watch.")
                return 1
            break
    if not agents:
        print("nothing cached - run: xxops-cmd discover")
        return 1
    targets = {k: v for k, v in agents.items()
               if role is None or v.get("role") == role}
    if not targets:
        print(f"no {role}s in the cache - try: xxops-cmd discover")
        return 1
    print(f"{action} on {len(targets)} hosts\n")
    results = []
    with ThreadPoolExecutor(max_workers=16) as ex:
        for r in ex.map(lambda kv: call(kv[0], kv[1], "run", action),
                        sorted(targets.items())):
            results.append(r)
    ran = skipped = failed = 0
    for host, ok, d in results:
        if ok:
            ran += 1
            show(host, d)
        elif "not applicable" in str(d):
            skipped += 1          # counted, not printed - it is not a problem
        else:
            failed += 1
            print(f"  {host:<17} FAILED: {d}")
    print(f"\n{ran} ran"
          + (f", {skipped} not applicable" if skipped else "")
          + (f", {failed} FAILED" if failed else ""))
    return 1 if failed else 0


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 1
    cmd = argv[1]
    if cmd == "discover":
        found = discover()
        for k in sorted(found):
            v = found[k]
            print(f"   {k:<17} {v['ip']:<16} {v.get('role') or '?':<8} "
                  f"{v.get('actions')} actions")
        return 0
    if cmd == "list":
        a = load()
        gw = sum(1 for v in a.values() if v.get("role") == "gateway")
        print(f"{len(a)} agents cached: {gw} gateways, {len(a)-gw} nodes")
        for k in sorted(a):
            print(f"   {k:<17} {a[k]['ip']:<16} {a[k].get('role') or '?'}")
        return 0
    if cmd == "actions" and len(argv) == 3:
        return one(argv[2], "actions", "disk")
    if cmd == "run" and len(argv) in (4, 5):
        return one(argv[2], "run", argv[3], confirm=("--yes" in argv[4:]))
    if cmd == "all" and len(argv) == 3:
        return fleet(argv[2])
    if cmd == "gateways" and len(argv) == 3:
        return fleet(argv[2], "gateway")
    if cmd == "nodes" and len(argv) == 3:
        return fleet(argv[2], "node")
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
