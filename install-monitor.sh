#!/bin/bash
# xxOps monitor installer.
#
#   ./install-monitor.sh --bind <MONITOR-IP> [options]          # shows a plan
#   ./install-monitor.sh --bind <MONITOR-IP> [options] --apply  # does it
#
# DRY RUN BY DEFAULT. Without --apply it prints exactly what it would do and
# changes nothing. That is deliberate: this script has to work on machines its
# author has never seen, so the first thing it should do on a new box is let
# you read its intentions.
#
# What it does:
#   - writes /etc/xxops/xxops.conf, the one file holding this monitor's address
#   - generates the agent signing key if there is not one already
#   - copies the app, backend, units, Prometheus config, alert rules and the
#     backup script into place
#   - substitutes this monitor's address into prometheus.yml
#   - validates every config with promtool and amtool BEFORE anything is live
#   - enables the units and checks the app actually answers
#
# What it does NOT do, and will not pretend to:
#   - install Prometheus, Alertmanager or Grafana. Those vary by distribution.
#     Install them first; this script checks they are there.
#   - decide your networking. Your validator hosts must be able to reach this
#     machine's Prometheus port before any of this is useful.
#   - touch an existing alertmanager.yml. That file holds your contacts and
#     your bot token, and it is never overwritten.
#
# Safe to re-run: it backs up anything it replaces and skips what is already
# correct.

set -euo pipefail

BIND=""
APP_DIR="/opt/xxops"
# Must match STATE_DIR in server/xxops-server.py.
STATE_DIR="/var/lib/xxops"
TAILNET_HOST=""
BACKUP_DESTS=""
APPLY=0
RUN_USER="${SUDO_USER:-$(id -un)}"

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STAMP="$(date +%Y%m%d-%H%M%S)"

usage() {
  cat >&2 <<'USAGE'
xxOps monitor installer

  --bind <IP>          address your validator hosts reach this machine on.
                       Required. Must NOT be 127.0.0.1 - the hosts push to it.
  --app-dir <path>     where the app and backend live (default /opt/xxops)
  --host <name>        DNS name you open the app on, for TLS. Optional.
  --backup-dests "<a> <b>"
                       space separated user@host:/path targets for the config
                       backup. Optional, but pick two machines that cannot
                       fail together.
  --apply              actually make the changes. Without it, nothing happens.

Example:
  sudo ./install-monitor.sh --bind 10.0.0.5 --host mon.example.net --apply
USAGE
  exit 2
}

while [ $# -gt 0 ]; do
  case "$1" in
    --bind)         BIND="${2:-}"; shift 2 ;;
    --app-dir)      APP_DIR="${2:-}"; shift 2 ;;
    --host)         TAILNET_HOST="${2:-}"; shift 2 ;;
    --backup-dests) BACKUP_DESTS="${2:-}"; shift 2 ;;
    --apply)        APPLY=1; shift ;;
    -h|--help)      usage ;;
    *) echo "unknown option: $1" >&2; usage ;;
  esac
done

say()  { printf '%s\n' "$*"; }
step() { printf '  %s\n' "$*"; }
die()  { printf 'REFUSING: %s\n' "$*" >&2; exit 1; }

# --- checks that must pass before we describe a plan at all -----------------

[ -n "$BIND" ] || usage
[ "$BIND" = "127.0.0.1" ] && die "--bind cannot be loopback. Your validator
hosts push metrics to this machine, so Prometheus has to listen somewhere they
can reach."

[ "$(id -u)" -eq 0 ] || die "run with sudo - this writes to /etc and /opt"

for f in app/xxops.html server/xxops-server.py prometheus/prometheus.yml \
         alerting/xxops-rules.yml backup/xxops-backup.sh; do
  [ -f "$REPO/$f" ] || die "$f not found - run this from inside the repo"
done

for b in systemctl python3 ssh-keygen; do
  command -v "$b" >/dev/null || die "$b not found"
done

MISSING=""
command -v prometheus >/dev/null || [ -x /usr/bin/prometheus ] || MISSING="$MISSING prometheus"
command -v promtool   >/dev/null || MISSING="$MISSING promtool"
command -v amtool     >/dev/null || MISSING="$MISSING amtool"
if [ -n "$MISSING" ]; then
  die "not installed:$MISSING
Install Prometheus and Alertmanager first, then re-run. Prometheus needs
--web.enable-remote-write-receiver and --web.enable-lifecycle."
fi

# --- work out what would change ---------------------------------------------

say ""
say "xxOps monitor install"
say "  repo:      $REPO"
say "  bind:      $BIND"
say "  app dir:   $APP_DIR"
say "  runs as:   $RUN_USER"
[ -n "$TAILNET_HOST" ] && say "  app host:  $TAILNET_HOST"
[ -n "$BACKUP_DESTS" ] && say "  backups:   $BACKUP_DESTS"
say ""
say "Plan:"

step "create $APP_DIR and $STATE_DIR, and copy the app, backend and docs in"
step "write /etc/xxops/xxops.conf"

if [ -f /etc/xxops/cmd_key ]; then
  step "keep the existing agent signing key (not regenerating)"
else
  step "generate an agent signing key at /etc/xxops/cmd_key"
fi

step "copy prometheus.yml to /etc/prometheus/ with $BIND substituted in"
step "copy xxops-rules.yml to /etc/prometheus/"

if [ -f /etc/alertmanager/alertmanager.yml ]; then
  step "LEAVE the existing alertmanager.yml alone"
else
  step "install the example alertmanager.yml - you must edit it afterwards"
fi

step "copy the systemd units and the backup script"
step "validate every config, then enable the units"
step "check the app answers on http://$BIND:8080/api/health"

if [ "$APPLY" -ne 1 ]; then
  say ""
  say "Dry run - nothing was changed. Re-run with --apply to do it."
  exit 0
fi

# --- from here on it acts ----------------------------------------------------

backup_if_present() {
  [ -f "$1" ] && cp -a "$1" "$1.pre-xxops-$STAMP" && say "  backed up $1"
  return 0
}

say ""
say "Applying."

install -d -m 755 "$APP_DIR"
# The app writes its settings and notification state here and
# creates it at startup -- which it cannot do under /var/lib as an
# unprivileged user. Without this the service crash-loops on a
# clean machine with a permission error that reads like a unit
# file problem.
install -d -m 755 "$STATE_DIR"
install -d -m 755 /etc/xxops /etc/prometheus
# The app rewrites alertmanager.yml when contacts are saved, and a safe write
# creates a temp file in this DIRECTORY before renaming it into place. Owned by
# root that fails, with an error naming the FILE rather than the directory --
# so it stays confusing even after the file's own ownership is correct.
install -d -m 750 -o "$RUN_USER" -g alertmanager /etc/alertmanager

backup_if_present "$APP_DIR/xxops.html"
backup_if_present "$APP_DIR/xxops-server.py"
install -m 644 "$REPO/app/xxops.html"        "$APP_DIR/xxops.html"
install -m 644 "$REPO/app/xxops.css" "$APP_DIR/xxops.css"
install -m 644 "$REPO/app/xxops-util.js" "$APP_DIR/xxops-util.js"
install -m 644 "$REPO/app/xxops-am.js" "$APP_DIR/xxops-am.js"
install -m 644 "$REPO/app/xxops-search.js" "$APP_DIR/xxops-search.js"
install -m 644 "$REPO/app/xxops-commands.js" "$APP_DIR/xxops-commands.js"
install -m 644 "$REPO/app/xxops-settings.js" "$APP_DIR/xxops-settings.js"
install -m 644 "$REPO/app/xxops-views.js" "$APP_DIR/xxops-views.js"
install -m 644 "$REPO/app/xxops-data.js" "$APP_DIR/xxops-data.js"
install -m 755 "$REPO/server/xxops-server.py" "$APP_DIR/xxops-server.py"
install -m 755 "$REPO/server/xxops_qr.py" "$APP_DIR/xxops_qr.py"
install -m 755 "$REPO/server/xxops_md.py" "$APP_DIR/xxops_md.py"
install -m 755 "$REPO/server/xxops_amconfig.py" "$APP_DIR/xxops_amconfig.py"
install -m 644 "$REPO/server/login.html"  "$APP_DIR/login.html"
# The agent installer fetches these four from the monitor. allowed_signers is
# generated per monitor and cannot come from anywhere else; the other three
# ride along so one address serves the whole agent install.
install -m 644 "$REPO/agent/xxops-agent.py" "$APP_DIR/xxops-agent.py"
install -m 644 "$REPO/agent/xxops-update-node.sh" "$APP_DIR/xxops-update-node.sh"
install -m 644 "$REPO/agent/xxops-update-gateway.sh" "$APP_DIR/xxops-update-gateway.sh"
install -m 644 "$REPO/server/redeem.html" "$APP_DIR/redeem.html"

# The app's Documentation page reads from here. Without this it is empty on
# every fresh install, which defeats the point of being able to read the guides
# on the machine you are managing.
#
# Overwritten on each run: these are product documents, not your configuration,
# and an update should bring the updated guides. .bak files are skipped -- the
# patch scripts leave them beside the originals and they would be served as
# duplicate stale documents.
install -d -m 755 "$APP_DIR/docs"
for d in "$REPO"/docs/*.md; do
  [ -e "$d" ] || continue
  case "$d" in *.bak) continue ;; esac
  install -m 644 "$d" "$APP_DIR/docs/"
done
[ -f "$REPO/fixes/bundled-fixes.json" ] && \
  install -m 644 "$REPO/fixes/bundled-fixes.json" "$APP_DIR/bundled-fixes.json"
chown -R "$RUN_USER":"$RUN_USER" "$APP_DIR"
chown "$RUN_USER":"$RUN_USER" "$STATE_DIR"

# The config file. Written out in full rather than composed, because systemd
# does no variable expansion inside an EnvironmentFile.
backup_if_present /etc/xxops/xxops.conf
{
  echo "# The one place this monitor's own address lives."
  echo "# A fresh install edits this file and nothing else."
  echo ""
  echo "XXOPS_BIND=$BIND"
  echo "XXOPS_PROM_URL=http://$BIND:9090"
  echo "XXOPS_AM_URL=http://127.0.0.1:9093"
  [ -n "$TAILNET_HOST" ] && echo "XXOPS_TAILNET_HOST=$TAILNET_HOST"
  [ -n "$BACKUP_DESTS" ] && echo "XXOPS_BACKUP_DESTS=$BACKUP_DESTS"
} > /etc/xxops/xxops.conf
chmod 644 /etc/xxops/xxops.conf
say "  wrote /etc/xxops/xxops.conf"

# The signing key. Never regenerated - replacing it would orphan every agent
# already trusting the old one.
if [ ! -f /etc/xxops/cmd_key ]; then
  ssh-keygen -t ed25519 -N "" -C "xxops-command-key" -f /etc/xxops/cmd_key >/dev/null
  chmod 600 /etc/xxops/cmd_key
  chmod 644 /etc/xxops/cmd_key.pub
  say "  generated /etc/xxops/cmd_key"
  say "  THIS KEY CAN RESTART SERVICES ON EVERY HOST YOU MANAGE."
  say "  It stays on this machine only, and is excluded from the backup."
fi

# Every agent fetches this to verify that a command really came from this
# monitor. It is the PUBLIC half with a principal prefixed -- the private key
# above never leaves this machine. Written outside the block above on purpose:
# a monitor that already had a key from an older install still needs this file,
# and without it the agent installer gets a 404 and never writes its unit.
if [ -f /etc/xxops/cmd_key.pub ]; then
  printf 'xxops-monitor %s' "$(cat /etc/xxops/cmd_key.pub)" \
    > /etc/xxops/allowed_signers
  chmod 644 /etc/xxops/allowed_signers
  say "  wrote /etc/xxops/allowed_signers"
fi

# Prometheus config, with this monitor's address substituted for the
# placeholder. Kept as bare IP:port on purpose - the app recognises that shape
# and keeps these targets out of its host list.
backup_if_present /etc/prometheus/prometheus.yml
sed "s|__MONITOR_IP__|$BIND|g" "$REPO/prometheus/prometheus.yml" \
  > /etc/prometheus/prometheus.yml
if grep -q "__MONITOR_IP__" /etc/prometheus/prometheus.yml; then
  die "placeholder substitution failed - check prometheus/prometheus.yml"
fi
backup_if_present /etc/prometheus/xxops-rules.yml
install -m 644 "$REPO/alerting/xxops-rules.yml" /etc/prometheus/xxops-rules.yml
say "  wrote the Prometheus config and rules"

if [ ! -f /etc/alertmanager/alertmanager.yml ]; then
  install -m 640 "$REPO/alerting/alertmanager.yml.example" \
    /etc/alertmanager/alertmanager.yml
  say "  installed the example alertmanager.yml - EDIT IT before relying on alerts"
fi

# Outside the block above on purpose: an install that ALREADY had a config
# keeps whatever ownership it had, and the app cannot rewrite it. Owned by the
# app so it can, group alertmanager so the service can read it, 640 because it
# ends up holding a bot token.
if [ -f /etc/alertmanager/alertmanager.yml ]; then
  chown "$RUN_USER":alertmanager /etc/alertmanager/alertmanager.yml
  chmod 640 /etc/alertmanager/alertmanager.yml
fi

install -m 755 "$REPO/backup/xxops-backup.sh" /usr/local/bin/xxops-backup.sh

# The digest units ship in systemd/, so without this they point at a script
# that does not exist. It reads its Telegram token from the app's own notify
# state and exits quietly when there is none, so enabling it now costs nothing
# and it starts working by itself once notifications are set up.
install -m 755 "$REPO/monitor/xxops-digest.py" /usr/local/bin/xxops-digest.py

# The backup authenticates with this key. Generated whether or not you have
# chosen destinations yet: an unused key costs nothing, and it means deciding
# on backups later needs no archaeology about what was expected where.
if [ ! -f /root/.ssh/xxops_backup ]; then
  install -d -m 700 /root/.ssh
  ssh-keygen -t ed25519 -N "" -C "xxops-backup" -f /root/.ssh/xxops_backup >/dev/null
  say "  generated /root/.ssh/xxops_backup"
fi

# The units ship with placeholders for the account and the app directory,
# because a unit carrying whoever built it is both a leak and broken on
# anyone else's machine. Substitute them here.
for u in "$REPO"/systemd/*.service "$REPO"/systemd/*.timer; do
  [ -f "$u" ] || continue
  sed -e "s|__RUN_USER__|$RUN_USER|g" -e "s|__APP_DIR__|$APP_DIR|g" "$u" \
    > "/etc/systemd/system/$(basename "$u")"
  chmod 644 "/etc/systemd/system/$(basename "$u")"
done
if grep -rq "__RUN_USER__\|__APP_DIR__" /etc/systemd/system/xxops-*.service 2>/dev/null; then
  die "unit placeholder substitution failed - check systemd/ in the repo"
fi
say "  copied the units and the backup script"

# Validate BEFORE anything is enabled. A bad rules file takes alerting down
# quietly, which is the one failure this whole system exists to prevent.
promtool check config /etc/prometheus/prometheus.yml >/dev/null \
  || die "prometheus.yml did not validate - nothing enabled"
promtool check rules  /etc/prometheus/xxops-rules.yml >/dev/null \
  || die "xxops-rules.yml did not validate - nothing enabled"
amtool check-config   /etc/alertmanager/alertmanager.yml >/dev/null \
  || die "alertmanager.yml did not validate - nothing enabled"
say "  configs validated"

systemctl daemon-reload
systemctl enable xxops-app >/dev/null 2>&1 || true
# restart, not `enable --now`. --now starts a STOPPED service and does nothing
# to a running one, so on a re-install the old process carries on serving the
# old code with the new file sitting on disk beside it.
systemctl restart xxops-app >/dev/null 2>&1 || true
systemctl enable --now xxops-digest.timer >/dev/null 2>&1 || true
[ -n "$BACKUP_DESTS" ] && systemctl enable --now xxops-backup.timer >/dev/null 2>&1 || true
say "  units enabled"

# Starting is not answering. Check the thing actually responds.
sleep 2
if curl -sf "http://$BIND:8080/api/health" >/dev/null; then
  say "  the app answers on http://$BIND:8080"
else
  say ""
  say "The app did not answer. It may still be starting, or the unit may need"
  say "its paths adjusting for --app-dir $APP_DIR. Check:"
  say "  systemctl status xxops-app"
  say "  journalctl -u xxops-app -n 30"
  exit 1
fi

cat <<NEXT

Done. Next:

  1. Open http://$BIND:8080 and create your account.
  2. Edit /etc/alertmanager/alertmanager.yml, or set contacts up in the app.

  BACKUPS are optional and off unless you passed --backup-dests. What is at
  stake: the app, the alert rules, your contacts and the bot token. NOT the
  agent signing key, which is excluded on purpose - the backup travels to
  machines that key can command. Losing the monitor without a backup means
  re-running this installer and re-entering your contacts.

  To turn them on, authorise a destination with the key just generated:

    ssh-copy-id -i /root/.ssh/xxops_backup.pub user@machine

  then re-run this installer with:

    --backup-dests "user@machine:/path user@other:/path"

  Pick two machines that cannot fail together. A laptop or desktop you already
  own is a better second copy than another validator host - it fails
  independently of the things being backed up.
  3. Install the agent and producer on each node and gateway - see the
     install guide in docs/.
  4. Confirm hosts are reporting:
     curl -s 'http://$BIND:9090/api/v1/query?query=count(up{pilot="xxops"})'

NEXT
