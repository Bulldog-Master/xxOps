#!/usr/bin/env python3
"""
xxops-linkspeed.py -- measure this host's link and write a state file.

Two modes, two different jobs:

  health    against this node's own GATEWAY. The production path. Capped by
            the gateway's VPS uplink (~150 Mbps on this fleet), which is fine
            for a threshold. Run often, it is cheap.

  capacity  against a PEER NODE. Both ends are fast boxes, so this is the
            only path in the fleet that reports the node's real link. Run
            rarely, it moves gigabytes.

Reads  /etc/xxops/linkspeed.conf
Writes /var/lib/xxops/linkspeed.json  (atomically, world-readable)

THE RULE THIS SCRIPT EXISTS TO ENFORCE: a failed or skipped run NEVER writes
a zero. Zero means "this link is dead" to an alert rule. A test that could
not run is not a dead link, so the previous reading is kept and the failure
is recorded separately. The producer publishes the age, and the alert rule
decides what a stale reading means.

Usage:  xxops-linkspeed.py health
        xxops-linkspeed.py capacity
        xxops-linkspeed.py health --dry-run
"""

import fcntl
import json
import os
import subprocess
import sys
import tempfile
import time

CONF = os.environ.get("XXOPS_LINKSPEED_CONF", "/etc/xxops/linkspeed.conf")
STATE = os.environ.get("XXOPS_LINKSPEED_STATE", "/var/lib/xxops/linkspeed.json")
LOCK = "/run/xxops-linkspeed.lock"

# Defaults; any of these can be overridden in the conf file.
DEFAULTS = {
    "HEALTH_T": "8",
    "HEALTH_O": "3",
    "CAPACITY_T": "8",
    "CAPACITY_O": "3",
    "IPERF3": "/usr/bin/iperf3",
    "TAILSCALE": "/usr/bin/tailscale",
}

# A run must not outlive its own timer. -t plus -O plus handshake plus slack.
TIMEOUT_SLACK = 25


def read_conf(path):
    """KEY=VALUE lines. Blank lines and # comments ignored."""
    cfg = dict(DEFAULTS)
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                cfg[k.strip()] = v.strip().strip('"').strip("'")
    except FileNotFoundError:
        return None
    return cfg


def read_state(path):
    """Never let a corrupt state file stop a run -- start fresh instead."""
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, ValueError):
        return {}


def write_state(path, data):
    """Write then rename. The producer reads this every 60s and must never
    catch a half-written file."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".linkspeed.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, sort_keys=True)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def warm_path(tailscale, peer):
    """Ping the peer and report how the traffic is going.

    Two jobs in one. Tailscale needs a round trip to establish the direct
    path; without it the first seconds of iperf3 measure path negotiation
    rather than the link -- that is the 1.4 Mbps first second seen on a
    healthy 800 Mbps node.

    IMPORTANT: `tailscale ping` prints one line and stops at the first pong,
    and with --until-direct it keeps retrying until the direct path is up.
    So calling this on a COLD path reports the relay it used while
    negotiating, even though iperf3 a moment later runs direct at full
    speed. Call it once to warm, and again afterwards to classify.

    Returns "direct", "relay" or "unknown".
    """
    try:
        r = subprocess.run(
            [tailscale, "ping", "-c", "5", peer],
            capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    # Only the LAST pong tells you anything. Tailscale sends the first packet
    # via DERP while it negotiates the direct path, so a healthy direct peer
    # reports DERP on line 1 and its real address on line 3. Checking the
    # whole output labels a good 800 Mbps link a relay.
    lines = [l for l in r.stdout.splitlines() if "pong from" in l.lower()]
    if not lines:
        return "unknown"
    last = lines[-1].lower()
    return "relay" if "derp" in last else "direct"


def run_iperf(iperf3, peer, secs, omit, reverse):
    """One direction. Returns Mbps, or raises RuntimeError with the reason."""
    cmd = [iperf3, "-c", peer, "-t", str(secs), "-O", str(omit), "-J"]
    if reverse:
        cmd.append("-R")
    try:
        r = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=int(secs) + int(omit) + TIMEOUT_SLACK,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError("iperf3 timed out")
    except OSError as e:
        raise RuntimeError("iperf3 could not be run: %s" % e)

    try:
        data = json.loads(r.stdout)
    except ValueError:
        raise RuntimeError((r.stderr or r.stdout or "no output").strip()[:200])

    if data.get("error"):
        raise RuntimeError(str(data["error"])[:200])

    end = data.get("end", {})
    # -R measures what arrived here; forward measures what left here.
    key = "sum_received" if reverse else "sum_sent"
    bps = end.get(key, {}).get("bits_per_second")
    if bps is None:
        bps = end.get("sum_received", {}).get("bits_per_second")
    if not bps:
        raise RuntimeError("no bitrate in iperf3 output")

    retr = end.get("sum_sent", {}).get("retransmits")
    return round(bps / 1e6, 1), retr


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    dry = "--dry-run" in sys.argv

    if len(args) != 1 or args[0] not in ("health", "capacity"):
        print("usage: xxops-linkspeed.py health|capacity [--dry-run]")
        return 2
    mode = args[0]

    cfg = read_conf(CONF)
    if cfg is None:
        print("no config at %s -- nothing to do" % CONF)
        return 1

    peer_key = "GATEWAY_PEER" if mode == "health" else "NODE_PEER"
    peer = cfg.get(peer_key)

    # NODE_PEER may be a comma-separated list. Rotating which peer is used
    # keeps a slow reading attributable: against one fixed partner you can
    # never tell which end is slow. Selection happens HERE, by ISO week, so
    # the config file stays static and the fleet needs no weekly reissue.
    # Every host picks a different starting point, so they do not all
    # converge on the same peer in the same week.
    if peer and "," in peer:
        peers = [p.strip() for p in peer.split(",") if p.strip()]
        if peers:
            # Days since epoch, not ISO week: the capacity test may run
            # daily, and a weekly key would pin it to the same peer for
            # seven runs. Day-based works at either cadence.
            day = int(time.time() // 86400)
            seed = sum(ord(c) for c in os.uname().nodename)
            peer = peers[(day + seed) % len(peers)]
        else:
            peer = None

    if not peer:
        # Not an error. A host with no peer assigned simply has no test to
        # run, and must not be recorded as a failure.
        print("%s not set in %s -- skipping" % (peer_key, CONF))
        return 0

    secs = cfg["HEALTH_T"] if mode == "health" else cfg["CAPACITY_T"]
    omit = cfg["HEALTH_O"] if mode == "health" else cfg["CAPACITY_O"]

    # One run at a time. Two overlapping tests would measure each other.
    lock_fd = os.open(LOCK, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("another link-speed run is in progress -- skipping")
        return 0

    now = int(time.time())
    state = read_state(STATE)
    entry = dict(state.get(mode, {}))

    # Warm first -- this call establishes the direct path. Its answer is
    # about the cold state and is deliberately discarded.
    warm_path(cfg["TAILSCALE"], peer)

    try:
        up, up_retr = run_iperf(cfg["IPERF3"], peer, secs, omit, reverse=False)
        down, down_retr = run_iperf(cfg["IPERF3"], peer, secs, omit, reverse=True)
    except RuntimeError as e:        # THE IMPORTANT BRANCH. Keep whatever was measured last, record why
        # this attempt failed, and let the age of the reading speak for
        # itself. Writing 0 here would page as a dead link.
        entry["last_error"] = str(e)
        entry["last_error_ts"] = now
        entry["peer"] = peer
        state[mode] = entry
        if dry:
            print(json.dumps(state, indent=2, sort_keys=True))
        else:
            write_state(STATE, state)
        print("FAILED: %s (previous reading kept)" % e)
        return 1

    # Now that traffic has flowed, ask again. THIS is the answer that
    # describes the path the measurement actually used.
    path = warm_path(cfg["TAILSCALE"], peer)

    entry.update({
        "up_mbps": up,
        "down_mbps": down,
        "up_retransmits": up_retr,
        "down_retransmits": down_retr,
        "peer": peer,
        "path": path,
        "ts": now,
        "secs": int(secs),
        "omit": int(omit),
    })
    entry.pop("last_error", None)
    entry.pop("last_error_ts", None)
    state[mode] = entry

    if dry:
        print(json.dumps(state, indent=2, sort_keys=True))
    else:
        write_state(STATE, state)

    print("%s: up %.1f Mbps, down %.1f Mbps, path %s, peer %s"
          % (mode, up, down, path, peer))
    return 0


if __name__ == "__main__":
    sys.exit(main())
