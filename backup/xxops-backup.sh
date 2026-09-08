#!/usr/bin/env bash
# xxOps monitor backup
#
# Everything that matters lives on one VPS: the app, the backend, the alert
# rules, the Alertmanager config with its bot token, and the contacts. The
# producers survive on your validator hosts; nothing else does. This copies the
# lot to two machines you already own.
#
# It writes a metric on every run, because a backup nobody checks is not a
# backup - the same failure class as the producer that silently froze.
#
# Usage:  xxops-backup.sh [--with-tsdb]
set -u

STAMP="$(date +%Y%m%d-%H%M%S)"
WORK=/var/lib/xxops-backup
KEEP_LOCAL=${XXOPS_KEEP_LOCAL:-7}
KEEP_REMOTE=${XXOPS_KEEP_REMOTE:-7}
SSHKEY=${XXOPS_BACKUP_KEY:-/root/.ssh/xxops_backup}
DESTS=${XXOPS_BACKUP_DESTS:-}       # space separated user@host:/path
WITH_TSDB=0
[ "${1:-}" = "--with-tsdb" ] && WITH_TSDB=1

mkdir -p "$WORK"
ARCHIVE="$WORK/xxops-$STAMP.tar.gz"
ok=1
note(){ logger -t xxops-backup "$1"; echo "$1"; }

# --- what to include -------------------------------------------------------
# only paths that exist, so this works on a monitor set up slightly differently
# the app directory comes from the running service, since this runs as root
# and $HOME would be /root rather than the operator home
APPDIR="$(systemctl show xxops-app -p WorkingDirectory --value 2>/dev/null)"
[ -z "$APPDIR" ] && APPDIR="$(systemctl show xxops-app -p Environment --value 2>/dev/null | tr ' ' '\n' | sed -n "s/^XXOPS_APP_DIR=//p" | head -1)"
[ -z "$APPDIR" ] && APPDIR=/root/xxops

PATHS=""
for p in "$APPDIR" /etc/prometheus /etc/alertmanager /var/lib/xxops /etc/xxops \
         /var/lib/grafana/grafana.db /etc/grafana/grafana.ini \
         /etc/systemd/system/xxops-app.service \
         /etc/systemd/system/prometheus.service \
         /etc/systemd/system/alertmanager.service; do
  [ -e "$p" ] && PATHS="$PATHS $p"
done

# the TSDB is history rather than configuration, so it is opt in
if [ "$WITH_TSDB" -eq 1 ]; then
  TSDB="$(systemctl show prometheus -p ExecStart --value 2>/dev/null \
          | tr ' ' '\n' | sed -n "s/^--storage.tsdb.path=//p" | head -1)"
  [ -z "$TSDB" ] && for c in /var/lib/prometheus /var/lib/prometheus/data "$HOME/prometheus-data"; do
    [ -d "$c" ] && TSDB="$c" && break
  done
  [ -n "$TSDB" ] && [ -d "$TSDB" ] && PATHS="$PATHS $TSDB"
fi

if [ -z "$PATHS" ]; then
  note "nothing found to back up - check the paths in this script"
  exit 1
fi

# --- build it --------------------------------------------------------------
# cmd_key signs commands to every agent and this archive lands on two
# of them - a re-key is recoverable, a leaked key is not
# --- clear rollback files that have outlived their usefulness ---------------
# A .bak is written by a patch or by deploy.sh. After a week the change has
# survived several deploys and a backup cycle, so the rollback path can go -
# git holds every version regardless.
#
# This lives here rather than in deploy.sh because it must not depend on
# anyone deploying: a fleet running well is one nobody deploys to, which is
# exactly when debris would build up unnoticed. And it runs BEFORE the archive,
# so the app directory's debris is not carried into every future backup.
prune_baks() {
  local n=0 d
  for d in "$APPDIR" /home/*/xxops-repo /root/xxops-repo; do
    [ -d "$d" ] || continue
    n=$(( n + $(find "$d" -maxdepth 3 -name '*.bak' -type f -mtime +7 \
                  -print -delete 2>/dev/null | wc -l) ))
  done
  [ "$n" -gt 0 ] && note "cleared $n rollback file(s) older than a week"
  return 0
}
prune_baks

if tar -czf "$ARCHIVE" --warning=no-file-changed --exclude='*/cmd_key' $PATHS 2>/dev/null; then
  chmod 600 "$ARCHIVE"
  note "built $(basename "$ARCHIVE") ($(du -h "$ARCHIVE" | cut -f1))"
else
  # tar exits non zero when a file changed while reading, which is routine for
  # a live TSDB. treat a present, non trivial archive as a success.
  if [ -s "$ARCHIVE" ]; then
    chmod 600 "$ARCHIVE"
    note "built $(basename "$ARCHIVE") ($(du -h "$ARCHIVE" | cut -f1)) with warnings"
  else
    note "FAILED to build the archive"
    ok=0
  fi
fi

# --- copy it off the box ---------------------------------------------------
sent=0
if [ "$ok" -eq 1 ] && [ -n "$DESTS" ]; then
  for d in $DESTS; do
    if scp -q -i "$SSHKEY" -o StrictHostKeyChecking=accept-new \
           -o ConnectTimeout=20 "$ARCHIVE" "$d/" 2>/dev/null; then
      note "sent to $d"
      sent=$(( sent + 1 ))
      host="${d%%:*}"; dir="${d#*:}"
      ssh -i "$SSHKEY" -o StrictHostKeyChecking=accept-new -o ConnectTimeout=20 \
          "$host" "ls -1t $dir/xxops-*.tar.gz 2>/dev/null | tail -n +$(( KEEP_REMOTE + 1 )) | xargs -r rm -f" 2>/dev/null
    else
      note "COULD NOT send to $d"
      ok=0
    fi
  done
else
  [ -z "$DESTS" ] && note "no destinations set - keeping a local copy only"
fi

# --- prune local copies ----------------------------------------------------
ls -1t "$WORK"/xxops-*.tar.gz 2>/dev/null | tail -n +$(( KEEP_LOCAL + 1 )) | xargs -r rm -f

# --- make the outcome visible ----------------------------------------------
# a silent backup failure is the thing to avoid, so publish it where the rest
# of the monitoring can see it. writes wherever a textfile collector exists.
for tdir in /var/lib/alloy/textfile /var/lib/node_exporter/textfile /var/lib/prometheus/textfile; do
  if [ -d "$tdir" ]; then
    {
      echo "xx_backup_last_run $(date +%s)"
      echo "xx_backup_ok $ok"
      echo "xx_backup_destinations_reached $sent"
      [ -f "$ARCHIVE" ] && echo "xx_backup_size_bytes $(stat -c %s "$ARCHIVE")"
    } > "$tdir/xx_backup.prom.tmp" && mv "$tdir/xx_backup.prom.tmp" "$tdir/xx_backup.prom"
    chmod 644 "$tdir/xx_backup.prom"
    break
  fi
done

echo "$(date +%s) ok=$ok sent=$sent" > "$WORK/last_status"
[ "$ok" -eq 1 ] || exit 1
note "backup complete, $sent destination(s)"
