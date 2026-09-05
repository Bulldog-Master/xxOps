#!/bin/bash
# Take all pending package updates on this node and reboot.
#
# Called by the xxOps agent through sudo, which permits exactly this path.
#
# IT HANDS THE WORK TO SYSTEMD rather than backgrounding it. Two reasons:
#
#   The agent kills anything still running at 90 seconds, and an apt upgrade
#   killed mid-transaction leaves dpkg broken.
#
#   More importantly, the agent's own unit drops most capabilities, and a
#   CapabilityBoundingSet applies to everything the service spawns. setsid
#   starts a new session but stays in the same cgroup, so apt could download
#   but not chown, and shutdown could not talk to logind. You cannot regain a
#   capability the bounding set has dropped - sudo does not help. systemd-run
#   starts a fresh transient unit outside all of that.
#
# Everything is logged, because after a reboot the log is the only record.

set -uo pipefail
LOG=/var/log/xxops-update.log
UNIT=xxops-update

if [ "${1:-}" != "--go" ]; then
  if systemctl is-active --quiet "$UNIT.service"; then
    echo "an update is already running on this host"
    exit 1
  fi
  command -v systemd-run >/dev/null || { echo "systemd-run not found"; exit 1; }
  systemd-run --collect --quiet --unit="$UNIT" \
    --description="xxOps update and reboot" "$0" --go || {
      echo "could not start the update unit"; exit 1; }
  echo "update started - this node will reboot when it finishes"
  echo "it stops earning until it returns, usually a few minutes"
  echo "watch it with: tail -f $LOG"
  exit 0
fi

exec >>"$LOG" 2>&1
echo "=== $(date -Is) update starting on $(hostname) ==="

systemctl stop xxnetwork-chain || true
systemctl stop xxnetwork-cmix  || true

export DEBIAN_FRONTEND=noninteractive
# --force-confold matters: a changed config file would otherwise prompt, and a
# prompt with no terminal attached hangs for ever.
apt-get -y update
apt-get -y -o Dpkg::Options::=--force-confold upgrade
apt-get -y autoremove
apt-get -y autoclean

echo "=== $(date -Is) update finished, rebooting ==="
systemctl reboot
