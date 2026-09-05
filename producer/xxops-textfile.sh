#!/usr/bin/env bash
set -u
OUT="/var/lib/alloy/textfile/xx.prom"
# In the OUTPUT directory, not /tmp. Two reasons: /tmp is on the root
# filesystem, so a read-only root kills the producer here before it
# writes anything - which would defeat putting the output on tmpfs.
# And mv is only atomic within one filesystem, so a temp file on a
# different filesystem from $OUT turns the publish into copy-then-
# delete and lets a collector read a half-written file.
TMP="$(mktemp -p "$(dirname "$OUT")")"
LOGDIR="/opt/xxnetwork/log"
emit(){ printf "%s\n" "$1" >> "$TMP"; }

for svc in xxnetwork-cmix xxnetwork-gateway xxnetwork-chain postgresql; do
  state="$(systemctl is-active "$svc" 2>/dev/null || true)"
  val=0; [ "$state" = "active" ] && val=1
  emit "xx_service_up{service=\"$svc\"} $val"
done

if [ -f "$LOGDIR/cmix.log" ]; then
  line="$(tail -c 200000 "$LOGDIR/cmix.log" 2>/dev/null | grep -a "Round took" | tail -1)"
  if [ -n "$line" ]; then
    rid="$(printf "%s" "$line" | grep -oE "RID [0-9]+" | grep -oE "[0-9]+")"
    dur="$(printf "%s" "$line" | grep -oE "took [0-9.]+s" | grep -oE "[0-9.]+")"
    [ -n "$rid" ] && emit "xx_cmix_last_round $rid"
    [ -n "$dur" ] && emit "xx_cmix_last_round_seconds $dur"
  fi
  pc="$(tail -c 5000000 "$LOGDIR/cmix.log" 2>/dev/null | grep -ac "panic:" 2>/dev/null || true)"
  [ -z "$pc" ] && pc=0
  emit "xx_cmix_recoverable_failures $pc"
fi

if [ -f "$LOGDIR/cmix-err.log" ]; then emit "xx_cmix_err_file_present 1"; else emit "xx_cmix_err_file_present 0"; fi

if [ -d "$LOGDIR" ]; then
  for f in "$LOGDIR"/*.log; do
    [ -f "$f" ] || continue
    n="$(basename "$f")"; s="$(stat -c %s "$f" 2>/dev/null || echo 0)"
    emit "xx_log_bytes{file=\"$n\"} $s"
  done
fi

# --- gateway liveness: gossip rounds advance while it is doing its job -----
if [ -f "$LOGDIR/gateway.log" ]; then
  gl="$(tail -c 300000 "$LOGDIR/gateway.log" 2>/dev/null | grep -a "Gossip received for round" | tail -1)"
  if [ -n "$gl" ]; then
    gr="$(printf "%s" "$gl" | grep -oE "round [0-9]+" | head -1 | grep -oE "[0-9]+")"
    [ -n "$gr" ] && emit "xx_gateway_last_round $gr"
  fi
  # traffic from its OWN node — liveness, separate from the peering metric above
  ll="$(tail -c 300000 "$LOGDIR/gateway.log" 2>/dev/null | grep -a "Local round data for round" | tail -1)"
  if [ -n "$ll" ]; then
    lr="$(printf "%s" "$ll" | grep -oE "round [0-9]+" | head -1 | grep -oE "[0-9]+")"
    [ -n "$lr" ] && emit "xx_gateway_local_round $lr"
  fi
  gw="$(tail -c 5000000 "$LOGDIR/gateway.log" 2>/dev/null | grep -ac "^WARN" 2>/dev/null || true)"
  [ -z "$gw" ] && gw=0
  emit "xx_gateway_warn_lines $gw"
fi

# --- chain state via the node local RPC -------------------------------------
RPCU="http://localhost:9933"
SLK="0x5f3e4907f716ac89b6347d15ececedca042824170a5db4381fe3395039cabd24"
VALK="0xcec5070d609dd3497f72bde07fc96ba088dcde934c658227ee1dfafcd6e16903"
rpc(){ curl -s -m 5 -H "Content-Type: application/json" -d "$1" "$RPCU" 2>/dev/null; }
sresp="$(rpc "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"state_getKeys\",\"params\":[\"$SLK\"]}")"
if printf "%s" "$sresp" | grep -q "\"result\""; then
  emit "xx_chain_rpc_up 1"
  # each pending slash is one storage key; none at all means the array is empty
  emit "xx_chain_pending_slashes $(printf "%s" "$sresp" | grep -o "0x[0-9a-f]\{40,\}" | wc -l)"
  vresp="$(rpc "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"state_getStorage\",\"params\":[\"$VALK\"]}")"
  vhex="$(printf "%s" "$vresp" | sed -n "s/.*\"result\":\"0x\([0-9a-f]*\)\".*/\1/p" | head -c 8)"
  if [ -n "$vhex" ]; then
    vb0=$((0x${vhex:0:2})); vm=$((vb0 & 3))
    if [ $vm -eq 0 ]; then vn=$((vb0 >> 2))
    elif [ $vm -eq 1 ]; then vn=$(( (0x${vhex:2:2} * 256 + vb0) >> 2 ))
    elif [ $vm -eq 2 ]; then vn=$(( (0x${vhex:6:2}*16777216 + 0x${vhex:4:2}*65536 + 0x${vhex:2:2}*256 + vb0) >> 2 ))
    else vn=""; fi
    [ -n "$vn" ] && emit "xx_chain_active_validators $vn"
  fi
else
  emit "xx_chain_rpc_up 0"
fi

# --- can this gateway still find its node? ----------------------------------
# the nodes sit on dynamic IPs behind dyndns while the gateways are static, so
# a stale record is a real failure mode: the address moves and the gateway
# carries on dialling the old one. resolution alone is not enough to know.
GWCFG=/opt/xxnetwork/config/gateway.yaml
if [ -f "$GWCFG" ]; then
  naddr="$(sed -n "s/^cmixAddress:[[:space:]]*\"\{0,1\}\([^\"]*\)\"\{0,1\}[[:space:]]*$/\1/p" "$GWCFG")"
  nhost="${naddr%%:*}"; nport="${naddr##*:}"; nname="${nhost%%.*}"
  if [ -n "$nhost" ] && [ -n "$nport" ]; then
    nip="$(getent ahostsv4 "$nhost" 2>/dev/null | head -1 | cut -d" " -f1)"
    if printf "%s" "$nip" | grep -qE "^([0-9]{1,3}[.]){3}[0-9]{1,3}$"; then
      emit "xx_node_dns_resolves{node=\"$nname\"} 1"
      emit "xx_node_dns_ip{node=\"$nname\",host=\"$nhost\",ip=\"$nip\"} 1"
      if timeout 3 bash -c "exec 3<>/dev/tcp/$nip/$nport" 2>/dev/null; then
        emit "xx_node_reachable{node=\"$nname\"} 1"
      else
        emit "xx_node_reachable{node=\"$nname\"} 0"
      fi
    else
      emit "xx_node_dns_resolves{node=\"$nname\"} 0"
      emit "xx_node_reachable{node=\"$nname\"} 0"
    fi
  fi
fi

# --- config drift: is log rotation set up, and is it actually firing? ------
if [ -f /etc/logrotate.d/xxnetwork ]; then
  emit "xx_logrotate_rule_present 1"
else
  emit "xx_logrotate_rule_present 0"
fi
newest=""
for f in "$LOGDIR"/*.log.1 "$LOGDIR"/*.log.1.gz "$LOGDIR"/*.log.*.gz; do
  [ -f "$f" ] || continue
  if [ -z "$newest" ] || [ "$f" -nt "$newest" ]; then newest="$f"; fi
done
if [ -n "$newest" ]; then
  emit "xx_log_last_rotation_seconds $(( $(date +%s) - $(stat -c %Y "$newest") ))"
  emit "xx_log_ever_rotated 1"
else
  emit "xx_log_ever_rotated 0"
fi

# --- GPU: card stats plus whether cMix actually holds the card -------------
if command -v nvidia-smi >/dev/null 2>&1; then
  idx=0
  nvidia-smi --query-gpu=name,temperature.gpu,utilization.gpu,memory.used,memory.total,power.draw,clocks.sm \
             --format=csv,noheader,nounits 2>/dev/null | while IFS="," read -r nm tp ut mu mt pw ck; do
    nm="$(echo "$nm" | sed "s/^ *//;s/ *$//")"
    for v in tp ut mu mt pw ck; do eval "$v=\$(echo \$$v | tr -d \" \")"; done
    L="{gpu=\"$idx\",model=\"$nm\"}"
    emit "xx_gpu_present$L 1"
    [ -n "$tp" ] && emit "xx_gpu_temp_celsius$L $tp"
    [ -n "$ut" ] && emit "xx_gpu_utilization_percent$L $ut"
    [ -n "$mu" ] && emit "xx_gpu_memory_used_bytes$L $(( mu * 1048576 ))"
    [ -n "$mt" ] && emit "xx_gpu_memory_total_bytes$L $(( mt * 1048576 ))"
    [ -n "$pw" ] && emit "xx_gpu_power_watts$L $pw"
    [ -n "$ck" ] && emit "xx_gpu_clock_mhz$L $ck"
    idx=$((idx+1))
  done
  ca="$(nvidia-smi --query-compute-apps=process_name,used_memory --format=csv,noheader,nounits 2>/dev/null \
        | grep -i cmix | head -1)"
  if [ -n "$ca" ]; then
    cm="$(printf "%s" "$ca" | awk -F, "{gsub(/ /,\"\",\$2); print \$2}")"
    emit "xx_gpu_compute_attached 1"
    case "$cm" in ""|*[!0-9]*) : ;; *) emit "xx_gpu_compute_memory_bytes $(( cm * 1048576 ))" ;; esac
  else
    emit "xx_gpu_compute_attached 0"
  fi
else
  emit "xx_gpu_present 0"
fi
# --- certificate expiry ------------------------------------------------------
# one gateway ran on an expired certificate for a long time and
# nothing noticed. as a metric it cannot go unseen again. files prefixed old.
# are deliberately retired copies from a rotation, so they are skipped.
for c in /opt/xxnetwork/cred/*.crt; do
  [ -f "$c" ] || continue
  b="$(basename "$c")"
  case "$b" in old.*) continue;; esac
  e="$(openssl x509 -in "$c" -noout -enddate 2>/dev/null | cut -d= -f2)"
  [ -z "$e" ] && continue
  ts="$(date -d "$e" +%s 2>/dev/null)"
  [ -n "$ts" ] && emit "xx_cert_expiry_seconds{cert=\"$b\"} $ts"
done

# --- can this host resolve what xx network depends on? ----------------------
# OUTBOUND resolution, unlike xx_node_dns_resolves which is about a gateway
# finding its paired node. Every host needs these regardless of how it is
# itself addressed. Override per host with /etc/xxops/dns-names, one per line.
# Only names VERIFIED to resolve are listed - a dead name would report a
# permanent 0 and become noise. binaries.xx.network was dropped for that
# reason: it resolves nowhere, including from a public resolver.
XXDNS_FILE=/etc/xxops/dns-names
if [ -r "$XXDNS_FILE" ]; then
  xxdns_names="$(grep -vE "^[[:space:]]*(#|$)" "$XXDNS_FILE" | tr "\n" " ")"
else
  xxdns_names="scheduling.mainnet.cmix.rip auth.mainnet.cmix.rip"
fi
for xxdns_n in $xxdns_names; do
  xxdns_ip="$(getent ahostsv4 "$xxdns_n" 2>/dev/null | head -1 | cut -d" " -f1)"
  if printf "%s" "$xxdns_ip" | grep -qE "^([0-9]{1,3}[.]){3}[0-9]{1,3}$"; then
    emit "xx_dns_resolves{name=\"$xxdns_n\"} 1"
  else
    emit "xx_dns_resolves{name=\"$xxdns_n\"} 0"
  fi
done

# --- wrapper signed-command verification (pyOpenSSL) ---
# pyOpenSSL 24.3.0 removed OpenSSL.crypto.verify, which the xx wrapper needs to
# check the signature on command.jsonl. Test the WRAPPER's environment, not this
# script's: the wrapper's deps live in the service user's ~/.local. stderr is
# redirected because hasattr alone raises a DeprecationWarning on 24.x.
xxv_ok=-1
xxv_ver="unknown"
xxv_pid="$(pgrep -f "/opt/xxnetwork/.*wrapper[.]py" 2>/dev/null | head -1)"
if [ -n "$xxv_pid" ]; then
  xxv_user="$(ps -o user= -p "$xxv_pid" 2>/dev/null | tr -d " ")"
  xxv_home="$(getent passwd "$xxv_user" 2>/dev/null | cut -d: -f6)"
  xxv_sp=""
  if [ -n "$xxv_home" ]; then
    xxv_sp="$(ls -d "$xxv_home"/.local/lib/python3*/site-packages 2>/dev/null | head -1)"
  fi
  xxv_ver="$(PYTHONPATH="$xxv_sp" python3 -c 'import OpenSSL,sys; sys.stdout.write(OpenSSL.__version__)' 2>/dev/null)"
  [ -z "$xxv_ver" ] && xxv_ver="unknown"
  if PYTHONPATH="$xxv_sp" python3 -c 'import OpenSSL.crypto as c,sys; sys.exit(0 if hasattr(c,"verify") else 1)' 2>/dev/null; then
    xxv_ok=1
  else
    xxv_ok=0
  fi
fi
emit "xx_wrapper_cmd_verify_ok $xxv_ok"
emit "xx_pyopenssl_version_info{version=\"$xxv_ver\"} 1"

# --- can this host's path actually carry its configured MTU? ----------------
# Some nodes were configured for 1500 on PPPoE paths that carry only 1492.
# Large TCP segments were dropped upstream, the ICMP that would trigger Path
# MTU Discovery was filtered, and cMix precomputation streams died partway
# through. The host looked healthy in every other respect while failing rounds
# at twice the fleet rate.
#
# Three states on purpose. A large ping failing proves nothing by itself -
# ICMP may be blocked - so a small ping is the control:
#   1 = full-size crosses, 0 = only small crosses (THE FAULT), -1 = cannot tell
mtu_iface=$(ip route get 1.1.1.1 2>/dev/null | grep -oP 'dev \K\S+' | head -1)
if [ -n "$mtu_iface" ]; then
  mtu_conf=$(cat "/sys/class/net/$mtu_iface/mtu" 2>/dev/null)
  case "$mtu_conf" in ''|*[!0-9]*) mtu_conf="" ;; esac
  if [ -n "$mtu_conf" ]; then
    emit "xx_path_mtu_configured_bytes $mtu_conf"
    mtu_target=$(cat /etc/xxops/mtu-target 2>/dev/null | head -1)
    [ -n "$mtu_target" ] || mtu_target=1.1.1.1
    # 28 bytes of IP + ICMP header
    # Success needs one packet, failure needs three. Any large packet
    # crossing proves the path carries that size, so stop at the first
    # success. Declaring a fault is the expensive claim - it costs a network
    # config change - so it has to survive three attempts, and then a small
    # ping has to succeed to separate a real MTU limit from blocked ICMP.
    #
    # The single-ping version reported 14 of every nodes faulty; twelve of them
    # flipped value within two hours.
    mtu_big=0
    for _ in 1 2 3; do
      if ping -c1 -W2 -M do -s $(( mtu_conf - 28 )) "$mtu_target" >/dev/null 2>&1; then
        mtu_big=1; break
      fi
    done
    if [ "$mtu_big" = "1" ]; then
      emit "xx_path_mtu_ok 1"
    else
      mtu_small=0
      for _ in 1 2 3; do
        if ping -c1 -W2 -M do -s 1200 "$mtu_target" >/dev/null 2>&1; then
          mtu_small=1; break
        fi
      done
      if [ "$mtu_small" = "1" ]; then
        emit "xx_path_mtu_ok 0"
      else
        emit "xx_path_mtu_ok -1"
      fi
    fi
  fi
fi

# --- storage health ---------------------------------------------------------
# A node's EXT4 root went read-only overnight and took SSH, TeamViewer and the
# services with it. Nothing saw it coming. These are the counters that would
# have shown it, and the ones worth watching for the next time.
#
# The cumulative values are for DELTAS, not thresholds: a host sitting at 356
# error-log entries with zero media errors is fine and always has been. 356
# means nothing. 356 -> 357 is an event.
root_opts=$(findmnt -no OPTIONS / 2>/dev/null)
if [ -n "$root_opts" ]; then
  # Comma-delimited on purpose. `grep -qw ro` looks right and is WRONG: a
  # healthy root carries errors=remount-ro, and - is not a word character, so
  # -w finds "ro" inside "remount-ro" and calls every healthy host read-only.
  case ",$root_opts," in
    *",ro,"*) emit "xx_storage_root_writable 0" ;;
    *)        emit "xx_storage_root_writable 1" ;;
  esac
fi

# Resolve the device rather than assuming one. Hosts differ - NVMe, SATA, a
# virtual disk - so a host with no NVMe simply skips this and the metrics are
# absent, which means "not applicable" rather than "broken".
root_src=$(findmnt -no SOURCE / 2>/dev/null)
nvme_ctrl=""
case "$root_src" in
  /dev/nvme*) nvme_ctrl=$(printf '%s' "$root_src" |
                          grep -oE '^/dev/nvme[0-9]+') ;;
esac
if [ -n "$nvme_ctrl" ] && command -v nvme >/dev/null 2>&1; then
  nvme_name=$(basename "$nvme_ctrl")
  nvme_state=$(cat "/sys/class/nvme/$nvme_name/state" 2>/dev/null)
  if [ -n "$nvme_state" ]; then
    if [ "$nvme_state" = "live" ]; then
      emit "xx_nvme_controller_live 1"
    else
      emit "xx_nvme_controller_live 0"
    fi
  fi
  nvme_smart=$(nvme smart-log "$nvme_ctrl" 2>/dev/null)
  if [ -n "$nvme_smart" ]; then
    # First integer on the matching line, commas stripped. Some nvme-cli
    # builds print "44 C (317 Kelvin)" and some print "1,234", so neither a
    # bare gsub nor a bare grep would survive both.
    nvme_val(){
      printf '%s\n' "$nvme_smart" |
        awk -F: -v k="^$1[[:space:]]*$" '$1 ~ k {print $2; exit}' |
        tr -d ',' | grep -oE '[0-9]+' | head -1
    }
    for nvme_f in critical_warning:xx_nvme_critical_warning \
                  media_errors:xx_nvme_media_errors \
                  num_err_log_entries:xx_nvme_error_log_entries \
                  percentage_used:xx_nvme_percentage_used \
                  available_spare:xx_nvme_available_spare_pct \
                  temperature:xx_nvme_temperature_celsius; do
      nvme_k=${nvme_f%%:*}
      nvme_m=${nvme_f#*:}
      nvme_v=$(nvme_val "$nvme_k")
      [ -n "$nvme_v" ] && emit "$nvme_m $nvme_v"
    done
  fi
fi

# --- what CPU is this? ------------------------------------------------------
# node_exporter reports a core count and clock speeds but never the model, so
# there is no way to tell a Ryzen 5 from a Threadripper from metrics alone.
# /proc/cpuinfo has it everywhere.
#
# A label on a constant 1, like substrate_build_info - the value means
# nothing, the label is the point, and the Changes tab already spots label
# changes on series of this shape, so a CPU swap surfaces for free.
#
# Quotes and backslashes are stripped: the textfile collector rejects the
# ENTIRE FILE on one malformed line, which would take every other metric on
# this host with it.
cpu_model=$(awk -F: '/^model name/{sub(/^[ \t]+/,"",$2); print $2; exit}' \
            /proc/cpuinfo 2>/dev/null | tr -d '"\\')
if [ -n "$cpu_model" ]; then
  emit "xx_cpu_info{model=\"$cpu_model\"} 1"
fi

# --- link speed -------------------------------------------------------------
# The test does NOT run here. It runs on its own timer and leaves a state file;
# this block only publishes what it finds. That split matters: a 60s producer
# cannot run an 8-second iperf3, and a reading must survive a reboot rather
# than vanishing until the next scheduled test.
#
# Two modes, two different questions. health is the path to this node's own
# gateway - the production path, capped by the gateway's VPS uplink, and the
# one an alert should fire on. capacity is the path to a peer node, where both
# ends are fast, and is the only measurement that reflects the real link.
#
# ABSENT MEANS NOT APPLICABLE, as everywhere else here. A gateway has no state
# file, a freshly paired node has no reading yet, and neither is a fault.
ls_state=/var/lib/xxops/linkspeed.json
if [ -r "$ls_state" ] && command -v python3 >/dev/null 2>&1; then
  ls_out=$(python3 - "$ls_state" <<'LSPY' 2>/dev/null
import json, sys, time

try:
    with open(sys.argv[1], encoding="utf-8") as fh:
        state = json.load(fh)
except Exception:
    raise SystemExit(0)          # unreadable or half-written: emit nothing
if not isinstance(state, dict):
    raise SystemExit(0)

now = int(time.time())
out = []

def esc(s):
    return str(s).replace("\\", "\\\\").replace('"', '\\"')

for mode in ("health", "capacity"):
    e = state.get(mode)
    if not isinstance(e, dict):
        continue

    # The peer is an INFO metric, never a label on the values. The capacity
    # peer rotates, and a rotating label would start a new time series each
    # time and break every graph and rate() over the change.
    peer = e.get("peer")
    if peer:
        out.append('xx_linkspeed_peer{mode="%s",peer="%s"} 1'
                   % (mode, esc(peer)))

    # An error that has never been followed by a success leaves no reading.
    # Publish the failure age so staleness is visible, and NOTHING ELSE --
    # emitting 0 Mbps here is exactly the false page this design avoids.
    if e.get("last_error_ts"):
        out.append('xx_linkspeed_error_age_seconds{mode="%s"} %d'
                   % (mode, max(0, now - int(e["last_error_ts"]))))

    ts = e.get("ts")
    up, down = e.get("up_mbps"), e.get("down_mbps")
    if ts is None or up is None or down is None:
        continue

    # Age is what makes a stale reading legible. Without it a six-month-old
    # 800 Mbps looks exactly like a fresh one.
    out.append('xx_linkspeed_age_seconds{mode="%s"} %d'
               % (mode, max(0, now - int(ts))))
    out.append('xx_linkspeed_up_mbps{mode="%s"} %s' % (mode, float(up)))
    out.append('xx_linkspeed_down_mbps{mode="%s"} %s' % (mode, float(down)))

    # A relayed path measures a DERP server, not the link. The alert rule
    # gates on this rather than on the speed alone.
    path = e.get("path")
    if path in ("direct", "relay"):
        out.append('xx_linkspeed_path_direct{mode="%s"} %d'
                   % (mode, 1 if path == "direct" else 0))

print("\n".join(out))
LSPY
)
  [ -n "$ls_out" ] && printf '%s\n' "$ls_out" >> "$TMP"
fi

emit "xx_textfile_producer_last_run $(date +%s)"
mv "$TMP" "$OUT"
chmod 644 "$OUT"
