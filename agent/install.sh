#!/bin/bash
# xxOps agent installer.
#
# Run on each host as:
#   curl -sL https://raw.githubusercontent.com/Bulldog-Master/xxOps/main/agent/install.sh \
#     | sudo bash -s -- <monitor>:8080/agent
#
# The monitor address is an ARGUMENT, not a default. sudo strips the
# environment, so passing it as a variable in front of the pipe would
# silently not arrive.
#
# The agent runs as a dedicated unprivileged user with exactly ONE capability,
# CAP_DAC_READ_SEARCH, which bypasses file-read and directory-search checks and
# nothing else. It can read the 0700 xx directories it needs and cannot write,
# cannot change ownership, and cannot execute as anyone else. Verified on both
# a node and a gateway: reads succeed, writes are refused.
#
# That matters more for whoever installs this next than it does here. On a
# fleet you own, root plus a fixed allowlist is arguably proportionate; someone
# installing in six months will run whatever the installer does and will not
# think to ask. So the installer does the safe thing.
#
# Safe to re-run: it upgrades an existing install in place.
set -e

MON="${1:-${XXOPS_MONITOR:-}}"
if [ -z "$MON" ]; then
  echo "xxOps agent installer" >&2
  echo "" >&2
  echo "  usage: sudo bash install.sh <monitor>:8080/agent [bind-address]" >&2
  echo "" >&2
  echo "The address is passed as an argument because sudo strips the" >&2
  echo "environment, so XXOPS_MONITOR in front of the pipe would not reach" >&2
  echo "this script. Use sudo -E if you prefer the variable." >&2
  exit 1
fi
AGENT_USER=xxops-agent

if [ "$(id -u)" != "0" ]; then
  echo "run this with sudo"
  exit 1
fi

# --- who does Prometheus think this host is? --------------------------------
LABEL="$(grep -A1 'target_label = "instance"' /etc/alloy/config.alloy 2>/dev/null \
         | sed -n 's/.*replacement *= *"\([^"]*\)".*/\1/p' | head -1)"
if [ -z "$LABEL" ]; then
  echo "could not read this host's instance label from /etc/alloy/config.alloy"
  exit 1
fi

# Which address should the agent listen on? Whatever the monitor reaches this
# host by. A mesh VPN is the common answer but not the only one, so try in
# order: what you told us, what tailscale says, and the source address of the
# default route. Only give up if all three come back empty.
TSIP="${2:-}"
[ -n "$TSIP" ] || TSIP="$(tailscale ip -4 2>/dev/null | head -1)"
[ -n "$TSIP" ] || TSIP="$(ip -4 route get 192.0.2.1 2>/dev/null \
                          | sed -n 's/.* src \([0-9.]*\).*/\1/p' | head -1)"
if [ -z "$TSIP" ]; then
  echo "could not work out which address to bind the agent to." >&2
  echo "" >&2
  echo "pass it yourself, as the second argument:" >&2
  echo "  sudo bash install.sh <monitor>:8080/agent <this-host-address>" >&2
  exit 1
fi

# --- the account it runs as -------------------------------------------------
if ! id -u "$AGENT_USER" >/dev/null 2>&1; then
  useradd -r -s /usr/sbin/nologin -M -d /nonexistent "$AGENT_USER"
  echo "created the $AGENT_USER account"
fi

echo "installing the xxOps agent as ${LABEL}, listening on ${TSIP}:8181"

# --- fetch ------------------------------------------------------------------
mkdir -p /etc/xxops
curl -sfS "http://${MON}/allowed_signers" -o /etc/xxops/allowed_signers.new
curl -sfS "http://${MON}/xxops-agent.py"  -o /usr/local/bin/xxops-agent.py.new
curl -sfS "http://${MON}/xxops-update-node.sh"    -o /usr/local/bin/xxops-update-node.sh.new
curl -sfS "http://${MON}/xxops-update-gateway.sh" -o /usr/local/bin/xxops-update-gateway.sh.new

# only replace once both downloads succeeded, so a half-fetch cannot break a
# working agent
mv /etc/xxops/allowed_signers.new /etc/xxops/allowed_signers
mv /usr/local/bin/xxops-agent.py.new /usr/local/bin/xxops-agent.py
chmod 644 /etc/xxops/allowed_signers
chmod 755 /usr/local/bin/xxops-agent.py
mv /usr/local/bin/xxops-update-node.sh.new    /usr/local/bin/xxops-update-node.sh
mv /usr/local/bin/xxops-update-gateway.sh.new /usr/local/bin/xxops-update-gateway.sh
chmod 755 /usr/local/bin/xxops-update-node.sh /usr/local/bin/xxops-update-gateway.sh

# --- run it -----------------------------------------------------------------
cat > /etc/systemd/system/xxops-agent.service <<UNIT
[Unit]
Description=xxOps agent
After=network-online.target tailscaled.service

[Service]
Type=simple
User=${AGENT_USER}
Group=${AGENT_USER}
Environment=XXOPS_AGENT_HOST=${LABEL}
Environment=XXOPS_AGENT_BIND=${TSIP}
Environment=XXOPS_AGENT_PORT=8181
ExecStart=/usr/bin/python3 /usr/local/bin/xxops-agent.py
Restart=always
RestartSec=5

# read what it needs, and nothing else. no write, no chown.
AmbientCapabilities=CAP_DAC_READ_SEARCH
CapabilityBoundingSet=CAP_DAC_READ_SEARCH CAP_SETUID CAP_SETGID
# NoNewPrivileges is deliberately NOT set. It blocks setuid binaries, and sudo
# is one - the two cannot both be true. The boundary is the sudoers file, which
# permits nine exact command lines and nothing else.
# Everything below is chosen to NOT imply NoNewPrivileges. systemd turns that
# on implicitly for PrivateDevices, ProtectKernelTunables, ProtectControlGroups,
# RestrictAddressFamilies and LockPersonality - and it blocks sudo. The same
# RestrictAddressFamilies line also blocked logger, which writes to a unix
# socket, so the audit trail was silently empty.
ProtectSystem=full
ProtectHome=true
PrivateTmp=true
ReadWritePaths=/run

[Install]
WantedBy=multi-user.target
UNIT

# --- what it is allowed to change, and nothing else -------------------------
# A broken sudoers file can lock this host out of sudo entirely, so it is
# validated before it is installed. Written to a temp file, checked, then moved.
SYSTEMCTL="$(command -v systemctl)"
SUDOTMP="$(mktemp)"
cat > "$SUDOTMP" <<SUDO
# xxOps agent - bounce xx services and nothing else.
# Written by the xxOps installer. Remove this file to revoke.
${AGENT_USER} ALL=(root) NOPASSWD: ${SYSTEMCTL} restart xxnetwork-gateway
${AGENT_USER} ALL=(root) NOPASSWD: ${SYSTEMCTL} stop xxnetwork-cmix
${AGENT_USER} ALL=(root) NOPASSWD: ${SYSTEMCTL} start xxnetwork-cmix
${AGENT_USER} ALL=(root) NOPASSWD: ${SYSTEMCTL} restart xxnetwork-chain
${AGENT_USER} ALL=(root) NOPASSWD: ${SYSTEMCTL} start xxnetwork-gateway
${AGENT_USER} ALL=(root) NOPASSWD: ${SYSTEMCTL} stop xxnetwork-gateway
${AGENT_USER} ALL=(root) NOPASSWD: ${SYSTEMCTL} start xxnetwork-chain
${AGENT_USER} ALL=(root) NOPASSWD: ${SYSTEMCTL} stop xxnetwork-chain
${AGENT_USER} ALL=(root) NOPASSWD: ${SYSTEMCTL} restart xxnetwork-cmix
${AGENT_USER} ALL=(root) NOPASSWD: /usr/local/bin/xxops-update-node.sh
${AGENT_USER} ALL=(root) NOPASSWD: /usr/local/bin/xxops-update-gateway.sh
SUDO
if visudo -cf "$SUDOTMP" >/dev/null 2>&1; then
  install -m 440 -o root -g root "$SUDOTMP" /etc/sudoers.d/xxops-agent
  echo "granted: bounce xx services only"
else
  echo "the sudoers file did not validate - NOT installing it"
  echo "the agent will work read-only; actions will be refused"
fi
rm -f "$SUDOTMP"

systemctl daemon-reload
systemctl enable xxops-agent >/dev/null 2>&1
systemctl restart xxops-agent
sleep 2

# --- prove it is up AND that it can still read what it needs ----------------
if ! curl -sf "http://${TSIP}:8181/health"; then
  echo "agent did not answer - check: journalctl -u xxops-agent -n 20"
  exit 1
fi
echo
RUNAS="$(systemctl show xxops-agent -p User --value)"
echo "agent is running as ${RUNAS:-root}"
