#!/usr/bin/env bash
# xxops-host-install.sh -- set up one node or gateway, in one command.
#
#   curl -sL https://raw.githubusercontent.com/Bulldog-Master/xxOps/main/fixes/xxops-host-install.sh \
#     | sudo bash -s -- --label <NAME> --monitor <MONITOR-IP>
#
# The two things it cannot work out are at the FRONT of the command, where you
# can see them without scrolling:
#
#   --label <NAME>       what this machine is called in the app. Short,
#                        lowercase, unique across your hosts. If a gateway's
#                        label starts with its node's label, xxOps pairs them
#                        for you.
#   --monitor <ADDRESS>  the address this host reaches the monitor on.
#
# Everything else it works out for itself, including whether this is a node or
# a gateway. Override with --role node|gateway if it guesses wrong.
#
# It announces each step before doing it, so a failure tells you exactly where
# it stopped. Safe to re-run: that is also how you change the label or move to
# a different monitor.

set -eu

RAW="${XXOPS_RAW:-https://raw.githubusercontent.com/Bulldog-Master/xxOps/main}"

LABEL=""
MON=""
ROLE=""
TOKEN=""
SKIP_AGENT=no
APPLY=0

usage() {
  cat >&2 <<'USAGE'
xxOps host install -- one node or gateway, one command.

  sudo bash xxops-host-install.sh --label <NAME> --monitor <ADDRESS>

  --label <NAME>       what this machine is called in the app. Short,
                       lowercase, and unique across your hosts.
  --monitor <ADDRESS>  the address this host reaches the monitor on.

  --token <TOKEN>      the enrolment token from the app, under Commands.
                       Without it this host still reports metrics and raises
                       alerts, but will not appear on the Commands tab.

  --apply              actually do it. WITHOUT THIS IT IS A DRY RUN:
                       it prints what it would do and changes
                       nothing, the same as install-monitor.sh.

  --role node|gateway  only if the automatic guess is wrong.
  --skip-agent         install metrics only, no command agent.
                       It never REMOVES an agent or watchdog a
                       previous run installed - use
                       agent/uninstall.sh for that.

It installs Grafana Alloy, the metric producer, the xxOps agent, a logrotate
rule and a journal cap -- and on a gateway, the gossip watchdog.
USAGE
  exit 2
}

while [ $# -gt 0 ]; do
  case "$1" in
    --label)      LABEL="${2:-}"; shift 2 ;;
    --monitor)    MON="${2:-}"; shift 2 ;;
    --role)       ROLE="${2:-}"; shift 2 ;;
    --token)      TOKEN="${2:-}"; shift 2 ;;
    --skip-agent) SKIP_AGENT=yes; shift ;;
    --apply)      APPLY=1; shift ;;
    -h|--help)    usage ;;
    *) echo "unknown option: $1" >&2; echo "" >&2; usage ;;
  esac
done

[ "$(id -u)" = "0" ] || { echo "run this with sudo" >&2; exit 1; }

# --- check the arguments before touching anything ---------------------------
fail=0
if [ -z "$LABEL" ]; then
  echo "missing --label: what should this machine be called in the app?" >&2
  fail=1
fi
if [ -z "$MON" ]; then
  echo "missing --monitor: what address does this host reach the monitor on?" >&2
  fail=1
fi
# The agent's files are fetched over plain HTTP and two of them run as root
# through sudoers entries. They are verified against a manifest MACed with
# this token, so without it there is no way to know what arrived. Refuse
# rather than install something unverified onto validator infrastructure.
if [ -z "$TOKEN" ] && [ "$SKIP_AGENT" = no ]; then
  echo "missing --token: needed to verify the agent's files before installing" >&2
  echo "" >&2
  echo "  Get it from the app's Commands tab." >&2
  echo "  Or pass --skip-agent for metrics and alerts only, with no agent." >&2
  fail=1
fi
[ "$fail" = 0 ] || { echo "" >&2; usage; }

# A label with a placeholder in it is the single most likely mistake, and it
# fails silently later, so refuse it here where the message can be useful.
case "$LABEL$MON" in
  *"<"*|*">"*)
    echo "REFUSING: '$LABEL' or '$MON' still contains < >." >&2
    echo "Those are placeholders - replace them, brackets and all." >&2
    exit 1 ;;
esac
case "$LABEL" in
  *[!a-zA-Z0-9_-]*)
    echo "REFUSING: the label '$LABEL' has characters that will not survive" >&2
    echo "a metric label. Use letters, digits, underscore or hyphen." >&2
    exit 1 ;;
esac

step()  { printf '\n== %s\n' "$1"; }
say()   { printf '   %s\n' "$1"; }
die()   { printf '\nFAILED at: %s\n%s\n' "$CURRENT" "$1" >&2; exit 1; }
CURRENT="starting"

# --- which role is this ------------------------------------------------------
CURRENT="working out the role"
step "Working out what this machine is"
if [ -z "$ROLE" ]; then
  if systemctl list-unit-files 2>/dev/null | grep -q '^xxnetwork-gateway'; then
    ROLE=gateway
  elif systemctl list-unit-files 2>/dev/null | grep -q '^xxnetwork-cmix'; then
    ROLE=node
  else
    echo "REFUSING: cannot tell whether this is a node or a gateway." >&2
    echo "Neither xxnetwork-cmix nor xxnetwork-gateway is installed here." >&2
    echo "If that is expected, pass --role node or --role gateway." >&2
    exit 1
  fi
  say "detected: $ROLE"
else
  say "told: $ROLE (not detected)"
fi
case "$ROLE" in node|gateway) ;; *) echo "--role must be node or gateway" >&2; exit 1 ;; esac
say "label:  $LABEL"
say "monitor: $MON"

# --- can it reach the monitor ------------------------------------------------
CURRENT="checking the monitor is reachable"
step "Checking this host can reach the monitor"
if curl -sf -m 10 "http://${MON}:9090/-/healthy" >/dev/null 2>&1; then
  say "the monitor answers on ${MON}:9090"
else
  die "cannot reach http://${MON}:9090/-/healthy

Nothing else will work until this does. Check the address, and that this
host is on the same network as the monitor."
fi

# --- what this would do, and the line before it does any of it -------------
#
# Everything above here is read-only: argument checks, working out the role,
# and a curl that proves the monitor answers. Everything below here changes
# the host. install-monitor.sh draws the line the same way and uses the same
# words, so the two installers behave alike.
#
# The plan is built from what is actually here rather than printed from a
# fixed list. "install Alloy" on a host that already has it is the kind of
# line that teaches you to stop reading the plan.
CURRENT="describing the plan"
step "What this will do"

if command -v alloy >/dev/null 2>&1; then
  say "leave Grafana Alloy alone (already installed)"
else
  say "install Grafana Alloy from Grafana's apt repository"
fi

if [ -f /etc/alloy/config.alloy ]; then
  say "REPLACE /etc/alloy/config.alloy, labelling this host '${LABEL}'"
  say "  and pointing it at ${MON}:9090 (the current one is backed up)"
else
  say "write /etc/alloy/config.alloy, labelling this host '${LABEL}'"
  say "  and pointing it at ${MON}:9090"
fi

say "install the metric producer and its 60s timer, running as alloy"
say "grant alloy read access to /opt/xxnetwork cred, log and config by ACL"

if [ "$ROLE" = gateway ] && [ "$SKIP_AGENT" = no ]; then
  say "install the gossip watchdog and its timer (gateway)"
elif [ "$ROLE" = gateway ]; then
  if [ -f /etc/systemd/system/xxops-gateway-watchdog.timer ]; then
    say "NOT install the watchdog (--skip-agent), and LEAVE the existing"
    say "  one running - this installer never removes it"
  else
    say "NOT install the watchdog (--skip-agent)"
  fi
fi

say "write /etc/logrotate.d/xxnetwork and validate it"
say "cap the systemd journal at 1G"

if [ "$SKIP_AGENT" = no ]; then
  say "install the xxOps agent, its account and its sudoers entry"
  if [ -n "$TOKEN" ]; then
    say "register this host with the monitor so it appears under Commands"
  else
    say "NOT register it (no --token), so it will not appear under Commands"
  fi
else
  if [ -f /etc/sudoers.d/xxops-agent ]; then
    say "NOT install the agent (--skip-agent), and LEAVE the existing one"
    say "  and its sudoers grant in place - use agent/uninstall.sh to remove"
  else
    say "NOT install the agent (--skip-agent)"
  fi
fi

say "check the monitor is receiving metrics labelled '${LABEL}'"

if [ "$APPLY" -ne 1 ]; then
  say ""
  say "Dry run - nothing was changed. Re-run with --apply to do it."
  exit 0
fi

# --- Alloy -------------------------------------------------------------------
CURRENT="installing Grafana Alloy"
step "Installing Grafana Alloy"
if command -v alloy >/dev/null 2>&1 || [ -x /usr/bin/alloy ]; then
  say "already installed"
else
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq gpg >/dev/null 2>&1 || true
  install -d -m 755 /etc/apt/keyrings
  if ! wget -q -O - https://apt.grafana.com/gpg.key 2>/dev/null \
       | gpg --dearmor > /etc/apt/keyrings/grafana.gpg 2>/dev/null; then
    die "could not fetch Grafana's signing key. Check this host has internet access."
  fi
  echo "deb [signed-by=/etc/apt/keyrings/grafana.gpg] https://apt.grafana.com stable main" \
    > /etc/apt/sources.list.d/grafana.list
  apt-get update -qq >/dev/null 2>&1 || die "apt-get update failed after adding Grafana's repository."
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq alloy >/dev/null \
    || die "could not install the alloy package."
  say "installed"
fi

CURRENT="writing the Alloy configuration"
step "Configuring Alloy"
install -d -m 755 -o alloy -g alloy /var/lib/alloy/textfile
say "textfile directory ready"

cat > /etc/alloy/config.alloy <<EOF
logging {
  level  = "info"
  format = "logfmt"
}

prometheus.exporter.self "alloy" {}

prometheus.scrape "alloy" {
  targets         = prometheus.exporter.self.alloy.targets
  forward_to      = [prometheus.relabel.add_host_label.receiver]
  job_name        = "alloy"
  scrape_interval = "60s"
}

prometheus.exporter.unix "system" {
  include_exporter_metrics = true

  filesystem {
    fs_types_exclude = "^(autofs|binfmt_misc|bpf|cgroup2?|configfs|debugfs|devpts|devtmpfs|fusectl|hugetlbfs|iso9660|mqueue|nsfs|overlay|proc|procfs|pstore|rpc_pipefs|securityfs|selinuxfs|squashfs|sysfs|tracefs|tmpfs)\$"
    mount_points_exclude = "^/(dev|proc|sys|run|var/lib/docker/.+)(\$|/)"
    mount_timeout = "5s"
  }
}

prometheus.scrape "system" {
  targets         = prometheus.exporter.unix.system.targets
  forward_to      = [prometheus.relabel.add_host_label.receiver]
  job_name        = "node"
  scrape_interval = "30s"
}

prometheus.scrape "xx_chain" {
  targets         = [{ __address__ = "127.0.0.1:9615" }]
  forward_to      = [prometheus.relabel.add_host_label.receiver]
  job_name        = "xx_chain"
  scrape_interval = "30s"
}

prometheus.relabel "add_host_label" {
  forward_to = [prometheus.remote_write.xxops.receiver]

  rule {
    target_label = "instance"
    replacement  = "${LABEL}"
  }
  rule {
    target_label = "pilot"
    replacement  = "xxops"
  }
}

prometheus.remote_write "xxops" {
  endpoint {
    url = "http://${MON}:9090/api/v1/write"

    queue_config {
      capacity             = 10000
      max_samples_per_send = 2000
      batch_send_deadline  = "5s"
    }
  }

  wal {
    truncate_frequency = "2h"
  }
}

prometheus.exporter.unix "xx_textfile" {
  set_collectors = ["textfile"]

  textfile {
    directory = "/var/lib/alloy/textfile"
  }
}

prometheus.scrape "xx_textfile" {
  targets         = prometheus.exporter.unix.xx_textfile.targets
  forward_to      = [prometheus.relabel.add_host_label.receiver]
  job_name        = "xx"
  scrape_interval = "60s"
}
EOF

# Prove the values landed rather than assuming. This is the failure that has
# bitten every hand-pasted install: an empty label or address writes a config
# that starts cleanly and sends nothing anywhere.
grep -q "replacement  = \"${LABEL}\"" /etc/alloy/config.alloy \
  || die "the label did not make it into the config."
grep -q "http://${MON}:9090/api/v1/write" /etc/alloy/config.alloy \
  || die "the monitor address did not make it into the config."
say "config written, label and address verified in it"

systemctl enable --now alloy >/dev/null 2>&1 || true
systemctl restart alloy
sleep 2
systemctl is-active --quiet alloy || die "alloy did not stay running. See: journalctl -u alloy -n 30"
say "alloy running"

# --- producer ----------------------------------------------------------------
CURRENT="installing the metric producer"
step "Installing the producer"
tmp="$(mktemp)"
curl -fsS "$RAW/producer/xxops-textfile.sh" -o "$tmp" \
  || die "could not download the producer from $RAW"
bash -n "$tmp" || die "the downloaded producer has a syntax error - truncated download?"
install -m 755 "$tmp" /usr/local/bin/xxops-textfile.sh
rm -f "$tmp"

# --- what may read the validator's own directories --------------------------
# Granted HERE, before the collector unit below, and regardless of
# --skip-agent.
#
# These ACLs used to be set only by agent/install.sh, which runs later and
# only when an agent is being installed. That was fine while the collector
# ran as root, because root ignores ACLs. It stops being fine the moment the
# collector runs as an unprivileged account: a --skip-agent host would never
# get the grant, and the collector would quietly emit fewer metrics rather
# than fail.
#
# Both accounts: alloy, which the collector runs as, and xxops-agent when it
# exists. An account that is not present is skipped, not an error.
if ! command -v setfacl >/dev/null 2>&1; then
  step "install the acl package"
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq acl >/dev/null 2>&1 || true
fi

if command -v setfacl >/dev/null 2>&1; then
  xxops_grant() {
    [ -e "$1" ] || return 0
    for _u in alloy xxops-agent; do
      id -u "$_u" >/dev/null 2>&1 || continue
      setfacl -m "u:${_u}:$2" "$1" 2>/dev/null || true
    done
  }
  xxops_grant /opt/xxnetwork        x
  xxops_grant /opt/xxnetwork/cred   rx
  xxops_grant /opt/xxnetwork/log    rx
  xxops_grant /opt/xxnetwork/config rx

  # Prove it, as the account the collector will actually run as. A
  # filesystem mounted without acl support accepts setfacl and does nothing.
  if [ -d /opt/xxnetwork/cred ] && id -u alloy >/dev/null 2>&1; then
    if runuser -u alloy -- test -r /opt/xxnetwork/cred 2>/dev/null; then
      say "  granted alloy read access to the validator's certs and logs"
    else
      say "  WARNING: alloy still cannot read /opt/xxnetwork/cred."
      say "  The collector will run but will emit fewer metrics. Is this"
      say "  filesystem mounted with acl support?"
    fi
  fi
else
  say "  WARNING: setfacl unavailable - the collector will not be able to"
  say "  read the validator's certificates or logs."
fi

# RUNS AS alloy, NOT ROOT.
#
# Two root code-execution bugs were found in this collector in two days, and
# both were critical only because of the account it ran under. Everything it
# reads is world-readable or ACL-granted below; the one thing it must WRITE
# is /var/lib/alloy/textfile, which alloy already owns.
#
# alloy rather than a new account: it exists on every host, it is already
# unprivileged, and it is already the process that publishes these metrics.
#
# ProtectSystem=strict makes the whole filesystem read-only except the paths
# named below, so even a future bug in this script cannot write anywhere
# unexpected. NoNewPrivileges stops it gaining any through a setuid binary -
# it needs none, unlike the agent.
cat > /etc/systemd/system/xxops-textfile.service <<'EOF'
[Unit]
Description=xxOps textfile metric producer
[Service]
Type=oneshot
User=alloy
Group=alloy
ExecStart=/usr/local/bin/xxops-textfile.sh
NoNewPrivileges=yes
ProtectHome=read-only
PrivateTmp=yes
# NO ProtectSystem. It made the filesystem read-only for this service, and
# the storage check then reported the HOST's disk as read-only - a false
# StorageRootReadOnly on every host, and a real check disabled, because a
# genuinely read-only disk became indistinguishable from the sandbox.
#
# It was guarding against the collector writing somewhere unexpected, which
# the unprivileged account already prevents: alloy owns almost nothing.
# Redundant hardening that breaks working detection is not worth having.
EOF

cat > /etc/systemd/system/xxops-textfile.timer <<'EOF'
[Unit]
Description=Run the xxOps textfile producer every 60s
[Timer]
OnBootSec=30
OnUnitActiveSec=60
[Install]
WantedBy=timers.target
EOF

systemctl daemon-reload
systemctl enable --now xxops-textfile.timer >/dev/null 2>&1
/usr/local/bin/xxops-textfile.sh >/dev/null 2>&1 || true
if [ -s /var/lib/alloy/textfile/xx.prom ]; then
  say "producer installed, and it wrote metrics"
else
  say "producer installed (it has not written yet - the timer will)"
fi

# Inside the SKIP_AGENT check on purpose. This watchdog's timer runs as ROOT
# and calls `systemctl restart` on the gateway directly - no sudo, no
# signature, no confirmation. It is the least passive thing this installer
# places, so a flag meaning "do not put anything on my validator that acts"
# has to cover it. It used to install regardless, which made --skip-agent a
# promise the installer did not keep.
if [ "$ROLE" = gateway ] && [ "$SKIP_AGENT" = no ]; then
  CURRENT="installing the gateway watchdog"
  step "Installing the gateway watchdog"
  tmp="$(mktemp)"
  curl -fsS "$RAW/producer/xxops-gateway-watchdog.sh" -o "$tmp" \
    || die "could not download the watchdog from $RAW"
  bash -n "$tmp" || die "the downloaded watchdog has a syntax error."
  install -m 755 "$tmp" /usr/local/bin/xxops-gateway-watchdog.sh
  rm -f "$tmp"

  cat > /etc/systemd/system/xxops-gateway-watchdog.service <<'EOF'
[Unit]
Description=xxOps gateway gossip watchdog
[Service]
Type=oneshot
ExecStart=/usr/local/bin/xxops-gateway-watchdog.sh
EOF

  cat > /etc/systemd/system/xxops-gateway-watchdog.timer <<'EOF'
[Unit]
Description=Run the xxOps gateway watchdog every 5 minutes
[Timer]
OnBootSec=10min
OnUnitActiveSec=5min
[Install]
WantedBy=timers.target
EOF

  systemctl daemon-reload
  systemctl enable --now xxops-gateway-watchdog.timer >/dev/null 2>&1
  say "watchdog installed"
elif [ "$ROLE" = gateway ]; then
  say "watchdog NOT installed (--skip-agent)"
  say "  Nothing installed here will restart your gateway. If it stops"
  say "  gossiping you will be alerted, and the restart is yours to make."
  # A host that HAD one from an earlier run keeps it unless it is removed,
  # which would be a surprise in the other direction. Say so plainly.
  if [ -f /etc/systemd/system/xxops-gateway-watchdog.timer ]; then
    say ""
    say "  NOTE: a watchdog from an earlier install is still present and"
    say "  still running. This installer does not remove it. To take it"
    say "  and the agent off cleanly - backed up first, and reversible:"
    say "    sudo ./agent/uninstall.sh          # shows what it would do"
    say "    sudo ./agent/uninstall.sh --apply"
  fi
fi

# --- logrotate ---------------------------------------------------------------
CURRENT="adding a logrotate rule"
step "Adding a logrotate rule"
U="$(stat -c '%U' /opt/xxnetwork 2>/dev/null || echo root)"
if [ "$ROLE" = gateway ]; then
  LOGS="/opt/xxnetwork/log/gateway.log /opt/xxnetwork/log/gateway-wrapper.log /opt/xxnetwork/log/chain.log"
  ROT="    size 200M"
else
  LOGS="/opt/xxnetwork/log/cmix.log /opt/xxnetwork/log/cmix-err.log /opt/xxnetwork/log/cmix-wrapper.log /opt/xxnetwork/log/chain.log"
  ROT="    daily
    maxsize 250M"
fi
{
  for f in $LOGS; do echo "$f"; done
  echo "{"
  echo "$ROT"
  echo "    rotate 7"
  echo "    compress"
  echo "    missingok"
  echo "    notifempty"
  echo "    copytruncate"
  echo "    su $U $U"
  echo "}"
} > /etc/logrotate.d/xxnetwork
if logrotate -d /etc/logrotate.d/xxnetwork >/dev/null 2>&1; then
  say "rule written and it validates (owner: $U)"
else
  say "rule written, but logrotate -d reported a problem - check it by hand"
fi

# --- journal cap -------------------------------------------------------------
CURRENT="capping the systemd journal"
step "Capping the systemd journal"
install -d -m 755 /etc/systemd/journald.conf.d
printf '[Journal]\nSystemMaxUse=1G\n' > /etc/systemd/journald.conf.d/xxops.conf
systemctl restart systemd-journald >/dev/null 2>&1 || true
journalctl --vacuum-size=1G >/dev/null 2>&1 || true
say "capped at 1G"

# --- agent -------------------------------------------------------------------
if [ "$SKIP_AGENT" = no ]; then
  CURRENT="installing the xxOps agent"
  step "Installing the agent"
  tmp="$(mktemp)"
  curl -fsS "$RAW/agent/install.sh" -o "$tmp" \
    || die "could not download the agent installer from $RAW"
  bash -n "$tmp" || die "the downloaded agent installer has a syntax error."
  if bash "$tmp" "${MON}:8080/agent" "" "$TOKEN"; then
    say "agent installed"
    # Tell the monitor where to reach this agent. Nothing else can: metrics
    # are PUSHED, so Prometheus only ever learns a label, never an address.
    #
    # The address is worked out the same way the agent installer worked out
    # what to bind to - tailscale if present, otherwise the source address of
    # the default route - so this is not tied to any one kind of network.
    if [ -n "$TOKEN" ]; then
      MYIP="$(tailscale ip -4 2>/dev/null | head -1)"
      [ -n "$MYIP" ] || MYIP="$(ip -4 route get 192.0.2.1 2>/dev/null \
                                | sed -n 's/.* src \([0-9.]*\).*/\1/p' | head -1)"
      if [ -z "$MYIP" ]; then
        say "could not work out this host's address - not registered."
        say "  the Commands tab will not list it. Everything else works."
      else
        reg="$(curl -sS -m 20 -X POST "http://${MON}:8080/api/agent/register" \
                 -H "Content-Type: application/json" \
                 -d "{\"token\":\"${TOKEN}\",\"host\":\"${LABEL}\",\"ip\":\"${MYIP}\"}" \
               2>&1 || true)"
        case "$reg" in
          *'"ok": true'*|*'"ok":true'*)
            say "registered with the monitor at ${MYIP}:8181" ;;
          *)
            say "could NOT register with the monitor:"
            say "  ${reg}"
            say "  metrics and alerts are unaffected. To fix it, re-run this"
            say "  with a current --token from the app's Commands tab." ;;
        esac
      fi
    else
      say "no --token given, so this host will not appear on the Commands tab."
      say "  Everything else works. Re-run with --token to add it later."
    fi
  else
    rm -f "$tmp"
    die "the agent installer failed. Everything above it is done - re-run this
script once that is sorted, or use --skip-agent to leave the agent out."
  fi
  rm -f "$tmp"
else
  step "Skipping the agent"
  say "as asked. Metrics and alerts work; actions from the app will not."
  # THIS DOES NOT REVOKE. A host that already has an agent keeps it, and
  # keeps its sudoers grant, so a re-run with --skip-agent leaves the host
  # exactly as privileged as it was. Removing things from inside an
  # installer would be a surprise in the other direction, and
  # agent/uninstall.sh already does it properly - backed up first, and it
  # reads the units rather than assuming paths.
  if [ -f /etc/sudoers.d/xxops-agent ] \
     || [ -f /etc/systemd/system/xxops-agent.service ]; then
    say ""
    say "  NOTE: an agent from an earlier install is still present, and"
    say "  still holds its sudoers grant. --skip-agent does not remove it."
    say "  To take it off cleanly - backed up first, and reversible:"
    say "    sudo ./agent/uninstall.sh          # shows what it would do"
    say "    sudo ./agent/uninstall.sh --apply"
  fi
fi

# --- did it actually work ----------------------------------------------------
CURRENT="confirming metrics arrive"
step "Confirming the monitor is receiving this host"
ok=no
for i in 1 2 3 4 5 6; do
  sleep 5
  if curl -sf -m 10 "http://${MON}:9090/api/v1/query?query=up" 2>/dev/null \
     | grep -q "\"${LABEL}\""; then
    ok=yes
    break
  fi
done
if [ "$ok" = yes ]; then
  say "the monitor is receiving metrics labelled '${LABEL}'"
else
  say "not seen yet after 30 seconds. That is not necessarily wrong -"
  say "it can take a minute. Check with:"
  say "  journalctl -u alloy -n 20"
fi

printf '\n== Done: %s, as %s\n' "$ROLE" "$LABEL"
printf '   It should appear in the app within a minute or two.\n'
