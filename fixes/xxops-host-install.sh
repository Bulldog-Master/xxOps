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
SKIP_AGENT=no

usage() {
  cat >&2 <<'USAGE'
xxOps host install -- one node or gateway, one command.

  sudo bash xxops-host-install.sh --label <NAME> --monitor <ADDRESS>

  --label <NAME>       what this machine is called in the app. Short,
                       lowercase, and unique across your hosts.
  --monitor <ADDRESS>  the address this host reaches the monitor on.

  --role node|gateway  only if the automatic guess is wrong.
  --skip-agent         install metrics only, no command agent.

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
    --skip-agent) SKIP_AGENT=yes; shift ;;
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

cat > /etc/systemd/system/xxops-textfile.service <<'EOF'
[Unit]
Description=xxOps textfile metric producer
[Service]
Type=oneshot
ExecStart=/usr/local/bin/xxops-textfile.sh
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

if [ "$ROLE" = gateway ]; then
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
  if bash "$tmp" "${MON}:8080/agent"; then
    say "agent installed"
  else
    rm -f "$tmp"
    die "the agent installer failed. Everything above it is done - re-run this
script once that is sorted, or use --skip-agent to leave the agent out."
  fi
  rm -f "$tmp"
else
  step "Skipping the agent"
  say "as asked. Metrics and alerts work; actions from the app will not."
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
