#!/bin/bash
# xxOps monitor uninstaller. The mirror of install-monitor.sh.
#
#   sudo bash install-monitor-uninstall.sh           # shows what it would do
#   sudo bash install-monitor-uninstall.sh --apply   # archives, then removes
#
# DRY RUN BY DEFAULT. Nothing is touched until --apply.
#
# EVERYTHING IS ARCHIVED FIRST to a single tar under /root, so the monitor can
# be put back. That matters most for /etc/xxops/cmd_key - see below.
#
# IT NEVER TOUCHES /opt/xxnetwork. A monitor is often colocated with a
# gateway, and your validator keeps running and keeps earning throughout.
#
# IT DOES NOT REMOVE PROMETHEUS OR ALERTMANAGER. install-monitor.sh never
# installed them - the guide has you do that by hand - so removing them here
# would be this script overreaching. Their xxOps config is removed only with
# --purge-config.

set -euo pipefail

APPLY=0
PURGE_CONFIG=0
STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP="/root/xxops-monitor-uninstall-$STAMP.tar.gz"

# DISCOVERED, not assumed. install-monitor.sh takes --app-dir, so this path
# differs per install - and a machine can have a stale /opt/xxops beside the
# real one, in which case assuming removes the wrong directory and leaves the
# running app behind.
UNIT=/etc/systemd/system/xxops-app.service
APP_DIR=""
if [ -f "$UNIT" ]; then
  APP_DIR="$(sed -n 's/^WorkingDirectory=//p' "$UNIT" | head -1)"
  [ -n "$APP_DIR" ] || APP_DIR="$(sed -n 's/^ExecStart=.* \(\/.*\)\/xxops-server\.py$/\1/p' "$UNIT" | head -1)"
fi
[ -n "$APP_DIR" ] || APP_DIR=/opt/xxops

# This goes to rm -rf, so a wrong answer is unrecoverable. Accept it only if
# it is a real app directory - xxops-server.py is what makes it one - and
# never a path whose removal would take the system with it.
case "$APP_DIR" in
  /|/home|/usr|/etc|/var|/opt|"")
    echo "REFUSING: discovered app directory is '$APP_DIR'" >&2; exit 1 ;;
esac
if [ ! -f "$APP_DIR/xxops-server.py" ]; then
  echo "REFUSING: '$APP_DIR' has no xxops-server.py, so it is not the app" >&2
  echo "directory. Nothing was touched. Check $UNIT." >&2
  exit 1
fi

STATE_DIR=/var/lib/xxops

for a in "$@"; do
  case "$a" in
    --apply)        APPLY=1 ;;
    --purge-config) PURGE_CONFIG=1 ;;
    -h|--help)
      cat >&2 <<'USAGE'
xxOps monitor uninstaller

  --apply          actually remove things. Without it, nothing happens.
  --purge-config   also remove the Prometheus and Alertmanager CONFIG that
                   xxOps wrote. Leaves both programs installed. Use this if
                   you are reinstalling and want a genuinely clean start -
                   note Prometheus will not start again without a config.
USAGE
      exit 2 ;;
    *) echo "unknown option: $a" >&2; exit 2 ;;
  esac
done

[ "$(id -u)" -eq 0 ] || { echo "run with sudo" >&2; exit 1; }

say(){ printf '   %s\n' "$1"; }
step(){ printf '\n== %s\n' "$1"; }

UNITS="xxops-app.service xxops-backup.service xxops-backup.timer
       xxops-cert.service xxops-cert.timer
       xxops-digest.service xxops-digest.timer"

FILES="$APP_DIR $STATE_DIR /etc/xxops
       /etc/prometheus/xxops-rules.yml
       /usr/local/bin/xxops-backup.sh /usr/local/bin/xxops-digest.py
       /root/.ssh/xxops_backup /root/.ssh/xxops_backup.pub"

[ "$PURGE_CONFIG" -eq 1 ] && FILES="$FILES /etc/prometheus/prometheus.yml
                                          /etc/alertmanager/alertmanager.yml"

step "What is here"
found=""
for u in $UNITS; do
  if systemctl list-unit-files "$u" >/dev/null 2>&1 \
     && [ -f "/etc/systemd/system/$u" ]; then
    say "unit    $u ($(systemctl is-active "$u" 2>/dev/null || true))"
    found="$found $u"
  fi
done
for f in $FILES; do
  [ -e "$f" ] && say "path    $f"
done

if [ -f /etc/xxops/cmd_key ]; then
  step "Read this before going further"
  say "/etc/xxops/cmd_key is the key EVERY AGENT ON EVERY HOST trusts."
  say "It is archived, but if you reinstall, the new monitor generates a"
  say "DIFFERENT key - and no host will accept commands until each one has"
  say "been reinstalled against it. Metrics and alerts keep working; the"
  say "Commands tab does not."
fi

step "What is deliberately left alone"
say "/opt/xxnetwork - your node or gateway, untouched"
say "prometheus and alertmanager themselves - this never installed them"
[ "$PURGE_CONFIG" -eq 0 ] && say "their config - pass --purge-config to remove it too"

if [ "$APPLY" -ne 1 ]; then
  printf '\n'
  say "Dry run - nothing was changed. Re-run with --apply to do it."
  exit 0
fi

step "Archiving first"
tarlist=""
for f in $FILES; do [ -e "$f" ] && tarlist="$tarlist $f"; done
for u in $found; do tarlist="$tarlist /etc/systemd/system/$u"; done
if [ -n "$tarlist" ]; then
  # shellcheck disable=SC2086
  tar czf "$BACKUP" $tarlist 2>/dev/null || true
  chmod 600 "$BACKUP"
  say "$BACKUP"
else
  say "nothing to archive"
fi

step "Stopping and removing units"
for u in $found; do
  systemctl disable --now "$u" >/dev/null 2>&1 || true
  rm -f "/etc/systemd/system/$u"
  say "removed $u"
done
systemctl daemon-reload

step "Removing files"
for f in $FILES; do
  [ -e "$f" ] || continue
  rm -rf "$f"
  say "removed $f"
done
rmdir /etc/prometheus /etc/alertmanager 2>/dev/null || true

step "What is still running"
for s in prometheus alertmanager xxnetwork-cmix xxnetwork-gateway; do
  if systemctl list-unit-files "$s.service" >/dev/null 2>&1; then
    say "$(printf '%-22s %s' "$s" "$(systemctl is-active "$s" 2>/dev/null || echo -)")"
  fi
done

step "Done"
say "Archive: $BACKUP"
say "Your validator was not touched."
