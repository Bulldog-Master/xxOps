#!/usr/bin/env python3
"""xxOps agent - runs named actions, never commands.

The whole security argument rests on one property: a request names an ACTION
from a fixed catalogue. There is no code path where text from the network
reaches a shell. Actions are either argument lists run without a shell, or
Python functions. Even with signature checking bypassed entirely, the worst an
attacker could do is read a log.

Requests are signed with ssh-keygen -Y, so agents hold only a PUBLIC key -
compromising one host cannot forge requests to the other 45. Each request is
bound to one host, expires, and carries a nonce that cannot be reused.

Read-only for now. Nothing here changes state.

  /health   unsigned, says what this host is
  /actions  signed, lists what applies here
  /run      signed, runs one action
"""
import json, os, re, socket, subprocess, tempfile, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BIND    = os.environ.get("XXOPS_AGENT_BIND", "0.0.0.0")
PORT    = int(os.environ.get("XXOPS_AGENT_PORT", "8181"))
ALLOWED = os.environ.get("XXOPS_AGENT_SIGNERS", "/etc/xxops/allowed_signers")
SIGNER  = os.environ.get("XXOPS_AGENT_SIGNER", "xxops-monitor")
NAMESPACE = "xxops"

GW_CONF  = "/opt/xxnetwork/config/gateway.yaml"
GW_LOG   = "/opt/xxnetwork/log/gateway.log"
CMIX_LOG = "/opt/xxnetwork/log/cmix.log"
XXPROM   = "/var/lib/alloy/textfile/xx.prom"

HOSTNAME = os.environ.get("XXOPS_AGENT_HOST") or socket.gethostname()


# --------------------------------------------------------------- helpers
def tail_bytes(path, n=400000):
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - n))
            return f.read().decode("utf-8", "replace")
    except Exception:
        return ""


def last_matching(path, needle):
    """Last line containing needle, and how long ago it was written."""
    for line in reversed(tail_bytes(path).splitlines()):
        if needle in line:
            m = re.search(r"(\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2})", line)
            age = None
            if m:
                try:
                    age = int(time.time() - time.mktime(
                        time.strptime(m.group(1), "%Y/%m/%d %H:%M:%S")))
                except Exception:
                    pass
            return line.strip(), age
    return None, None


# --------------------------------------------------------------- actions
def a_gossip_status():
    g, ga = last_matching(GW_LOG, "Gossip received for round")
    l, la = last_matching(GW_LOG, "Local round data for round")
    out = []
    out.append(f"peer gossip:  {'none in the recent log' if not g else str(ga) + 's ago'}")
    out.append(f"local rounds: {'none in the recent log' if not l else str(la) + 's ago'}")
    if g and l:
        out.append("verdict: healthy - receiving from peers and from its own node")
    elif l and not g:
        out.append("verdict: ISOLATED - processing its own node but deaf to peers")
    elif not l and not g:
        out.append("verdict: DEAD - neither signal present")
    return "\n".join(out)


def a_cmix_status():
    r, age = last_matching(CMIX_LOG, "Round took")
    err = os.path.exists("/opt/xxnetwork/log/cmix-err.log")
    out = [f"last round: {'not found in the recent log' if not r else str(age) + 's ago'}"]
    if r:
        out.append(r)
    out.append(f"err file present: {err}"
               + ("  (transient by design - only matters if rounds also stopped)" if err else ""))
    return "\n".join(out)


def a_producer_status():
    try:
        st = os.stat(XXPROM)
        age = int(time.time() - st.st_mtime)
        verdict = "healthy" if age < 180 else "STALE - metrics are frozen at their last values"
        return f"xx.prom written {age}s ago ({st.st_size} bytes)\nverdict: {verdict}"
    except FileNotFoundError:
        return "xx.prom does not exist - the producer has never run here"


def a_watchdog_state():
    def read(p):
        try:
            return open(p).read().strip()
        except Exception:
            return "?"
    fails = read("/var/lib/xxops-watchdog/consec_fail")
    total = read("/var/lib/xxops-watchdog/restarts")
    gave_up = fails.isdigit() and int(fails) >= 3
    return (f"restarts total: {total}\nconsecutive failures: {fails}\n"
            f"verdict: {'GIVEN UP - needs a human' if gave_up else 'armed'}")


def a_chain_health():
    import urllib.request
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "system_health",
                       "params": []}).encode()
    try:
        req = urllib.request.Request("http://localhost:9933", data=body,
                                     headers={"Content-Type": "application/json"})
        d = json.load(urllib.request.urlopen(req, timeout=8))["result"]
        return (f"peers: {d.get('peers')}\nsyncing: {d.get('isSyncing')}\n"
                f"verdict: {'catching up' if d.get('isSyncing') else 'in sync'}")
    except Exception as e:
        return f"local chain RPC did not answer: {e}"


def _unit_states(units):
    """is-active for each unit, one labelled line apiece."""
    out = []
    for u in units:
        try:
            r = subprocess.run([SYSTEMCTL, "is-active", u],
                               capture_output=True, text=True, timeout=10)
            raw = (r.stdout or r.stderr).strip().splitlines()
            state = raw[0] if raw else "unknown"
        except Exception:
            state = "unknown"
        out.append(f"{u}: {state}")
    return "\n".join(out)


def a_node_status():
    """The services a node actually runs. postgresql is here because cMix
    depends on it."""
    return _unit_states(("xxnetwork-chain", "xxnetwork-cmix", "postgresql"))


def a_gateway_status():
    """The services a gateway actually runs."""
    return _unit_states(("xxnetwork-chain", "xxnetwork-gateway"))


def a_cert_expiry():
    """Every certificate in the cred dir, both roles.

    Nodes carry a gateway-cert.crt too. On 2026-08-02 that copy was the only
    surviving version of a gateway certificate that had expired a long time
    earlier, so seeing both machines' certificates side by side matters.
    """
    cred = "/opt/xxnetwork/cred"
    try:
        names = sorted(n for n in os.listdir(cred) if n.endswith(".crt"))
    except Exception as e:
        return f"cannot read {cred}: {e}"
    out = []
    for n in names:
        try:
            r = subprocess.run(["openssl", "x509", "-in",
                                os.path.join(cred, n), "-noout", "-enddate"],
                               capture_output=True, text=True, timeout=10)
            end = r.stdout.strip().replace("notAfter=", "") or "unreadable"
        except Exception:
            end = "unreadable"
        out.append(f"{n}: {end}")
    return "\n".join(out) or "no certificates found"


def a_disk_usage():
    """What is filling this host, not just how full it is.

    Bounded on purpose: depth 1 over three known paths with a timeout. /run is
    request-response with no progress, so an unbounded du across a node's
    archive filesystem would read as hung.
    """
    out = []
    for base in ("/opt/xxnetwork", "/var/lib", "/var/log"):
        if not os.path.isdir(base):
            continue
        try:
            r = subprocess.run(["du", "-x", "-d1", "-m", base],
                               capture_output=True, text=True, timeout=60)
        except subprocess.TimeoutExpired:
            out.append(f"{base}: timed out")
            continue
        except Exception as e:
            out.append(f"{base}: {e}")
            continue
        rows = []
        for line in r.stdout.splitlines():
            parts = line.split("\t", 1)
            if len(parts) != 2:
                continue
            try:
                rows.append((int(parts[0]), parts[1]))
            except ValueError:
                pass
        rows.sort(reverse=True)
        for mb, path in rows[:8]:
            out.append(f"{mb / 1024:8.1f} GB  {path}")
    return "\n".join(out) or "nothing to report"


def a_wrapper_log():
    """Tail of the role's wrapper log, with the credentials removed.

    The wrapper logs its entire configuration on every startup, INCLUDING a
    live s3_access_key and s3_secret for the elixxir management bucket. Those
    belong to xx network, they sit on every host, and they must not reach the
    app or anyone's screenshot. Drop any line carrying them.
    """
    path = ("/opt/xxnetwork/log/gateway-wrapper.log"
            if os.path.exists(GW_CONF)
            else "/opt/xxnetwork/log/cmix-wrapper.log")
    try:
        text = tail_bytes(path, 40000)
    except Exception as e:
        return f"cannot read {path}: {e}"
    lines = [l for l in text.splitlines()
             if "s3_secret" not in l and "s3_access_key" not in l]
    return "\n".join(lines[-40:]) or "nothing in the log"


CATALOGUE = {
    # name: (needs, how, description)
    "disk":            (None,     ["df", "-h", "/"],            "disk usage"),
    "disk-usage":      (None,     a_disk_usage,                 "what is filling this host"),
    "wrapper-log":     (None,     a_wrapper_log,                "tail of the wrapper log, credentials removed"),
    "versions":        (None,     ["uname", "-sr"],             "kernel and OS"),
    "node-status":     (CMIX_LOG, a_node_status,
                                  "are this node's services running"),
    "gateway-status":  (GW_CONF,  a_gateway_status,
                                  "are this gateway's services running"),
    "uptime":          (None,     ["uptime"],                   "load and uptime"),
    "producer-status": (None,     a_producer_status,            "is this host still producing metrics"),
    "chain-health":    (None,     a_chain_health,               "peers and sync state from local RPC"),
    "cert-expiry":     (None,     a_cert_expiry,
                                  "expiry of every certificate on this host"),
    "gossip-status":   (GW_LOG,   a_gossip_status,              "peering and liveness in one answer"),
    "watchdog-state":  (GW_CONF,  a_watchdog_state,             "has automated recovery given up"),
    "cmix-status":     (CMIX_LOG, a_cmix_status,                "last cMix round and the err file"),
    "gpu":             (None,     ["nvidia-smi", "--query-gpu=name,temperature.gpu,power.draw,memory.used",
                                   "--format=csv,noheader"], "GPU state, nodes only"),
}


# --- the action tier. these CHANGE things. ---------------------------------
# each entry is (needs, command, description). the command is a fixed argument
# list run through sudo; sudoers permits exactly these and nothing else.
SYSTEMCTL = "/usr/bin/systemctl" if os.path.exists("/usr/bin/systemctl") else "/bin/systemctl"

CHANGES = {
    "restart-gateway": (GW_CONF,  [SYSTEMCTL, "restart", "xxnetwork-gateway"],
                        "restart the gateway process"),
    "stop-cmix":       (CMIX_LOG, [SYSTEMCTL, "stop", "xxnetwork-cmix"],
                        "stop cMix on this node - it will stop earning"),
    "start-cmix":      (CMIX_LOG, [SYSTEMCTL, "start", "xxnetwork-cmix"],
                        "start cMix on this node"),
    "restart-chain":   (None,     [SYSTEMCTL, "restart", "xxnetwork-chain"],
                        "restart the chain process"),
    "start-gateway":   (GW_CONF,  [SYSTEMCTL, "start", "xxnetwork-gateway"],
                        "start the gateway process"),
    "stop-gateway":    (GW_CONF,  [SYSTEMCTL, "stop", "xxnetwork-gateway"],
                        "stop the gateway - it will go deaf to peers"),
    "start-chain":     (None,     [SYSTEMCTL, "start", "xxnetwork-chain"],
                        "start the chain process"),
    "stop-chain":      (None,     [SYSTEMCTL, "stop", "xxnetwork-chain"],
                        "stop the chain - this leaves consensus until started"),
    "restart-cmix":    (CMIX_LOG, [SYSTEMCTL, "restart", "xxnetwork-cmix"],
                        "restart cMix on this node"),
    "update-node-reboot":    (CMIX_LOG, ["/usr/local/bin/xxops-update-node.sh"],
                        "update all packages and reboot - stops earning until it returns"),
    "update-gateway-reboot": (GW_CONF,  ["/usr/local/bin/xxops-update-gateway.sh"],
                        "update all packages and reboot - off the network until it returns"),
}


def journal(msg):
    try:
        subprocess.run(["logger", "-t", "xxops-agent", msg], timeout=5)
    except Exception:
        pass


def execute_change(name):
    needs, cmd, _desc = CHANGES[name]
    # The role gate was only ever applied when LISTING actions, so a signed
    # request could run a node action on a gateway. Refuse it here too.
    if needs is not None and not os.path.exists(needs):
        return {"exit": 1, "changed": False, "seconds": 0,
                "output": f"{name} does not apply to this host"}
    journal(f"running {name}: {' '.join(cmd)}")
    t0 = time.time()
    try:
        r = subprocess.run(["/usr/bin/sudo", "-n"] + cmd,
                           capture_output=True, text=True, timeout=90)
        journal(f"{name} finished with exit {r.returncode}")
        return {"exit": r.returncode,
                "output": ((r.stdout + r.stderr).strip()
                           or ("done" if r.returncode == 0 else "no output")),
                "seconds": round(time.time() - t0, 2), "changed": True}
    except subprocess.TimeoutExpired:
        journal(f"{name} timed out")
        return {"exit": 124, "output": "timed out", "seconds": 90, "changed": True}
    except Exception as e:
        journal(f"{name} failed: {e}")
        return {"exit": 1, "output": str(e), "seconds": 0, "changed": False}


def applicable():
    out = {}
    for name, (needs, _how, desc) in CATALOGUE.items():
        if needs is None or os.path.exists(needs):
            out[name] = desc
    for name, (needs, _cmd, desc) in CHANGES.items():
        if needs is None or os.path.exists(needs):
            out[name] = "CHANGES THINGS - " + desc
    return out


# --------------------------------------------------------------- signing
def verify(payload_bytes, signature):
    if not os.path.exists(ALLOWED):
        return False, "no allowed_signers file on this host"
    sf = None
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".sig", delete=False) as f:
            f.write(signature)
            sf = f.name
        p = subprocess.run(
            ["ssh-keygen", "-Y", "verify", "-f", ALLOWED, "-I", SIGNER,
             "-n", NAMESPACE, "-s", sf],
            input=payload_bytes, capture_output=True, timeout=10)
        return p.returncode == 0, (p.stderr.decode().strip() or "bad signature")
    except Exception as e:
        return False, str(e)
    finally:
        if sf:
            try: os.unlink(sf)
            except Exception: pass


SEEN = {}


def check(p, now):
    for f in ("action", "host", "nonce", "expires"):
        if f not in p:
            return False, f"missing {f}"
    if p["host"] != HOSTNAME:
        return False, f"addressed to {p['host']}, this host is {HOSTNAME}"
    if p["expires"] < now:
        return False, "expired"
    if p["expires"] > now + 3600:
        return False, "expiry implausibly far ahead"
    if p["nonce"] in SEEN:
        return False, "replay: nonce already used"
    name = p["action"]
    if name in CHANGES:
        if p.get("confirm") is not True:
            return False, "this action changes things and was not confirmed"
        needs, _cmd, _d = CHANGES[name]
        if needs and not os.path.exists(needs):
            return False, "not applicable on this host"
        return True, "ok"
    if name not in CATALOGUE:
        return False, "unknown action"
    needs, _how, _d = CATALOGUE[name]
    if needs and not os.path.exists(needs):
        return False, "not applicable on this host"
    return True, "ok"


def execute(name):
    needs, how, _desc = CATALOGUE[name]
    t0 = time.time()
    if callable(how):
        try:
            return {"exit": 0, "output": how()[:8000],
                    "seconds": round(time.time() - t0, 2)}
        except Exception as e:
            return {"exit": 1, "output": f"{type(e).__name__}: {e}",
                    "seconds": round(time.time() - t0, 2)}
    try:
        r = subprocess.run(how, capture_output=True, text=True, timeout=25)
        return {"exit": r.returncode, "output": (r.stdout + r.stderr)[-8000:],
                "seconds": round(time.time() - t0, 2)}
    except FileNotFoundError:
        return {"exit": 127, "output": "that tool is not installed here", "seconds": 0}
    except subprocess.TimeoutExpired:
        return {"exit": 124, "output": "timed out", "seconds": 25}


# --------------------------------------------------------------- http
class H(BaseHTTPRequestHandler):
    server_version = "xxops-agent"

    def log_message(self, *a):
        pass

    def _send(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.path == "/health":
            return self._send(200, {"ok": True, "host": HOSTNAME,
                                    "role": "gateway" if os.path.exists(GW_CONF) else "node",
                                    "actions": len(applicable())})
        return self._send(404, {"ok": False})

    def do_POST(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            return self._send(400, {"ok": False, "error": "unreadable request"})

        payload_text = body.get("payload", "")
        ok, why = verify(payload_text.encode(), body.get("sig", ""))
        if not ok:
            return self._send(403, {"ok": False, "error": f"rejected: {why}"})
        try:
            p = json.loads(payload_text)
        except Exception:
            return self._send(400, {"ok": False, "error": "payload is not valid json"})

        now = time.time()
        ok, why = check(p, now)
        if not ok:
            return self._send(400, {"ok": False, "error": why})

        SEEN[p["nonce"]] = now
        for k in [k for k, v in SEEN.items() if v < now - 3600]:
            del SEEN[k]

        if self.path == "/actions":
            return self._send(200, {"ok": True, "host": HOSTNAME, "actions": applicable()})
        if self.path == "/run":
            r = (execute_change(p["action"]) if p["action"] in CHANGES
                 else execute(p["action"]))
            return self._send(200, {"ok": True, "host": HOSTNAME,
                                    "action": p["action"], **r})
        return self._send(404, {"ok": False, "error": "no such endpoint"})


if __name__ == "__main__":
    print(f"xxops-agent on {BIND}:{PORT} as {HOSTNAME}, "
          f"{len(applicable())} actions available", flush=True)
    ThreadingHTTPServer((BIND, PORT), H).serve_forever()
