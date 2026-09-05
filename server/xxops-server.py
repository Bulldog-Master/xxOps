
#!/usr/bin/env python3
"""
xxOps server — the small backend that lets the app configure itself.

Replaces `python3 -m http.server` in the xxops-app unit. Serves the same page
on the same port, and adds the endpoints the app needs to stop sending you to
a terminal:

  GET  /api/settings          thresholds, validator pairs, profile
  POST /api/settings          save them (shared across your devices)

  GET  /api/notify            contacts, channels, SMTP — secrets masked
  POST /api/notify            save, regenerate alertmanager.yml, validate, reload
  POST /api/notify/test       send a real alert through Alertmanager

  POST /api/telegram/pair     start pairing: returns a short code
  GET  /api/telegram/pair     poll: has anyone sent the code to the bot yet?

Design notes:
  - stdlib only, no dependencies
  - never runs as root, and needs no sudo at all
  - alertmanager.yml is validated with amtool BEFORE it replaces the live file,
    so a bad edit in the UI can't take alerting down
  - reloading goes through Alertmanager's own POST /-/reload, so the service
    needs no sudo and no privilege beyond writing that one file
  - the previous config is kept as .bak on every write
  - secrets are masked on read and only overwritten when you send a new value
"""

import hashlib
import base64, hashlib, hmac, http.cookies, json, os, re, secrets, string, struct, subprocess, sys, tempfile, time, urllib.parse
import urllib.request
CMD_KEY = os.environ.get("XXOPS_CMD_KEY", "/var/lib/xxops/cmd_key")
INVITE_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"   # no look-alikes
INVITE_TTL = 24 * 3600
INVITE_MAX_TRIES = 10


def new_invite_code():
    # generated inline rather than through new_recovery_codes(), whose return
    # shape differs; consolidating the two is still worth doing
    return "".join(secrets.choice(INVITE_ALPHABET) for _ in range(4)) + "-" + \
           "".join(secrets.choice(INVITE_ALPHABET) for _ in range(4))


def contact_by_id(cid):
    try:
        for c in load_notify().get("contacts", []):
            if c.get("id") == cid:
                return c
    except Exception:
        pass
    return None


def prune_invites(store):
    now = int(time.time())
    inv = store.get("invites", {})
    for code in [k for k, v in inv.items()
                 if v.get("expires", 0) < now or v.get("tries", 0) >= INVITE_MAX_TRIES]:
        inv.pop(code, None)
    store["invites"] = inv
    return store


def find_invite(store, given):
    """Constant-time lookup so a wrong code cannot be distinguished by timing."""
    given = re.sub(r"\s", "", str(given or "")).lower()
    if not given:
        return None, None
    for code, rec in (store.get("invites") or {}).items():
        if secrets.compare_digest(code, given):
            return code, rec
    return None, None


def _template(name):
    """An HTML page kept as a file rather than a string in this source.

    Beside this file, not under APP_DIR: the pages are assigned long before
    APP_DIR exists, and a page is part of the program rather than part of the
    installation. Read at import so a missing one stops the service now rather
    than at someone's login screen.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(here, name), encoding="utf-8") as fh:
        return fh.read()


REDEEM_PAGE = _template("redeem.html")



def docs_dir():
    """Resolved when called, not at import - APP_DIR is defined
    further down this file and a module-level reference here
    crashed the service on startup."""
    return os.path.join(APP_DIR, "docs")
# The markdown renderer lives in xxops_md.py. Text in, HTML out;
# it has no idea what a validator is.
from xxops_md import DOC_NAME, doc_page, md_escape, md_to_html

def allowed_instances(me):
    """Hosts this caller may see, or None meaning no restriction.

    Derived from the contact record their account is bound to: their
    validators plus whatever gateways settings pairs those nodes to. Never a
    second list to maintain, so it cannot drift from alert routing.
    """
    if me and me.get("role") == "owner":
        return None
    if not me:
        # No caller at all. Before any account exists there is nothing to
        # protect and the app still has to render, so that case is
        # unrestricted. Once authentication is on, a missing caller is a
        # mistake somewhere, and a mistake must see nothing rather than
        # everything.
        return None if not auth_required() else set()
    cid = me.get("contactId")
    if not cid:
        return set()          # a non-owner bound to nothing sees nothing
    try:
        contacts = load_notify().get("contacts", [])
    except Exception:
        return set()
    mine = None
    for c in contacts:
        if c.get("id") == cid:
            mine = c
            break
    if not mine:
        return set()
    nodes = [n for n in (mine.get("validators") or []) if n]
    allowed = set(nodes)
    try:
        pairs = (_read(SETTINGS, {}) or {}).get("pairs", {}) or {}
    except Exception:
        pairs = {}
    for n in nodes:
        gw = pairs.get(n)
        if gw:
            allowed.add(gw)
    return allowed


def scope_prom(payload, allowed):
    """Drop series the caller may not see. Default deny on missing labels."""
    if allowed is None:
        return payload
    data = payload.get("data")
    if not isinstance(data, dict):
        return {"status": "success", "data": {"resultType": "vector", "result": []}}
    out = []
    for series in data.get("result", []) or []:
        inst = (series.get("metric") or {}).get("instance")
        if inst and inst in allowed:
            out.append(series)
    data = dict(data, result=out)
    return dict(payload, data=data)


def scope_am(payload, allowed):
    """Same idea for Alertmanager list responses."""
    if allowed is None:
        return payload
    if not isinstance(payload, list):
        return []
    out = []
    for item in payload:
        labels = item.get("labels") if isinstance(item, dict) else None
        inst = (labels or {}).get("instance")
        if inst and inst in allowed:
            out.append(item)
    return out


AGENT_CACHE = os.environ.get("XXOPS_AGENT_CACHE", "/var/lib/xxops/agents.json")


# --- login throttle ---------------------------------------------------------
# In memory on purpose: a restart clears it, which is the way back in if you
# ever lock yourself out. Counted against the username as submitted, existing
# or not, so it cannot be used to discover which accounts are real.
LOGIN_FAILS = {}
LOGIN_MAX = 5          # failures allowed
LOGIN_WINDOW = 900     # within this many seconds
LOGIN_LOCKOUT = 900    # then refused for this many


def login_locked(u):
    """Seconds still to wait, or 0."""
    rec = LOGIN_FAILS.get(u)
    if not rec:
        return 0
    _fails, _first, until = rec
    left = int(until - time.time())
    return left if left > 0 else 0


def login_failed(u):
    now = time.time()
    fails, first, _until = LOGIN_FAILS.get(u, (0, now, 0))
    if now - first > LOGIN_WINDOW:      # old failures do not accumulate
        fails, first = 0, now
    fails += 1
    LOGIN_FAILS[u] = (fails, first,
                      now + LOGIN_LOCKOUT if fails >= LOGIN_MAX else 0)


def login_ok(u):
    LOGIN_FAILS.pop(u, None)


def agent_request(ip, host, action, endpoint, confirm=False):
    """Sign a request the way /api/agent/run does, then post it to the agent.

    Returns (True, parsed_json) or (False, message). Kept separate from the
    /api/agent/run handler on purpose - that path is proven, so it keeps its
    own copy rather than being refactored underneath a working feature.
    """
    req_body = {"action": action, "host": host,
                "nonce": secrets.token_hex(12),
                "expires": int(time.time()) + 60}
    if confirm is True:
        req_body["confirm"] = True
    payload = json.dumps(req_body, sort_keys=True, separators=(",", ":"))
    tmp = None
    try:
        with tempfile.NamedTemporaryFile("w", delete=False) as f:
            f.write(payload)
            tmp = f.name
        r = subprocess.run(["ssh-keygen", "-Y", "sign", "-f", CMD_KEY,
                            "-n", "xxops", tmp],
                           capture_output=True, text=True, timeout=15)
        if r.returncode != 0:
            return False, "could not sign: " + r.stderr.strip()
        sig = open(tmp + ".sig").read()
    except Exception as e:
        return False, str(e)
    finally:
        for f in (tmp, (tmp or "") + ".sig"):
            try:
                os.unlink(f)
            except Exception:
                pass
    try:
        req = urllib.request.Request(
            "http://%s:8181%s" % (ip, endpoint),
            data=json.dumps({"payload": payload, "sig": sig}).encode(),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            return True, json.load(resp)
    except urllib.error.HTTPError as e:
        try:
            return True, json.load(e)
        except Exception:
            return False, "agent returned HTTP %d" % e.code
    except Exception as e:
        return False, "could not reach the agent: %s" % e
RESOLUTIONS = os.environ.get("XXOPS_RESOLUTIONS", "/var/lib/xxops/resolutions.json")
AUTH_FILE = os.environ.get("XXOPS_AUTH_FILE", "/var/lib/xxops/auth.json")
SESSIONS_FILE = os.environ.get("XXOPS_SESSIONS_FILE", "/var/lib/xxops/sessions.json")
SESSION_DAYS = 30

def load_users():
    """The store. An old single-password file counts as no users, so the app
    walks you through setup rather than stranding you with a form."""
    try:
        with open(AUTH_FILE) as f:
            d = json.load(f)
        if isinstance(d, dict) and isinstance(d.get("users"), dict):
            return d
    except Exception:
        pass
    return {"users": {}, "version": 1}


def save_users(store):
    tmp = AUTH_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(store, f, indent=1)
    os.chmod(tmp, 0o600)
    os.replace(tmp, AUTH_FILE)


def needs_setup():
    return not load_users().get("users")


def auth_required():
    return not needs_setup()


def hash_password(password, salt=None):
    salt = salt or secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1, dklen=32)
    return {"algo": "scrypt", "n": 16384, "r": 8, "p": 1,
            "salt": salt.hex(), "hash": dk.hex()}


def check_password(password, rec):
    if not rec or not rec.get("hash"):
        return False
    try:
        dk = hashlib.scrypt(password.encode(), salt=bytes.fromhex(rec["salt"]),
                            n=rec.get("n", 16384), r=rec.get("r", 8),
                            p=rec.get("p", 1), dklen=32)
        return secrets.compare_digest(dk.hex(), rec["hash"])
    except Exception:
        return False


# --- QR encoder, byte mode, EC level M, versions 1-10, stdlib only ---------
# Verified against the reference `qrcode` library and decoded with pyzbar
# across 490 payloads before shipping. Do not edit casually.
# total codewords, then (ec_per_block, [(nblocks, data_cw), ...]) for level M
# The QR encoder lives in xxops_qr.py - 307 lines of arithmetic that had no
# business sharing a file with request dispatch. Python puts this script's own
# directory on sys.path, so a sibling module needs no packaging.
from xxops_qr import qr_svg

def new_totp_secret():
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def totp_at(secret, when=None):
    when = time.time() if when is None else when
    key = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    mac = hmac.new(key, struct.pack(">Q", int(when // 30)), hashlib.sha1).digest()
    off = mac[-1] & 0x0F
    return str((struct.unpack(">I", mac[off:off + 4])[0] & 0x7FFFFFFF) % 1000000).zfill(6)


TOTP_DRIFT = (-3, -2, -1, 0, 1, 2, 3)


def check_totp(secret, given):
    given = re.sub(r"\s", "", str(given or ""))
    if not re.fullmatch(r"\d{6}", given):
        return False
    now = time.time()
    ok = False
    # three steps either way. His authenticator ran about a minute ahead of
    # the server across two incidents, which one step could not absorb.
    # Seven codes valid at once instead of three - acceptable behind a
    # password on a private network, and still a workaround for a clock.
    for drift in TOTP_DRIFT:
        if secrets.compare_digest(totp_at(secret, now + drift * 30), given):
            ok = True                 # no early return, so timing leaks nothing
    return ok


def new_recovery_codes(n=8):
    alphabet = "abcdefghjkmnpqrstuvwxyz23456789"      # no look-alikes
    out = []
    for _ in range(n):
        raw = "".join(secrets.choice(alphabet) for _ in range(10))
        out.append(raw[:5] + "-" + raw[5:])
    return out


def hash_recovery(code):
    return hashlib.sha256(code.replace("-", "").lower().encode()).hexdigest()


# --- sessions carry who you are --------------------------------------------
def load_sessions():
    try:
        with open(SESSIONS_FILE) as f:
            d = json.load(f)
        now = time.time()
        return {k: v for k, v in d.items()
                if isinstance(v, dict) and v.get("exp", 0) > now}
    except Exception:
        return {}


def save_sessions(d):
    tmp = SESSIONS_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(d, f)
    os.chmod(tmp, 0o600)
    os.replace(tmp, SESSIONS_FILE)


def new_session(username):
    tok = secrets.token_urlsafe(32)
    d = load_sessions()
    d[tok] = {"user": username, "exp": time.time() + SESSION_DAYS * 86400}
    save_sessions(d)
    return tok


VALID_USER = re.compile(r"[a-z0-9][a-z0-9_.-]{1,30}")

LOGIN_PAGE = _template("login.html")


# Fixes that ship with the software, so a new install is not an empty box.
# Read-only: writes never touch this file, only the operator's own.
BUNDLED_FIXES = os.environ.get(
    "XXOPS_BUNDLED_FIXES",
    os.path.join(os.path.dirname(os.path.abspath(__file__)),
                 "bundled-fixes.json"))


def load_bundled():
    try:
        with open(BUNDLED_FIXES) as f:
            d = json.load(f)
    except Exception:
        return []
    out = []
    for e in (d if isinstance(d, list) else []):
        if isinstance(e, dict) and e.get("id"):
            e = dict(e)
            e["bundled"] = True
            out.append(e)
    return out


def load_resolutions():
    """The operator's own entries, then whatever the bundle adds to them.

    An entry the operator wrote wins over a bundled one with the same id, so
    editing a shipped fix keeps the edit through an update. A tombstone
    suppresses a bundled entry they did not want.
    """
    try:
        with open(RESOLUTIONS) as f:
            d = json.load(f)
        local = d if isinstance(d, list) else []
    except FileNotFoundError:
        local = []
    tombs = {e.get("id") for e in local if e.get("deleted")}
    live = [e for e in local if not e.get("deleted")]
    seen = {e.get("id") for e in live}
    return live + [e for e in load_bundled()
                   if e.get("id") not in seen and e.get("id") not in tombs]

def save_resolutions(entries):
    # Never write the bundled ones back: they would then be the operator's
    # copies, and an update could no longer refresh them. Tombstones DO get
    # written - suppressing a bundled entry is the operator's decision and has
    # to survive a restart.
    entries = [e for e in entries if not e.get("bundled")]
    # Tombstones are never shown, so a caller's list never contains them.
    # Carry them across or deleting anything at all would resurrect every
    # bundled entry the operator had previously suppressed.
    try:
        with open(RESOLUTIONS) as f:
            prev = json.load(f)
        prev = prev if isinstance(prev, list) else []
    except Exception:
        prev = []
    have = {e.get("id") for e in entries}
    entries = entries + [e for e in prev
                         if e.get("deleted") and e.get("id") not in have]
    tmp = RESOLUTIONS + ".tmp"
    with open(tmp, "w") as f:
        json.dump(entries, f, indent=1)
    os.replace(tmp, RESOLUTIONS)

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

APP_DIR   = os.environ.get("XXOPS_APP_DIR", os.path.dirname(os.path.abspath(__file__)))
STATE_DIR = os.environ.get("XXOPS_STATE_DIR", "/var/lib/xxops")
AM_CONFIG = os.environ.get("XXOPS_AM_CONFIG", "/etc/alertmanager/alertmanager.yml")
AM_URL    = os.environ.get("XXOPS_AM_URL", "http://127.0.0.1:9093")
BIND      = os.environ.get("XXOPS_BIND", "127.0.0.1")
PORT      = int(os.environ.get("XXOPS_PORT", "8080"))
TLS_CERT  = os.environ.get("XXOPS_TLS_CERT", "")
TLS_KEY   = os.environ.get("XXOPS_TLS_KEY", "")
PROM_URL  = os.environ.get("XXOPS_PROM_URL", "http://127.0.0.1:9090")
AMTOOL    = os.environ.get("XXOPS_AMTOOL", "/usr/local/bin/amtool")

SETTINGS = os.path.join(STATE_DIR, "settings.json")
NOTIFY   = os.path.join(STATE_DIR, "notify.json")
MASK     = "········"

DEFAULT_NOTIFY = {
    "telegram": {"bot_token": ""},
    "smtp": {"host": "", "port": 587, "from": "", "username": "", "password": ""},
    "fallback": {"telegram_chat_id": "", "emails": ""},
    "contacts": [],   # {id,name,emails,telegram_chat_id,webhook,validators:[node,…]}
}

# ---------------------------------------------------------------- storage

def _read(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return json.loads(json.dumps(default))

def _write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path))
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=2)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)

def import_existing():
    """First run: adopt whatever alertmanager.yml already has, so a hand-written
    config is never replaced by an empty one."""
    n = json.loads(json.dumps(DEFAULT_NOTIFY))
    try:
        with open(AM_CONFIG) as f:
            cfg = f.read()
    except Exception:
        return n

    def grab(pat):
        m = re.search(pat, cfg, re.M)
        return m.group(1).strip().strip("'\"") if m else ""

    n["telegram"]["bot_token"] = grab(r"bot_token:\s*(\S+)")
    n["fallback"]["telegram_chat_id"] = grab(r"chat_id:\s*(-?\d+)")
    n["fallback"]["emails"] = grab(r"^\s+-?\s*to:\s*(.+)$")
    host = grab(r"smtp_smarthost:\s*(\S+)")
    if host:
        bits = host.split(":")
        n["smtp"]["host"] = bits[0]
        if len(bits) > 1 and bits[1].isdigit():
            n["smtp"]["port"] = int(bits[1])
    n["smtp"]["from"] = grab(r"smtp_from:\s*(\S+)")
    n["smtp"]["username"] = grab(r"smtp_auth_username:\s*(\S+)")
    n["smtp"]["password"] = grab(r"smtp_auth_password:\s*(\S+)")
    return n


def load_notify():
    if not os.path.exists(NOTIFY):
        n = import_existing()
        if n["telegram"].get("bot_token") or n["smtp"].get("host") \
           or n["fallback"].get("telegram_chat_id"):
            _write(NOTIFY, n)
        return n

    n = _read(NOTIFY, DEFAULT_NOTIFY)
    for k, v in DEFAULT_NOTIFY.items():
        n.setdefault(k, json.loads(json.dumps(v)))
    return n

def masked(n):
    """A copy safe to send to the browser."""
    out = json.loads(json.dumps(n))
    if out["telegram"].get("bot_token"):
        out["telegram"]["bot_token"] = MASK
    if out["smtp"].get("password"):
        out["smtp"]["password"] = MASK
    return out

def unmask(incoming, current):
    """Keep the stored secret wherever the browser sent back the mask."""
    if incoming.get("telegram", {}).get("bot_token") == MASK:
        incoming["telegram"]["bot_token"] = current["telegram"].get("bot_token", "")
    if incoming.get("smtp", {}).get("password") == MASK:
        incoming["smtp"]["password"] = current["smtp"].get("password", "")
    return incoming

# ------------------------------------------------------- config generation

# Alertmanager config building lives in xxops_amconfig.py: settings
# in, YAML out, with no idea where anything is stored.
from xxops_amconfig import build_config, chat_id, problems

def validate(text):
    fd, tmp = tempfile.mkstemp(suffix=".yml")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
        r = subprocess.run([AMTOOL, "check-config", tmp],
                           capture_output=True, text=True, timeout=20)
        return r.returncode == 0, (r.stdout + r.stderr).strip()
    except Exception as e:
        return False, f"could not run amtool: {e}"
    finally:
        try: os.unlink(tmp)
        except Exception: pass

def apply_config(text):
    ok, msg = validate(text)
    if not ok:
        return False, "Alertmanager rejected that config, so nothing was changed.\n\n" + msg
    try:
        if os.path.exists(AM_CONFIG):
            with open(AM_CONFIG) as f:
                old = f.read()
            with open(AM_CONFIG + ".bak", "w") as f:
                f.write(old)
        with open(AM_CONFIG, "w") as f:
            f.write(text)
    except PermissionError:
        return False, f"No permission to write {AM_CONFIG}. Check ownership."
    try:
        req = urllib.request.Request(AM_URL.rstrip("/") + "/-/reload", data=b"", method="POST")
        with urllib.request.urlopen(req, timeout=20) as r:
            r.read()
    except Exception as e:
        return False, ("Config was written but Alertmanager wouldn't reload it: "
                       f"{e}\nThe previous config is still at {AM_CONFIG}.bak")
    return True, "Saved and reloaded."

# ------------------------------------------------------------- telegram

PAIRING = {"code": None, "started": 0}

def tg(token, method, params=None):
    url = f"https://api.telegram.org/bot{token}/{method}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=15) as r:
        return json.load(r)

def tg_send(t,c,x):
    try:
        urllib.request.urlopen(urllib.request.Request("https://api.telegram.org/bot%s/sendMessage" % t, data=urllib.parse.urlencode({"chat_id":c,"text":x,"parse_mode":"HTML"}).encode(), method="POST"), timeout=15).read()
        return True
    except Exception:
        return False

def pair_start(n):
    if not n["telegram"].get("bot_token"):
        return {"ok": False, "error": "Add the bot token first."}
    code = "xx-" + "".join(secrets.choice(string.digits) for _ in range(4))
    PAIRING.update(code=code, started=time.time(), offset=0)
    try:  # drain anything already queued so old messages can't match
        d = tg(n["telegram"]["bot_token"], "getUpdates", {"timeout": 0})
        if d.get("result"):
            PAIRING["offset"] = d["result"][-1]["update_id"] + 1
    except Exception:
        pass
    return {"ok": True, "code": code}

def pair_check(n):
    if not PAIRING.get("code"):
        return {"ok": False, "error": "Pairing hasn't been started."}
    try:
        d = tg(n["telegram"]["bot_token"], "getUpdates",
               {"timeout": 0, "offset": PAIRING.get("offset", 0)})
    except Exception as e:
        return {"ok": False, "error": str(e)}
    for u in d.get("result", []):
        PAIRING["offset"] = u["update_id"] + 1
        msg = u.get("message") or u.get("channel_post") or {}
        if PAIRING["code"].lower() in str(msg.get("text","")).lower() or PAIRING["code"].split("-")[-1] in str(msg.get("text","")):
            frm = msg.get("from", {}) or {}
            tg_send(n["telegram"]["bot_token"], msg.get("chat", {}).get("id"), "\u2705 <b>Connected to xxOps</b>\nYou are now linked. If a validator you look after needs attention, the alert arrives here.\n\nNothing else to do — you can close this chat.")
            return {"ok": True, "found": True,
                    "chat_id": msg.get("chat", {}).get("id"),
                    "name": (frm.get("first_name", "") + " " + frm.get("last_name", "")).strip()
                            or frm.get("username", "")}
    return {"ok": True, "found": False}

# ---------------------------------------------------------------- http

class H(BaseHTTPRequestHandler):
    server_version = "xxops"

    def log_message(self, *a):
        pass  # journald already has what matters

    def _authed(self):
        if needs_setup():
            return False
        if not auth_required():
            return True
        raw = self.headers.get("Cookie")
        if not raw:
            return False
        try:
            c = http.cookies.SimpleCookie(raw)
        except Exception:
            return False
        tok = c["xxops_session"].value if "xxops_session" in c else None
        return bool(tok) and tok in load_sessions()

    def _whoami(self):
        raw = self.headers.get("Cookie")
        if not raw:
            return None
        try:
            c = http.cookies.SimpleCookie(raw)
            tok = c["xxops_session"].value if "xxops_session" in c else None
        except Exception:
            return None
        sess = load_sessions().get(tok or "")
        if not sess:
            return None
        u = load_users()["users"].get(sess.get("user"))
        return dict(u, username=sess["user"]) if u else None

    def _send(self, code, body, ctype="application/json"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    # Route table. A path in here is handled by the named method; anything not
    # in it falls through to the chains below, so routes can move a few at a
    # time. The point is that a route's METHOD, PATH and HANDLER are stated in
    # one place instead of being implied by where an `if` happens to sit.
    ROUTES = {
        ("GET",  "/api/agent/hosts"): "r_agent_hosts",
        ("POST", "/api/agent/hosts"): "r_agent_hosts",
        ("GET",  "/xxops.css"):       "r_static",
        ("GET",  "/xxops-data.js"):   "r_static",
        ("GET",  "/xxops-views.js"):  "r_static",
        ("GET",  "/xxops-settings.js"): "r_static",
        ("GET",  "/xxops-commands.js"): "r_static",
        ("GET",  "/xxops-search.js"): "r_static",
        ("GET",  "/xxops-am.js"):     "r_static",
        ("GET",  "/xxops-util.js"):   "r_static",
    }

    # What may be served from APP_DIR, and as what. An allowlist rather than a
    # directory: this process holds session cookies, and "serve any file under
    # here" is how traversal bugs happen. A name is in this dict or it is not
    # served - there is nothing to traverse.
    STATIC = {
        "xxops.css": "text/css; charset=utf-8",
        "xxops-data.js": "application/javascript; charset=utf-8",
        "xxops-views.js": "application/javascript; charset=utf-8",
        "xxops-settings.js": "application/javascript; charset=utf-8",
        "xxops-commands.js": "application/javascript; charset=utf-8",
        "xxops-search.js": "application/javascript; charset=utf-8",
        "xxops-am.js": "application/javascript; charset=utf-8",
        "xxops-util.js": "application/javascript; charset=utf-8",
    }

    def _routed(self, method, p):
        """True if a table entry handled this request."""
        name = self.ROUTES.get((method, p))
        if not name:
            return False
        getattr(self, name)()
        return True

    def r_static(self):
        """A file from the allowlist. No session needed - a stylesheet says
        nothing, and the sign-in page wants it too."""
        name = urllib.parse.urlparse(self.path).path.lstrip("/")
        ctype = self.STATIC.get(name)
        if not ctype:
            return self._send(404, {"ok": False, "message": "no"})
        try:
            with open(os.path.join(APP_DIR, name), "rb") as f:
                data = f.read()
        except OSError:
            return self._send(404, {"ok": False, "message": "no"})
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def r_agent_hosts(self):
        try:
            return self._send(200, {"ok": True,
                                    "hosts": json.load(open(AGENT_CACHE))})
        except Exception as e:
            return self._send(400, {"ok": False,
                                    "message": f"no agent list yet: {e}"})

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n))
        except Exception:
            return {}

    # ---- GET
    def _proxy(self, base, rest, query):
        """Forward one GET to Prometheus or Alertmanager and return the body."""
        if ".." in rest:
            return self._send(400, {"ok": False, "message": "no"})
        url = base.rstrip("/") + "/" + rest.lstrip("/") + (("?" + query) if query else "")
        try:
            with urllib.request.urlopen(url, timeout=45) as r:
                data = r.read()
                ctype = r.headers.get("Content-Type", "application/json")
            allowed = allowed_instances(self._whoami())
            if allowed is not None and "json" in ctype:
                # filter what comes back rather than rewriting the query -
                # a rewrite that misses is silent, a filter that misses is not
                try:
                    parsed = json.loads(data)
                    if isinstance(parsed, list):
                        parsed = scope_am(parsed, allowed)
                    else:
                        parsed = scope_prom(parsed, allowed)
                    data = json.dumps(parsed).encode()
                except Exception:
                    data = json.dumps({"status": "success",
                                       "data": {"resultType": "vector",
                                                "result": []}}).encode()
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except urllib.error.HTTPError as e:
            body = e.read()
            self.send_response(e.code)
            self.send_header("Content-Type", e.headers.get("Content-Type", "application/json"))
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except Exception as e:
            self._send(502, {"ok": False, "message": f"upstream did not answer: {e}"})

    def do_GET(self):
        try:
            return self._get()
        except Exception as e:
            return self._send(500, {"ok": False, "message": f"{type(e).__name__}: {e}"})

    def _get(self):
        p = urllib.parse.urlparse(self.path).path
        # deliberately readable without a login: neither is sensitive, and the
        # icon has to load on the sign-in screen itself.
        if p == "/manifest.webmanifest":
            return self._send(200, json.dumps({
                "name": "xxOps", "short_name": "xxOps",
                "description": "Monitoring for xx Network validators",
                "start_url": "/", "scope": "/",
                "display": "standalone", "orientation": "any",
                "background_color": "#0d1017", "theme_color": "#0d1017",
                "icons": [{"src": "/icon.png", "sizes": "512x512",
                           "type": "image/png", "purpose": "any maskable"}]
            }), "application/manifest+json")

        # The agent installer fetches these before the operator has an
        # account, so they cannot sit behind a login. None is a secret:
        # allowed_signers holds a PUBLIC key -- its private half lives in
        # /etc/xxops/cmd_key and is never served -- and the other three are
        # already public on GitHub.
        #
        # The name is matched against this fixed set, so the caller controls
        # no part of the path and this cannot be walked anywhere else.
        if p.startswith("/agent/"):
            name = p[len("/agent/"):]
            if name not in ("allowed_signers", "xxops-agent.py",
                            "xxops-update-node.sh", "xxops-update-gateway.sh"):
                return self._send(404, "no such file", "text/plain")
            src = ("/etc/xxops/allowed_signers" if name == "allowed_signers"
                   else os.path.join(APP_DIR, name))
            try:
                with open(src, "rb") as f:
                    data = f.read()
            except OSError:
                return self._send(404, "not installed on this monitor",
                                  "text/plain")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return

        if p == "/icon.png":
            try:
                with open(os.path.join(APP_DIR, "icon.png"), "rb") as f:
                    data = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Cache-Control", "public, max-age=86400")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
            except FileNotFoundError:
                return self._send(404, "no icon", "text/plain")

        if p.startswith("/prom/") or p.startswith("/am/"):
            if not self._authed():
                return self._send(401, {"ok": False, "message": "sign in first"})
            q = urllib.parse.urlparse(self.path).query
            if p.startswith("/prom/"):
                return self._proxy(PROM_URL, p[len("/prom/"):], q)
            return self._proxy(AM_URL, p[len("/am/"):], q)

        if p == "/docs" or p.startswith("/docs/"):
            if auth_required() and not self._whoami():
                return self._send(401, {"ok": False, "message": "sign in first"})
            name = p[len("/docs/"):] if p.startswith("/docs/") else ""
            if not name:
                try:
                    files = sorted(f for f in os.listdir(docs_dir())
                                   if DOC_NAME.fullmatch(f))
                except Exception:
                    files = []
                if not files:
                    body = ("<a class=\"back\" href=\"/\">&#8592; xxOps</a><h1>Documentation</h1><p>Nothing here yet. "
                            "Documents are read from <code>" + md_escape(docs_dir()) +
                            "</code>.</p>")
                else:
                    body = "<a class=\"back\" href=\"/\">&#8592; xxOps</a><h1>Documentation</h1><ul class=\"doclist\">" + "".join(
                        '<li><a href="/docs/%s">%s</a></li>'
                        % (f, md_escape(f[:-3].replace("-", " ").replace("_", " ")))
                        for f in files) + "</ul>"
                return self._send(200, doc_page("Documentation", body),
                                  "text/html; charset=utf-8")
            # the name is matched whole against a strict pattern, so no
            # slashes and no traversal - the docs dir is expected to be a
            # symlink, which is where a sloppy join would become a file read
            if not DOC_NAME.fullmatch(name):
                return self._send(404, "no such document", "text/plain")
            try:
                with open(os.path.join(docs_dir(), name), encoding="utf-8") as f:
                    src = f.read()
            except Exception:
                return self._send(404, "no such document", "text/plain")
            body = ('<a class="back" href="/">\u2190 xxOps</a>'
                    '<a class="back" href="/docs" style="margin-left:14px">all documents</a>'
                    + md_to_html(src))
            return self._send(200, doc_page(name[:-3], body),
                              "text/html; charset=utf-8")

        if p == "/redeem":
            return self._send(200, REDEEM_PAGE, "text/html; charset=utf-8")

        if p in ("/", "/index.html", "/xxops.html") and not self._authed():
            return self._send(200, LOGIN_PAGE, "text/html; charset=utf-8")

        if p in ("/", "/index.html", "/xxops.html"):
            try:
                with open(os.path.join(APP_DIR, "xxops.html"), "rb") as f:
                    return self._send(200, f.read().decode(), "text/html; charset=utf-8")
            except FileNotFoundError:
                return self._send(404, "xxops.html not found", "text/plain")
        # the GET side of the api is gated here. _gate() only covers _post,
        # so without this every /api/ GET answered anyone on the tailnet.
        # enforced only when auth is on, so a fresh install can still set up.
        if (auth_required() and p.startswith("/api/")
                and not p.startswith("/api/auth/")
                and p not in ("/api/health", "/api/version")):
            _me = self._whoami()
            if not _me:
                return self._send(401, {"ok": False, "message": "sign in first"})
            if (p in ("/api/notify", "/api/telegram/pair",
                      "/api/agent/hosts")
                    and _me.get("role") != "owner"):
                return self._send(403, {"ok": False,
                                        "message": "that is not yours to see"})

        if self._routed("GET", p):
            return

        # read endpoints, reachable with GET as well as POST. the POST forms
        # stay for now so nothing that still uses them breaks.
        if p == "/api/resolutions":
            try:
                return self._send(200, {"ok": True, "entries": load_resolutions()})
            except Exception as e:
                return self._send(400, {"ok": False, "message": str(e)})

        if p == "/api/settings":
            cfg = _read(SETTINGS, {}) or {}
            allowed = allowed_instances(self._whoami())
            if allowed is not None:
                # a contact needs cfg for the app to render, but not the map
                # of everyone else's machines
                cfg = dict(cfg)
                cfg["pairs"] = {n: g for n, g in (cfg.get("pairs") or {}).items()
                                if n in allowed}
                cfg.pop("ignore", None)
            return self._send(200, cfg)
        if p == "/api/notify":
            n = masked(load_notify())
            n["_amtool"] = os.path.exists(AMTOOL)
            return self._send(200, n)
        if p == "/api/telegram/pair":
            return self._send(200, pair_check(load_notify()))
        if p == "/api/version":
            try:
                with open(os.path.join(APP_DIR, "xxops.html"), "rb") as f:
                    b = f.read()
                mv = re.search(rb"VERSION=\"(v[0-9.]+)\"", b)
                return self._send(200, {"hash": hashlib.md5(b).hexdigest()[:12],
                                        "version": mv.group(1).decode() if mv else "?"})
            except Exception:
                return self._send(200, {"hash": "", "version": "?"})
        if p == "/api/health":
            # unauthenticated by design, so it says nothing but "alive"
            return self._send(200, {"ok": True})
        return self._send(404, {"error": "no such endpoint"})

    # ---- POST
    OWNER_ONLY = ("/api/settings", "/api/notify", "/api/notify/preview",
                  "/api/notify/test", "/api/telegram/pair",
                  "/api/agent/hosts", "/api/agent/actions", "/api/agent/run")

    def _gate(self, p):
        """Returns True if this request should be refused."""
        if p.startswith("/api/auth/"):
            return False
        me = self._whoami()
        if p in self.OWNER_ONLY and me and me.get("role") != "owner":
            self._send(403, {"ok": False,
                             "message": "that is not yours to change"})
            return True
        if self._authed():
            return False
        self._send(401, {"ok": False, "message": "sign in first"})
        return True

    def do_POST(self):
        try:
            return self._post()
        except Exception as e:
            return self._send(500, {"ok": False, "message": f"{type(e).__name__}: {e}"})

    def _post(self):
        p = urllib.parse.urlparse(self.path).path
        body = self._body()
      
        if self._gate(p):
            return

        if self._routed("POST", p):
            return

        if p == "/api/settings":
            _write(SETTINGS, body)
            return self._send(200, {"ok": True})

        if p == "/api/notify":
            cur = load_notify()
            new = unmask(body, cur)
            for k, v in DEFAULT_NOTIFY.items():
                new.setdefault(k, json.loads(json.dumps(v)))
            bad = problems(new)
            if bad:
                return self._send(400, {"ok": False, "message": "\n".join(bad)})
            pairs = (_read(SETTINGS, {}) or {}).get("pairs") or {}
            ok, msg = apply_config(build_config(new, pairs))
            if ok:
                _write(NOTIFY, new)
                _old = {c.get("id"): c for c in cur.get("contacts", [])}
                for c in new.get("contacts", []):
                    cid = chat_id(c.get("telegram_chat_id"))
                    if not cid:
                        continue
                    was = sorted((_old.get(c.get("id")) or {}).get("validators") or [])
                    now_ = sorted(c.get("validators") or [])
                    if was == now_:
                        continue
                    if now_:
                        txt = "\U0001F514 <b>xxOps</b>\nYou will now be alerted about: <b>" + ", ".join(now_) + "</b>"
                    else:
                        txt = "\U0001F515 <b>xxOps</b>\nYou are no longer set to receive alerts for any validator."
                    tg_send(new["telegram"]["bot_token"], cid, txt)
            return self._send(200 if ok else 400, {"ok": ok, "message": msg})

        if p == "/api/notify/preview":
            cur = load_notify()
            new = unmask(body or json.loads(json.dumps(cur)), cur)
            pairs = (_read(SETTINGS, {}) or {}).get("pairs") or {}
            text = build_config(new, pairs)
            ok, msg = validate(text)
            return self._send(200, {"ok": ok, "message": msg, "config": text})

        if p == "/api/notify/test":
            who = body.get("contact")
            alert = [{
                "labels": {"alertname": "xxOpsTest", "severity": "red",
                           "instance": body.get("instance") or "xxops"},
                "annotations": {"summary": "Test alert from xxOps",
                                "detail": "If you're reading this, alerting works."},
                "startsAt": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + "Z",
            }]
            try:
                req = urllib.request.Request(
                    AM_URL.rstrip("/") + "/api/v2/alerts",
                    data=json.dumps(alert).encode(),
                    headers={"Content-Type": "application/json"}, method="POST")
                urllib.request.urlopen(req, timeout=15).read()
                return self._send(200, {"ok": True,
                    "message": "Sent. It should arrive within about a minute."})
            except Exception as e:
                return self._send(400, {"ok": False, "message": str(e)})
        if p == "/api/auth/state":
            me = self._whoami()
            return self._send(200, {"ok": True, "needsSetup": needs_setup(),
                                    "authRequired": auth_required(),
                                    "user": me and me["username"],
                                    "role": me and me.get("role"),
                                    "totp": bool(me and me.get("totp"))})

        if p == "/api/auth/setup":
            if not needs_setup():
                return self._send(400, {"ok": False, "message": "an account already exists"})
            u = str(body.get("username", "")).strip().lower()
            pw = str(body.get("password", ""))
            if not VALID_USER.fullmatch(u):
                return self._send(400, {"ok": False, "message":
                    "Username: lowercase letters, numbers, dot, dash or underscore."})
            if len(pw) < 8:
                return self._send(400, {"ok": False, "message": "Use at least 8 characters."})
            store = load_users()
            store["users"][u] = {"role": "owner", "pw": hash_password(pw),
                                 "totp": None, "recovery": [], "created": int(time.time())}
            save_users(store)
            tok = new_session(u)
            b = json.dumps({"ok": True}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Set-Cookie",
                             f"xxops_session={tok}; Path=/; HttpOnly; SameSite=Strict; "
                             f"Max-Age={SESSION_DAYS*86400}")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)
            return

        if p == "/api/auth/login":
            if needs_setup():
                return self._send(400, {"ok": False, "message": "no account exists yet"})
            u = str(body.get("username", "")).strip().lower()
            wait = login_locked(u)
            if wait:
                return self._send(429, {"ok": False,
                    "message": "Too many attempts. Try again in %d minute%s."
                               % (max(1, wait // 60),
                                  "" if wait // 60 == 1 else "s")})
            store = load_users()
            rec = store["users"].get(u)
            # check the password even for an unknown user, so the response time
            # does not reveal which usernames exist
            ok = check_password(str(body.get("password", "")),
                                rec["pw"] if rec else {"salt": "00" * 16,
                                                       "hash": "ff" * 32})
            if not rec or not ok:
                login_failed(u)
                return self._send(401, {"ok": False, "message": "That did not work."})
            if rec.get("totp"):
                given = body.get("totp")
                recovery = str(body.get("recovery", "")).strip()
                if recovery:
                    h = hash_recovery(recovery)
                    left = [x for x in rec.get("recovery", [])
                            if not secrets.compare_digest(x, h)]
                    if len(left) == len(rec.get("recovery", [])):
                        login_failed(u)
                        return self._send(401, {"ok": False, "message": "That code is not valid."})
                    rec["recovery"] = left
                    save_users(store)
                elif given is None:
                    return self._send(200, {"ok": False, "needsTotp": True})
                elif not check_totp(rec["totp"], given):
                    login_failed(u)
                    return self._send(401, {"ok": False, "needsTotp": True,
                                            "message": "That code is not right."})
            login_ok(u)
            tok = new_session(u)
            b = json.dumps({"ok": True}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Set-Cookie",
                             f"xxops_session={tok}; Path=/; HttpOnly; SameSite=Strict; "
                             f"Max-Age={SESSION_DAYS*86400}")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)
            return

        if p == "/api/auth/logout":
            raw = self.headers.get("Cookie") or ""
            try:
                c = http.cookies.SimpleCookie(raw)
                tok = c["xxops_session"].value if "xxops_session" in c else None
                if tok:
                    d = load_sessions(); d.pop(tok, None); save_sessions(d)
            except Exception:
                pass
            return self._send(200, {"ok": True})

        if p in ("/api/auth/password", "/api/auth/totp/start",
                 "/api/auth/totp/confirm", "/api/auth/totp/disable"):
            me = self._whoami()
            if not me:
                return self._send(401, {"ok": False, "message": "sign in first"})
            store = load_users()
            rec = store["users"].get(me["username"])
            if not rec:
                return self._send(401, {"ok": False, "message": "sign in first"})

            if p == "/api/auth/password":
                if not check_password(str(body.get("current", "")), rec["pw"]):
                    return self._send(401, {"ok": False,
                        "message": "That is not your current password."})
                new = str(body.get("new", ""))
                if len(new) < 8:
                    return self._send(400, {"ok": False,
                        "message": "Use at least 8 characters."})
                rec["pw"] = hash_password(new)
                save_users(store)
                return self._send(200, {"ok": True, "message": "Password changed."})

            if p == "/api/auth/totp/start":
                if rec.get("totp"):
                    return self._send(400, {"ok": False,
                        "message": "Two-factor is already on. Turn it off first."})
                secret = rec.get("totpPending")
                if not secret:
                    # reuse a pending key, so reopening this view does
                    # not invalidate what the authenticator already holds
                    secret = new_totp_secret()
                    rec["totpPending"] = secret
                    save_users(store)
                label = urllib.parse.quote("xxOps:" + me["username"])
                uri = ("otpauth://totp/" + label + "?secret=" + secret +
                       "&issuer=xxOps&algorithm=SHA1&digits=6&period=30")
                return self._send(200, {"ok": True, "secret": secret,
                                        "uri": uri, "svg": qr_svg(uri)})

            if p == "/api/auth/totp/confirm":
                secret = rec.get("totpPending")
                if not secret:
                    return self._send(400, {"ok": False,
                        "message": "Start the setup again."})
                if not check_totp(secret, str(body.get("code", "")).strip()):
                    return self._send(400, {"ok": False,
                        "message": "That code is not right."})
                alpha = "abcdefghjkmnpqrstuvwxyz23456789"
                codes = ["".join(secrets.choice(alpha) for _ in range(5)) + "-" +
                         "".join(secrets.choice(alpha) for _ in range(5))
                         for _ in range(10)]
                rec["totp"] = secret
                rec.pop("totpPending", None)
                rec["recovery"] = [hash_recovery(c) for c in codes]
                save_users(store)
                return self._send(200, {"ok": True, "recovery": codes})

            if p == "/api/auth/totp/disable":
                if not check_password(str(body.get("password", "")), rec["pw"]):
                    return self._send(401, {"ok": False,
                        "message": "That password is not right."})
                rec["totp"] = None
                rec["recovery"] = []
                rec.pop("totpPending", None)
                save_users(store)
                return self._send(200, {"ok": True, "message": "Two-factor is off."})

        if p == "/api/resolutions":
            try:
                return self._send(200, {"ok": True, "entries": load_resolutions()})
            except Exception as e:
                return self._send(400, {"ok": False, "message": str(e)})

        if p == "/api/resolutions/add":
            e = {}
            for f in ("title", "symptom", "diagnosis", "fix", "host", "alertname"):
                e[f] = str(body.get(f, ""))[:4000]
            if not e["title"].strip():
                return self._send(400, {"ok": False, "message": "give it a title at least"})
            e["tags"] = [str(t)[:40] for t in (body.get("tags") or [])][:8]
            e["id"] = secrets.token_hex(8)
            e["created"] = int(time.time())
            entries = load_resolutions()
            entries.insert(0, e)
            save_resolutions(entries[:500])
            return self._send(200, {"ok": True, "entry": e})

        if p == "/api/resolutions/edit":
            rid = str(body.get("id", ""))
            current = load_resolutions()
            match = [x for x in current if x.get("id") == rid]
            if not match:
                return self._send(404, {"ok": False,
                                        "message": "no such entry"})
            old = match[0]
            e = {}
            for f in ("title", "symptom", "diagnosis", "fix",
                      "host", "alertname"):
                e[f] = str(body.get(f, ""))[:4000]
            if not e["title"].strip():
                return self._send(400, {"ok": False,
                                        "message": "give it a title at least"})
            e["tags"] = [str(t)[:40] for t in (body.get("tags") or [])][:8]
            e["id"] = rid
            e["created"] = int(old.get("created") or time.time())
            e["edited"] = int(time.time())
            # A bundled entry is not in the operator's file, so there is
            # nothing to update in place. Write their own copy under the same
            # id and let the existing merge prefer it. From here on they stop
            # receiving updates to that entry, which is what overriding means.
            others = [x for x in load_resolutions()
                      if x.get("id") != rid and not x.get("bundled")]
            others.insert(0, e)
            save_resolutions(others[:500])
            return self._send(200, {"ok": True, "entry": e})

        if p == "/api/resolutions/delete":
            rid = str(body.get("id", ""))
            current = load_resolutions()
            removed = [x for x in current if x.get("id") == rid]
            entries = [x for x in current if x.get("id") != rid]
            # a bundled entry does not live in the operator's file, so removing
            # it from the list achieves nothing - record that they do not want
            # it instead
            if removed and removed[0].get("bundled"):
                entries.append({"id": rid, "deleted": True})
            save_resolutions(entries)
            return self._send(200, {"ok": True})

        if p == "/api/users/invite":
            me = self._whoami()
            if not me or me.get("role") != "owner":
                return self._send(403, {"ok": False,
                                        "message": "that is not yours to change"})
            cid = str(body.get("contactId", ""))
            contact = contact_by_id(cid)
            if not contact:
                return self._send(400, {"ok": False, "message": "no such contact"})

            store = prune_invites(load_users())
            for u in store.get("users", {}).values():
                if u.get("contactId") == cid:
                    return self._send(400, {"ok": False,
                        "message": "%s already has a login" % contact.get("name", "that contact")})
            for code, rec in list((store.get("invites") or {}).items()):
                if rec.get("contactId") == cid:
                    store["invites"].pop(code, None)   # replace, never accumulate

            code = new_invite_code()
            store.setdefault("invites", {})[code] = {
                "contactId": cid, "tries": 0,
                "expires": int(time.time()) + INVITE_TTL,
                "created": int(time.time())}
            save_users(store)

            sent = False
            chat = contact.get("telegram_chat_id")
            if chat:
                try:
                    n = load_notify()
                    token = (n.get("telegram") or {}).get("bot_token")
                    host = self.headers.get("Host") or ""
                    base = ("https://" + host) if host else ""
                    if token:
                        tg_send(token, chat,
                                "You have been invited to the xxOps app.\n\n"
                                "Code: <b>%s</b>\n\n"
                                "Open %s/redeem and choose your own username and "
                                "password. The code works once and expires in 24 "
                                "hours.\n\n"
                                "xxOps runs on a private network, so you can only "
                                "reach it from a device that has been given access. "
                                "If that link does not open, ask for the network "
                                "invitation as well \u2014 you will need both." % (code, base))
                        sent = True
                except Exception:
                    sent = False

            return self._send(200, {"ok": True, "code": code, "sent": sent,
                                    "name": contact.get("name", ""),
                                    "expiresIn": "24 hours",
                                    "needsNetwork": True})

        if p == "/api/auth/redeem":
            # reachable without a session by necessity - validate everything
            store = prune_invites(load_users())
            code, rec = find_invite(store, body.get("code"))
            if not rec:
                return self._send(400, {"ok": False, "message": "That code is not valid."})

            u = str(body.get("username", "")).strip().lower()
            pw = str(body.get("password", ""))
            if not VALID_USER.fullmatch(u):
                return self._send(400, {"ok": False, "message":
                    "Username: lowercase letters, numbers, dot, dash or underscore."})
            if u in store.get("users", {}):
                rec["tries"] = rec.get("tries", 0) + 1
                save_users(store)
                return self._send(400, {"ok": False, "message": "That username is taken."})
            if len(pw) < 8:
                rec["tries"] = rec.get("tries", 0) + 1
                save_users(store)
                return self._send(400, {"ok": False, "message": "Use at least 8 characters."})

            cid = rec.get("contactId")
            for existing in store.get("users", {}).values():
                if existing.get("contactId") == cid:
                    store["invites"].pop(code, None)
                    save_users(store)
                    return self._send(400, {"ok": False,
                                            "message": "That invitation has already been used."})

            store["users"][u] = {"role": "contact", "contactId": cid,
                                 "pw": hash_password(pw), "totp": None,
                                 "recovery": [], "created": int(time.time())}
            store["invites"].pop(code, None)
            save_users(store)

            tok = new_session(u)
            b = json.dumps({"ok": True}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Set-Cookie",
                             f"xxops_session={tok}; Path=/; HttpOnly; SameSite=Strict; "
                             f"Max-Age={SESSION_DAYS*86400}")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)
            return

        if p == "/api/agent/actions":
            host = str(body.get("host", ""))
            if not re.fullmatch(r"[A-Za-z0-9_.-]{1,40}", host):
                return self._send(400, {"ok": False, "message": "that is not a host name"})
            try:
                agents = json.load(open(AGENT_CACHE))
            except Exception:
                return self._send(400, {"ok": False, "message": "no agents discovered yet"})
            if host not in agents:
                return self._send(400, {"ok": False,
                                        "message": "no agent known for " + host})
            entry = agents[host]
            ip = entry if isinstance(entry, str) else entry.get("ip")
            # the agent ignores the action name on /actions; xxops-cmd passes
            # "disk" as a placeholder for the same reason
            ok, res = agent_request(ip, host, "disk", "/actions")
            if not ok:
                return self._send(400, {"ok": False, "message": res})
            return self._send(200, {"ok": True,
                                    "actions": (res or {}).get("actions", {})})

        if p == "/api/agent/run":
            host = str(body.get("host", ""))
            action = str(body.get("action", ""))
            if not re.fullmatch(r"[A-Za-z0-9_.-]{1,40}", host):
                return self._send(400, {"ok": False, "message": "that is not a host name"})
            if not re.fullmatch(r"[a-z][a-z-]{0,29}", action):
                return self._send(400, {"ok": False, "message": "that is not an action name"})
            try:
                agents = json.load(open(AGENT_CACHE))
            except Exception:
                return self._send(400, {"ok": False, "message": "no agents discovered yet"})
            if host not in agents:
                return self._send(400, {"ok": False, "message": f"no agent known for {host}"})
            entry = agents[host]
            ip = entry if isinstance(entry, str) else entry.get("ip")

            req_body = {"action": action, "host": host,
                        "nonce": secrets.token_hex(12),
                        "expires": int(time.time()) + 60}
            # only ever a real boolean, so a truthy string cannot arm an action
            if body.get("confirm") is True:
                req_body["confirm"] = True
            payload = json.dumps(req_body, sort_keys=True, separators=(",", ":"))
            tmp = None
            try:
                with tempfile.NamedTemporaryFile("w", delete=False) as f:
                    f.write(payload)
                    tmp = f.name
                r = subprocess.run(["ssh-keygen", "-Y", "sign", "-f", CMD_KEY,
                                    "-n", "xxops", tmp],
                                   capture_output=True, text=True, timeout=15)
                if r.returncode != 0:
                    return self._send(400, {"ok": False,
                                            "message": "could not sign: " + r.stderr.strip()})
                sig = open(tmp + ".sig").read()
            except Exception as e:
                return self._send(400, {"ok": False, "message": str(e)})
            finally:
                for f in (tmp, (tmp or "") + ".sig"):
                    try: os.unlink(f)
                    except Exception: pass

            try:
                req = urllib.request.Request(
                    f"http://{ip}:8181/run",
                    data=json.dumps({"payload": payload, "sig": sig}).encode(),
                    headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=45) as resp:
                    return self._send(200, {"ok": True, "result": json.load(resp)})
            except urllib.error.HTTPError as e:
                try:
                    return self._send(200, {"ok": True, "result": json.load(e)})
                except Exception:
                    return self._send(400, {"ok": False, "message": f"agent returned HTTP {e.code}"})
            except Exception as e:
                return self._send(400, {"ok": False, "message": f"could not reach the agent: {e}"})


        if p == "/api/silence/create":
            # same reason as expire below: keep Alertmanager calls server side
            # so the browser never has to satisfy a CORS preflight.
            try:
                req = urllib.request.Request(
                    AM_URL.rstrip("/") + "/api/v2/silences",
                    data=json.dumps(body).encode(),
                    headers={"Content-Type": "application/json"}, method="POST")
                out = urllib.request.urlopen(req, timeout=15).read()
                return self._send(200, {"ok": True, "result": json.loads(out)})
            except Exception as e:
                return self._send(400, {"ok": False, "message": str(e)})

        if p == "/api/silence/expire":
            sid = str(body.get("id", ""))
            if not re.fullmatch(r"[0-9a-fA-F-]{36}", sid):
                return self._send(400, {"ok": False, "message": "that does not look like a silence id"})
            try:
                req = urllib.request.Request(
                    AM_URL.rstrip("/") + "/api/v2/silence/" + sid, method="DELETE")
                urllib.request.urlopen(req, timeout=15).read()
                return self._send(200, {"ok": True, "message": "unmuted"})
            except Exception as e:
                return self._send(400, {"ok": False, "message": str(e)})

        if p == "/api/telegram/pair":
            return self._send(200, pair_start(load_notify()))

        return self._send(404, {"error": "no such endpoint"})


def main():
    os.makedirs(STATE_DIR, exist_ok=True)
    srv = ThreadingHTTPServer((BIND, PORT), H)
    scheme = "http"
    if TLS_CERT and TLS_KEY:
        try:
            import ssl
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain(TLS_CERT, TLS_KEY)
            srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
            scheme = "https"
        except Exception as e:
            # a missing or expired certificate must not take monitoring offline
            print(f"TLS not enabled ({e}) - serving plain http", flush=True)
    print(f"xxops serving {APP_DIR} on {scheme}://{BIND}:{PORT}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()

