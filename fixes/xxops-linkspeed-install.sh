#!/usr/bin/env bash
# xxops-linkspeed-install.sh -- set up the link-speed test on this host.
#
# Two measurements, two different questions:
#
#   health    this node to ITS OWN GATEWAY. The production path, and the one
#             worth alerting on. Works with any number of validators.
#   capacity  this node to ANOTHER NODE. Both ends are fast machines, so this
#             is the only measurement that reflects the node's real link.
#             Needs three or more nodes before a slow reading can be blamed on
#             one end rather than the other, so it is optional.
#
# A GATEWAY only ever listens. The node dials it, so a gateway needs nothing
# but the listener.
#
# Usage, on a NODE:
#   sudo bash xxops-linkspeed-install.sh --gateway <gateway-address>
#   sudo bash xxops-linkspeed-install.sh --gateway <addr> --peers <a,b,c>
#
# Usage, on a GATEWAY:
#   sudo bash xxops-linkspeed-install.sh --listener-only
#
# Options:
#   --bind <address>     which of this host's addresses to listen on. Worked
#                        out automatically if you leave it off.
#   --peers <a,b,c>      other NODES to measure capacity against, comma
#                        separated. Omit to leave capacity switched off.
#   --from-monitor <ip>  large fleets only: fetch this host's config from a
#                        monitor running the peer generator, instead of
#                        passing addresses by hand.
#
# Idempotent -- re-running is how you change the peers or pick up an update.

set -eu

RAW="${XXOPS_RAW:-https://raw.githubusercontent.com/Bulldog-Master/xxOps/main}"

GATEWAY_PEER=""
NODE_PEERS=""
BIND=""
MONITOR=""
LISTENER_ONLY=no

while [ $# -gt 0 ]; do
  case "$1" in
    --gateway)       GATEWAY_PEER="${2:-}"; shift 2 ;;
    --peers)         NODE_PEERS="${2:-}"; shift 2 ;;
    --bind)          BIND="${2:-}"; shift 2 ;;
    --from-monitor)  MONITOR="${2:-}"; shift 2 ;;
    --listener-only) LISTENER_ONLY=yes; shift ;;
    -h|--help)       sed -n '2,40p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $1" >&2
       echo "run with --help for usage" >&2; exit 2 ;;
  esac
done

if [ -z "$GATEWAY_PEER" ] && [ "$LISTENER_ONLY" = no ] && [ -z "$MONITOR" ]; then
  echo "nothing to do: say what this host is." >&2
  echo "" >&2
  echo "  on a node:     sudo bash $0 --gateway <gateway-address>" >&2
  echo "  on a gateway:  sudo bash $0 --listener-only" >&2
  exit 2
fi

say(){ printf '  %s\n' "$1"; }

# --- which address do we listen on ------------------------------------------
# Same order as the agent installer: what you told us, then Tailscale, then
# the source address of the default route. TAILSCALE IS NOT REQUIRED -- an
# operator on a firewall rule or WireGuard is just as valid, and hard-requiring
# it would lock them out of the feature entirely.
[ -n "$BIND" ] || BIND="$(tailscale ip -4 2>/dev/null | head -1)"
[ -n "$BIND" ] || BIND="$(ip -4 route get 192.0.2.1 2>/dev/null \
                          | sed -n 's/.* src \([0-9.]*\).*/\1/p' | head -1)"
if [ -z "$BIND" ]; then
  echo "could not work out which address to listen on." >&2
  echo "" >&2
  echo "pass it yourself:  sudo bash $0 --bind <this-host-address> ..." >&2
  exit 1
fi

# --- large-fleet path: take the config from a monitor -----------------------
if [ -n "$MONITOR" ]; then
  TMPC="$(mktemp)"; trap 'rm -f "$TMPC"' EXIT
  if ! curl -fsS -o "$TMPC" "http://$MONITOR:8898/by-ip/$BIND.conf"; then
    echo "REFUSING: no config published for $BIND on $MONITOR" >&2
    echo "  run the peer generator there, and check it is serving :8898" >&2
    exit 1
  fi
  grep -q '^LISTEN_IP=' "$TMPC" || { echo "REFUSING: config has no LISTEN_IP" >&2; exit 1; }
  GOT="$(sed -n 's/^LISTEN_IP=//p' "$TMPC" | head -1)"
  if [ "$GOT" != "$BIND" ]; then
    echo "REFUSING: that config is for $GOT, but this host is $BIND" >&2
    exit 1
  fi
  mkdir -p /etc/xxops
  install -m 644 "$TMPC" /etc/xxops/linkspeed.conf
  say "config fetched from $MONITOR"
else
  # Written here rather than fetched. The only thing this host cannot work
  # out for itself is which gateway is ITS gateway, and the operator knows
  # that -- so they pass it, the same way the agent installer takes the
  # monitor address.
  mkdir -p /etc/xxops
  {
    echo "# written by xxops-linkspeed-install.sh"
    echo "LISTEN_IP=$BIND"
    [ -n "$GATEWAY_PEER" ] && echo "GATEWAY_PEER=$GATEWAY_PEER"
    [ -n "$NODE_PEERS" ]   && echo "NODE_PEER=$NODE_PEERS"
  } > /etc/xxops/linkspeed.conf
  chmod 644 /etc/xxops/linkspeed.conf
  say "config written (listening on $BIND)"
fi

IS_NODE=no
grep -q '^GATEWAY_PEER=' /etc/xxops/linkspeed.conf && IS_NODE=yes
say "role: $([ "$IS_NODE" = yes ] && echo node || echo "gateway (listener only)")"

if [ "$IS_NODE" = yes ] && ! grep -q '^NODE_PEER=' /etc/xxops/linkspeed.conf; then
  say "capacity test off: no peers given, so only the gateway path is measured"
fi

# --- iperf3 -----------------------------------------------------------------
if ! command -v iperf3 >/dev/null 2>&1; then
  say "installing iperf3"
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq iperf3 >/dev/null
else
  say "iperf3 already present"
fi

# --- listener ---------------------------------------------------------------
# Bound to ONE address on purpose. These machines have a public address too,
# and iperf3's default is to listen on all of them -- which would put an
# unauthenticated bandwidth test on the internet.
cat > /etc/systemd/system/xxops-iperf3.service <<'EOF'
[Unit]
Description=xxOps link-speed listener
After=network-online.target
Wants=network-online.target

[Service]
EnvironmentFile=/etc/xxops/linkspeed.conf
ExecStart=/usr/bin/iperf3 -s -B ${LISTEN_IP}
Restart=always
RestartSec=10
User=nobody
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now xxops-iperf3 >/dev/null 2>&1 || true
systemctl restart xxops-iperf3

# Verify the bind rather than trusting it. This is the one mistake that would
# actually matter.
sleep 1
BOUND="$(ss -ltn 2>/dev/null | grep ':5201' || true)"
if printf '%s' "$BOUND" | grep -q '0\.0\.0\.0:5201'; then
  echo "REFUSING TO CONTINUE: iperf3 bound to every address -- stopping it" >&2
  systemctl stop xxops-iperf3
  systemctl disable xxops-iperf3 >/dev/null 2>&1 || true
  exit 1
fi
if ! printf '%s' "$BOUND" | grep -q "$BIND:5201"; then
  echo "WARNING: listener not visible on $BIND:5201" >&2
  systemctl status xxops-iperf3 --no-pager | tail -5 >&2
  exit 1
fi
say "listener bound to $BIND:5201"

if [ "$IS_NODE" != yes ]; then
  echo "OK"
  exit 0
fi

# --- node: the test itself --------------------------------------------------
mkdir -p /var/lib/xxops

fetch(){   # fetch <url-path> <dest> <check-command>
  local tmp; tmp="$(mktemp)"
  if ! curl -fsS -o "$tmp" "$RAW/$1"; then
    echo "REFUSING: could not fetch $1" >&2; rm -f "$tmp"; exit 1
  fi
  if ! eval "$3 \"$tmp\"" >/dev/null 2>&1; then
    echo "REFUSING: $1 failed its check -- truncated download?" >&2
    rm -f "$tmp"; exit 1
  fi
  if [ -f "$2" ]; then cp -a "$2" "$2.bak"; fi
  install -m 755 "$tmp" "$2"
  rm -f "$tmp"
}

fetch producer/xxops-linkspeed.py /usr/local/bin/xxops-linkspeed.py \
      "python3 -c 'import py_compile,sys; py_compile.compile(sys.argv[1], doraise=True)'"
say "test script installed"

# Measuring is useless if nothing publishes it -- the producer is what turns
# the state file into metrics. A host left on an older copy measures
# perfectly and reports nothing, with no error anywhere to say so.
fetch producer/xxops-textfile.sh /usr/local/bin/xxops-textfile.sh "bash -n"
if ! grep -q 'ls_state=/var/lib/xxops/linkspeed.json' /usr/local/bin/xxops-textfile.sh; then
  echo "REFUSING: that producer has no link-speed block -- wrong version?" >&2
  [ -f /usr/local/bin/xxops-textfile.sh.bak ] &&
    mv /usr/local/bin/xxops-textfile.sh.bak /usr/local/bin/xxops-textfile.sh
  exit 1
fi
say "producer updated (previous kept as .bak)"

cat > /etc/systemd/system/xxops-linkspeed@.service <<'EOF'
[Unit]
Description=xxOps link-speed test (%i)
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/local/bin/xxops-linkspeed.py %i
Nice=10
IOSchedulingClass=idle
EOF

cat > /etc/systemd/system/xxops-linkspeed-health.timer <<'EOF'
[Unit]
Description=xxOps link-speed health test, twice daily

[Timer]
Unit=xxops-linkspeed@health.service
OnCalendar=*-*-* 06,18:00:00
RandomizedDelaySec=1800
Persistent=true

[Install]
WantedBy=timers.target
EOF

cat > /etc/systemd/system/xxops-linkspeed-capacity.timer <<'EOF'
[Unit]
Description=xxOps link-speed capacity test, daily

[Timer]
Unit=xxops-linkspeed@capacity.service
OnCalendar=*-*-* 03:00:00
RandomizedDelaySec=7200
Persistent=true

[Install]
WantedBy=timers.target
EOF

systemctl daemon-reload
systemctl enable --now xxops-linkspeed-health.timer >/dev/null 2>&1
if grep -q '^NODE_PEER=' /etc/xxops/linkspeed.conf; then
  systemctl enable --now xxops-linkspeed-capacity.timer >/dev/null 2>&1
  say "timers enabled (health twice daily, capacity daily)"
else
  # No peers, so the capacity timer would wake up daily to do nothing.
  systemctl disable --now xxops-linkspeed-capacity.timer >/dev/null 2>&1 || true
  say "timer enabled (health twice daily)"
fi
echo "OK"
