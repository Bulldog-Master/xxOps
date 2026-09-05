#!/usr/bin/env bash
# xxOps gateway watchdog
#
# one gateway sat for weeks receiving no gossip — peers refused it because it had
# no NDF — and a plain restart cleared it. This restarts a gateway that has
# stopped receiving gossip, but only under conditions strict enough that it
# will not fight a healthy one.
#
# Rails:
#   - only acts where /opt/xxnetwork/log/gateway.log exists (gateways only)
#   - needs the log to be established, so it cannot fire right after a rotation
#   - needs the service to have been up a while, so it cannot fire on a slow start
#   - one restart per cooldown window, so it can never restart-loop
#   - it GIVES UP after MAX_TRIES restarts that did not bring gossip back, and
#     publishes xx_gateway_watchdog_gave_up so a human gets told instead. this
#     exists because one gateway burned 14 restarts on an expired certificate,
#     which no restart could ever have fixed
#   - every action is counted into Prometheus and written to the journal
#
# The counter matters more than the restart. A gateway restarting itself daily
# is a problem being hidden, not solved — watch xx_gateway_watchdog_restarts_total.
set -u

# never let two copies run at once: an overlap corrupted the counters and made
# the give-up metric flap between 1 and 0, resolving and refiring the alert.
exec 9>/var/lock/xxops-watchdog.lock
flock -n 9 || exit 0

LOG=/opt/xxnetwork/log/gateway.log
SVC=xxnetwork-gateway
OUT=/var/lib/alloy/textfile/xx_watchdog.prom
STATE=/var/lib/xxops-watchdog

STALL_MIN=${XXOPS_STALL_MIN:-15}     # no gossip for this long = stalled
COOLDOWN_MIN=${XXOPS_COOLDOWN_MIN:-20}
GRACE_MIN=${XXOPS_GRACE_MIN:-10}     # ignore a service that only just started
MAX_TRIES=${XXOPS_MAX_TRIES:-3}      # stop after this many restarts that did not help
MIN_LOG_BYTES=${XXOPS_MIN_LOG:-50000}

[ -f "$LOG" ] || exit 0              # not a gateway

mkdir -p "$STATE" "$(dirname "$OUT")"
COUNT_F="$STATE/restarts"; LAST_F="$STATE/last_restart"; FAIL_F="$STATE/consec_fail"
[ -f "$COUNT_F" ] || echo 0 > "$COUNT_F"
[ -f "$LAST_F" ]  || echo 0 > "$LAST_F"
[ -f "$FAIL_F" ]  || echo 0 > "$FAIL_F"

now=$(date +%s)

line="$(tail -c 400000 "$LOG" 2>/dev/null | grep -a "Gossip received for round" | tail -1)"
if [ -n "$line" ]; then
  ts="$(printf "%s" "$line" | awk "{print \$2\" \"\$3}")"
  last="$(date -d "$ts" +%s 2>/dev/null || echo 0)"
else
  last=0
fi
if [ "$last" -gt 0 ]; then age=$(( now - last )); else age=-1; fi

write_metrics(){
  fails="$(cat "$FAIL_F")"
  { echo "xx_gateway_watchdog_restarts_total $(cat "$COUNT_F")"
    echo "xx_gateway_watchdog_last_restart $(cat "$LAST_F")"
    echo "xx_gateway_watchdog_gossip_age_seconds $age"
    echo "xx_gateway_watchdog_consecutive_failures $fails"
    echo "xx_gateway_watchdog_gave_up $([ "$fails" -ge "$MAX_TRIES" ] && echo 1 || echo 0)"
  } > "$OUT.tmp" && mv "$OUT.tmp" "$OUT" && chmod 644 "$OUT"
}
write_metrics

# --- decide -----------------------------------------------------------------
# age -1 means no gossip line in the window at all. That is the expired-certificate case,
# but it is also what a freshly rotated log looks like, so require some volume.
size=$(stat -c %s "$LOG" 2>/dev/null || echo 0)
[ "$size" -lt "$MIN_LOG_BYTES" ] && exit 0

if [ "$age" -ge 0 ] && [ "$age" -lt $(( STALL_MIN * 60 )) ]; then
  # gossip is back, so whatever we did worked. clear the failure state.
  if [ "$(cat "$FAIL_F")" -ne 0 ]; then
    logger -t xxops-watchdog "gossip has returned, clearing failure count"
    echo 0 > "$FAIL_F"; write_metrics
  fi
  exit 0
fi

# restarting has not helped. stop trying and let the alert stand.
if [ "$(cat "$FAIL_F")" -ge "$MAX_TRIES" ]; then
  exit 0
fi

started="$(systemctl show "$SVC" -p ActiveEnterTimestamp --value 2>/dev/null)"
sstart="$(date -d "$started" +%s 2>/dev/null || echo 0)"
if [ "$sstart" -gt 0 ] && [ $(( now - sstart )) -lt $(( GRACE_MIN * 60 )) ]; then
  exit 0                              # only just started, give it time
fi

if [ $(( now - $(cat "$LAST_F") )) -lt $(( COOLDOWN_MIN * 60 )) ]; then
  exit 0                              # already tried recently
fi

logger -t xxops-watchdog "no gossip for ${age}s — restarting $SVC"
systemctl restart "$SVC"
echo $(( $(cat "$COUNT_F") + 1 )) > "$COUNT_F"
echo $(( $(cat "$FAIL_F") + 1 )) > "$FAIL_F"
echo "$now" > "$LAST_F"
write_metrics
logger -t xxops-watchdog "restart issued (total $(cat "$COUNT_F"), attempt $(cat "$FAIL_F") of $MAX_TRIES)"
if [ "$(cat "$FAIL_F")" -ge "$MAX_TRIES" ]; then
  logger -t xxops-watchdog "that was attempt $MAX_TRIES. if gossip does not return, no further restarts will be tried."
fi
