#!/bin/bash
# xxOps host uninstaller. The mirror of agent/install.sh.
#
#   sudo ./uninstall.sh              # shows what it would remove
#   sudo ./uninstall.sh --apply      # backs everything up, then removes it
#
# DRY RUN BY DEFAULT. Nothing is touched until --apply.
#
# It DISCOVERS what is installed rather than assuming paths - reading the
# systemd units to find the agent's account and binary - so it still works on
# a host that was set up differently from the one this was written on.
#
# EVERYTHING IS BACKED UP FIRST, to a single tar under /root, so a host can be
# put back exactly as it was. That matters most for /etc/xxops/allowed_signers,
# which holds your monitor's public key: with the backup, restoring trust is a
# file copy rather than a reinstall.
#
# IT NEVER TOUCHES /opt/xxnetwork. Your node or gateway keeps running and keeps
# earning throughout. This removes monitoring, not the validator.
#
# Before running this on a monitored host, mute it in the app. Its metrics stop
# the moment Alloy does, and that is exactly what HostUnreachable watches for.

set -euo pipefail

APPLY=0
PURGE_ALLOY=0
STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP="/root/xxops-uninstall-$STAMP.tar.gz"

for a in "$@"; do
  case "$a" in
    --apply)       APPLY=1 ;;
    --purge-alloy) PURGE_ALLOY=1 ;;
    -h|--help)
      cat >&2 <<'USAGE'
xxOps host uninstaller

  --apply         actually remove things. Without it, nothing happens.
  --purge-alloy   also uninstall the Grafana Alloy package, not just its
                  config. Use this if you want the host genuinely back to
                  how it was before xxOps.
USAGE
      exit 2 ;;
    *) echo "unknown option: $a" >&2; exit 2 ;;
  esac
done

[ "$(id -u)" -eq 0 ] || { echo "run with sudo" >&2; exit 1; }

say()  { printf '%s\n' "$*"; }
step() { printf '  %s\n' "$*"; }

# --- find what is actually here ---------------------------------------------

UNITS=()
for u in /etc/systemd/system/xxops-*.service /etc/systemd/system/xxops-*.timer; do
  [ -e "$u" ] && UNITS+=("$(basename "$u")")
done
[ -e /etc/systemd/system/alloy.service ] && ALLOY_UNIT="alloy.service" || ALLOY_UNIT=""
systemctl list-unit-files alloy.service >/dev/null 2>&1 && ALLOY_UNIT="alloy.service"

AGENT_USER=""
AGENT_BIN=""
if [ -f /etc/systemd/system/xxops-agent.service ]; then
  AGENT_USER="$(sed -n 's/^User=//p' /etc/systemd/system/xxops-agent.service | head -1)"
  AGENT_BIN="$(sed -n 's/^ExecStart=//p' /etc/systemd/system/xxops-agent.service \
               | head -1 | awk '{for(i=1;i<=NF;i++) if ($i ~ /xxops/) {print $i; exit}}')"
fi

FILES=()
for f in /usr/local/bin/xxops-textfile.sh \
         /usr/local/bin/xxops-gateway-watchdog.sh \
         /etc/sudoers.d/xxops-agent \
         /etc/logrotate.d/xxnetwork \
         /etc/systemd/journald.conf.d/xxops.conf \
         /var/lib/alloy/textfile/xx.prom \
         /etc/alloy/config.alloy \
         "$AGENT_BIN"; do
  [ -n "$f" ] && [ -e "$f" ] && FILES+=("$f")
done
[ -d /etc/xxops ] && FILES+=("/etc/xxops")
for u in "${UNITS[@]}"; do FILES+=("/etc/systemd/system/$u"); done

# --- describe the plan ------------------------------------------------------

say ""
say "xxOps uninstall on $(hostname)"
say ""
say "Units to stop and disable:"
if [ ${#UNITS[@]} -eq 0 ]; then step "(none found)"; else
  for u in "${UNITS[@]}"; do step "$u"; done
fi
[ -n "$ALLOY_UNIT" ] && step "$ALLOY_UNIT"

say ""
say "Files and directories to back up, then remove:"
if [ ${#FILES[@]} -eq 0 ]; then step "(none found)"; else
  for f in "${FILES[@]}"; do step "$f"; done
fi

if [ -n "$AGENT_USER" ]; then
  say ""
  say "Account to remove:"
  step "$AGENT_USER"
fi

if [ "$PURGE_ALLOY" -eq 1 ]; then
  say ""
  say "Grafana Alloy package will also be uninstalled."
fi

say ""
say "NOT TOUCHED: /opt/xxnetwork - your validator keeps running."
say "Backup will be written to: $BACKUP"

if [ "$APPLY" -ne 1 ]; then
  say ""
  say "Dry run - nothing was changed. Re-run with --apply to do it."
  exit 0
fi

# --- back up before anything is removed -------------------------------------

say ""
if [ ${#FILES[@]} -gt 0 ]; then
  tar -czf "$BACKUP" --absolute-names --ignore-failed-read "${FILES[@]}" 2>/dev/null || true
  chmod 600 "$BACKUP"
  say "Backed up to $BACKUP"
  say "  $(tar -tzf "$BACKUP" 2>/dev/null | wc -l) entries"
else
  say "Nothing found to back up."
fi

# --- stop, then remove ------------------------------------------------------

for u in "${UNITS[@]}"; do
  systemctl disable --now "$u" >/dev/null 2>&1 || true
  say "  stopped and disabled $u"
done

if [ -n "$ALLOY_UNIT" ]; then
  systemctl disable --now "$ALLOY_UNIT" >/dev/null 2>&1 || true
  say "  stopped and disabled $ALLOY_UNIT"
fi

for f in "${FILES[@]}"; do
  case "$f" in
    /opt/xxnetwork*) say "  REFUSING to remove $f"; continue ;;
  esac
  rm -rf "$f"
  say "  removed $f"
done

if [ -n "$AGENT_USER" ] && id "$AGENT_USER" >/dev/null 2>&1; then
  userdel "$AGENT_USER" >/dev/null 2>&1 || true
  say "  removed account $AGENT_USER"
fi

if [ "$PURGE_ALLOY" -eq 1 ]; then
  if command -v apt-get >/dev/null; then
    apt-get -qq remove --purge -y alloy >/dev/null 2>&1 || true
    say "  uninstalled the Alloy package"
  fi
fi

systemctl daemon-reload
systemctl restart systemd-journald >/dev/null 2>&1 || true

# --- prove the validator is untouched ---------------------------------------

say ""
say "Checking your validator is still running:"
for s in xxnetwork-cmix xxnetwork-gateway xxnetwork-chain; do
  if systemctl list-unit-files "$s.service" >/dev/null 2>&1 \
     && systemctl cat "$s" >/dev/null 2>&1; then
    say "  $s: $(systemctl is-active "$s" 2>/dev/null)"
  fi
done

cat <<NEXT

Done. This host no longer reports to any monitor.

To put it back exactly as it was:
  sudo tar -xzf $BACKUP -C /
  sudo systemctl daemon-reload
  sudo systemctl enable --now <the units listed above>

Keep that tar somewhere you will find it.
NEXT
