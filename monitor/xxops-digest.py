#!/usr/bin/env python3
"""One Telegram message a day: is the fleet fine, and did the backup run."""
import glob
import json
import os
import time
import urllib.parse
import urllib.request

NOTIFY = "/var/lib/xxops/notify.json"
PROM = "http://__MONITOR_IP__:9090"
BACKUP_DIR = "/var/lib/xxops-backup"
EXPECTED_HOSTS = 46


def q(expr):
    url = PROM + "/api/v1/query?query=" + urllib.parse.quote(expr)
    with urllib.request.urlopen(url, timeout=20) as r:
        res = json.load(r)["data"]["result"]
    return float(res[0]["value"][1]) if res else None


def firing():
    with urllib.request.urlopen(PROM + "/api/v1/alerts", timeout=20) as r:
        alerts = json.load(r)["data"]["alerts"]
    out = []
    for a in alerts:
        if a.get("state") != "firing":
            continue
        lab = a.get("labels", {})
        out.append((lab.get("severity", "?"), lab.get("alertname", "?"),
                    lab.get("instance", "")))
    return out


def human_bytes(n):
    if n is None:
        return "?"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return "%.0f %s" % (n, unit) if unit != "GB" else "%.1f GB" % n
        n /= 1024.0
    return "%.1f PB" % n


def q_series(expr):
    """Every series, not just the first value. q() returns one float, which
    is no use for a per-instance metric carrying a mode label."""
    url = PROM + "/api/v1/query?query=" + urllib.parse.quote(expr)
    with urllib.request.urlopen(url, timeout=20) as r:
        return json.load(r)["data"]["result"]


def linkspeed_lines():
    """One line per node, slowest first, both modes side by side.

    A relayed capacity reading measures a Tailscale DERP server rather than
    the link and caps around 20-30 Mbps, so it is shown as "relay" instead
    of a number that would read as a fault.
    """
    try:
        hosts = {}
        for metric, key in (("xx_linkspeed_up_mbps", "up"),
                            ("xx_linkspeed_down_mbps", "down"),
                            ("xx_linkspeed_path_direct", "direct")):
            for s in q_series(metric):
                inst = s["metric"].get("instance")
                mode = s["metric"].get("mode")
                if not inst or not mode:
                    continue
                hosts.setdefault(inst, {}).setdefault(mode, {})[key] = \
                    float(s["value"][1])
    except Exception as e:
        return ["link: could not be read (%s)" % e]

    if not hosts:
        return []

    def cap_speed(d):
        c = d.get("capacity")
        if not c or c.get("up") is None:
            return None
        if c.get("direct") == 0:
            return None          # relayed: not a measurement of the link
        return c["up"]

    measured = sum(1 for d in hosts.values() if cap_speed(d) is not None)
    out = ["link: %d of %d measured" % (measured, len(hosts))]

    def pair(d, mode):
        m = d.get(mode) or {}
        if m.get("up") is None or m.get("down") is None:
            return "     -"
        return "%4.0f/%-4.0f" % (m["up"], m["down"])

    # Unmeasured last: they carry no number to rank on, and putting them at
    # the top would bury the slowest real link under them.
    for name in sorted(hosts, key=lambda n: (cap_speed(hosts[n]) is None,
                                             cap_speed(hosts[n]) or 0)):
        d = hosts[name]
        c = d.get("capacity") or {}
        if c.get("up") is None:
            cap = "    -    "        # no capacity test has run yet
        elif c.get("direct") == 0:
            cap = "  relay  "        # measured a DERP server, not the link
        else:
            cap = pair(d, "capacity")
        out.append("  %-13s %s  gw %s" % (name, cap, pair(d, "health")))
    return out


def backup_line():
    files = sorted(glob.glob(os.path.join(BACKUP_DIR, "*.tar.gz")),
                   key=os.path.getmtime)
    if not files:
        return "backup: no archive found"
    newest = files[-1]
    age_h = (time.time() - os.path.getmtime(newest)) / 3600.0
    size = human_bytes(os.path.getsize(newest))
    when = "%.0fh ago" % age_h if age_h >= 1 else "just now"
    flag = "" if age_h < 30 else "  <- STALE"
    # An archive on disk only proves one was BUILT. Whether it reached
    # anywhere is in last_status, written by the backup script as
    # "<epoch> ok=1 sent=2". A run that reached one destination of two
    # leaves an identical file behind and would otherwise read as fine.
    sent = None
    try:
        with open(os.path.join(BACKUP_DIR, "last_status")) as f:
            for part in f.read().split():
                if part.startswith("sent="):
                    sent = int(part[5:])
    except Exception:
        pass

    if sent is None:
        where = "  <- cannot tell where it went"
    elif sent == 0:
        where = "  <- BUILT BUT SENT NOWHERE"
    else:
        where = ", %d destination%s" % (sent, "" if sent == 1 else "s")
    return "backup: %s, %s%s%s" % (when, size, where, flag)


def failed_units():
    # Units systemd considers failed. Silence here is the good case.
    import subprocess
    try:
        r = subprocess.run(["systemctl", "--failed", "--no-legend",
                            "--plain", "--no-pager"],
                           capture_output=True, text=True, timeout=10)
    except Exception:
        return None
    names = [l.split()[0] for l in r.stdout.splitlines() if l.strip()]
    if not names:
        return None
    more = " and %d more" % (len(names) - 6) if len(names) > 6 else ""
    return "FAILED UNITS: " + ", ".join(names[:6]) + more


lines = []
healthy = True
try:
    hosts = q('count(count by (instance) (up{pilot="xxops"}))')
    lines.append("hosts reporting: %s of %d" %
                 (int(hosts) if hosts else 0, EXPECTED_HOSTS))
    if not hosts or int(hosts) < EXPECTED_HOSTS:
        healthy = False

    alerts = firing()
    if not alerts:
        lines.append("alerts: none firing")
    else:
        healthy = False
        red = [a for a in alerts if a[0] == "red"]
        lines.append("alerts: %d firing (%d red)" % (len(alerts), len(red)))
        for sev, name, inst in sorted(alerts)[:8]:
            lines.append("  %s %s %s" % (sev, name, inst))
        if len(alerts) > 8:
            lines.append("  ... and %d more" % (len(alerts) - 8))

    free = q('node_filesystem_avail_bytes{job="monitor-host",mountpoint="/"}')
    tsdb = q("prometheus_tsdb_storage_blocks_bytes")
    lines.append("monitor: %s free, tsdb %s" %
                 (human_bytes(free), human_bytes(tsdb)))
except Exception as e:
    healthy = False
    lines.append("COULD NOT REACH PROMETHEUS: %s" % e)
    lines.append("The fleet may be fine - the monitor is not.")

lines.extend(linkspeed_lines())
lines.append(backup_line())
_failed = failed_units()
if _failed:
    lines.append(_failed)

head = "xxOps daily - all steady" if healthy else "xxOps daily - needs a look"
text = head + "\n" + "\n".join(lines)

# Before the first contact is saved this file does not exist. Exit the same
# way we do for a missing token, rather than leaving a traceback in the
# journal every morning.
try:
    n = json.load(open(NOTIFY))
except (OSError, ValueError):
    print("no %s yet - nothing sent" % NOTIFY)
    raise SystemExit(0)
token = (n.get("telegram") or {}).get("bot_token")
chat = (n.get("fallback") or {}).get("telegram_chat_id")
if not chat:
    for c in n.get("contacts", []):
        if c.get("telegram_chat_id"):
            chat = c["telegram_chat_id"]
            break
if not token or not chat:
    print("no telegram token or chat id in %s - nothing sent" % NOTIFY)
    raise SystemExit(0)

data = urllib.parse.urlencode({"chat_id": str(chat), "text": text}).encode()
req = urllib.request.Request(
    "https://api.telegram.org/bot%s/sendMessage" % token, data=data)
with urllib.request.urlopen(req, timeout=20) as r:
    print("sent, HTTP %s" % r.status)
print(text)
