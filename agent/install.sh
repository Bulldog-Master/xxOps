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
# The agent runs as a dedicated unprivileged user with NO capabilities. It
# reaches the 0700 xx directories through ACLs granting traverse on
# /opt/xxnetwork and read on cred/ and log/ - set by this script, on every
# run. It cannot write, cannot change ownership, and cannot execute as anyone
# else.
#
# It used to hold CAP_DAC_READ_SEARCH, described here as reading "the
# directories it needs and nothing else". That was wrong: the capability
# bypasses read and search checks across the whole filesystem, so a flaw in
# this network-facing service meant reading anything root could read. The
# ACLs grant only what it uses, and the private key beside those certificates
# stays -rw------- and unreadable. Verified on a real host before the
# capability was removed.
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


# --- what it may read -------------------------------------------------------
# Scoped traverse instead of a filesystem-wide capability.
#
# /opt/xxnetwork and its subdirectories are drwx------ owned by the validator
# user, so the agent cannot reach them at all by default. It does not need to
# READ any file there: the certificates are already world-readable, and the
# private key beside them is -rw------- and stays that way. What it needs is
# permission to TRAVERSE.
#
# Set on every run, not just the first: these are ACLs on directories this
# project does not own, and a restore of /opt/xxnetwork from backup drops
# them silently. The failure then looks like a certificate problem rather
# than a permissions one.
grant_read() {
  # $1 = path, $2 = acl (x or rx). Missing paths are not an error - a node has
  # no gateway log and vice versa.
  [ -e "$1" ] || return 0
  setfacl -m "u:${AGENT_USER}:$2" "$1" 2>/dev/null \
    || echo "  could not set an ACL on $1 - is the filesystem mounted with acl?" >&2
}

# setfacl comes from the acl package, which is not installed everywhere -
# Ubuntu server images vary. INSTALLED, not warned about: the agent has no
# capability any more, so without these ACLs it cannot read a certificate at
# all, and an operator is not going to notice one warning in a long install.
if ! command -v setfacl >/dev/null 2>&1; then
  echo "  installing acl (needed to grant scoped access)"
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq acl >/dev/null 2>&1 || true
fi

if ! command -v setfacl >/dev/null 2>&1; then
  echo "REFUSING: setfacl is not available and could not be installed." >&2
  echo "" >&2
  echo "The agent runs with no capabilities and reaches /opt/xxnetwork only" >&2
  echo "through ACLs. Without them the certificate and log actions cannot" >&2
  echo "work at all. Install the acl package and re-run." >&2
  exit 1
fi

grant_read /opt/xxnetwork        x
grant_read /opt/xxnetwork/cred   rx
grant_read /opt/xxnetwork/log    rx
# config/ is how the agent knows whether it is a node or a gateway - it looks
# for gateway.yaml. Without this a gateway identifies as a node and offers the
# wrong actions, with no error anywhere: the test just comes back false.
grant_read /opt/xxnetwork/config rx

# Prove it took, rather than assuming setfacl succeeding means the agent can
# read. A filesystem mounted without acl support accepts the command and does
# nothing; so does a restore that dropped the entries. This is the check that
# would have caught the missing package, whatever the cause had been.
if [ -d /opt/xxnetwork/cred ]; then
  if runuser -u "${AGENT_USER}" -- test -r /opt/xxnetwork/cred 2>/dev/null \
     || su -s /bin/sh -c 'test -r /opt/xxnetwork/cred' "${AGENT_USER}" 2>/dev/null; then
    echo "  granted ${AGENT_USER} traverse into /opt/xxnetwork (not the keys)"
  else
    echo "REFUSING: the ACLs were set but ${AGENT_USER} still cannot read" >&2
    echo "/opt/xxnetwork/cred." >&2
    echo "" >&2
    echo "Is that filesystem mounted with acl support? Without this the" >&2
    echo "certificate and log actions will fail on this host, and the agent" >&2
    echo "no longer has a capability to fall back on." >&2
    exit 1
  fi
else
  echo "  no /opt/xxnetwork/cred here - nothing to grant"
fi

# --- fetch and verify -------------------------------------------------------
# These files come over plain HTTP, and two of them run as root through
# sudoers entries. Anyone able to modify that traffic could otherwise replace
# one and own this machine.
#
# So nothing is installed until it matches a manifest MACed with the enrolment
# token. The token came to you through the app in a browser and you typed it
# here by hand - it never crossed this wire, so someone sitting on the wire
# cannot forge a manifest that verifies.
TOKEN="${3:-}"
if [ -z "$TOKEN" ]; then
  echo "REFUSING: no enrolment token." >&2
  echo "" >&2
  echo "Without it these downloads cannot be verified, and two of them run as" >&2
  echo "root. Get the token from the app's Commands tab and pass it:" >&2
  echo "" >&2
  echo "  sudo bash install.sh <monitor>:8080/agent <this-host-address> <TOKEN>" >&2
  echo "" >&2
  echo "If you only want metrics and alerts, re-run the host installer with" >&2
  echo "--skip-agent instead. No agent, no unverified download." >&2
  exit 1
fi

mkdir -p /etc/xxops
MAN="$(mktemp)"
curl -sfS "http://${MON}/manifest" -o "$MAN" || {
  echo "REFUSING: could not fetch the manifest from ${MON}." >&2
  echo "An older monitor does not serve one - update it first." >&2
  rm -f "$MAN"; exit 1; }

# Verify the manifest before trusting a single line of it.
man_mac="$(sed -n 's/^#mac //p' "$MAN" | head -1)"
# Piped, NOT through a variable. `man_body="$(grep ...)"` would strip the
# trailing newline - command substitution always does - and that newline is
# inside what the server MACs. Hashing the body without it fails every time,
# with a message that reads like a wrong token.
calc_mac="$(grep -v '^#mac ' "$MAN" \
            | openssl dgst -sha256 -hmac "$TOKEN" -r 2>/dev/null \
            | cut -d' ' -f1)"
if [ -z "$man_mac" ] || [ "$man_mac" != "$calc_mac" ]; then
  echo "REFUSING: the manifest did not verify." >&2
  echo "" >&2
  echo "Either the token is wrong, or something between this host and the" >&2
  echo "monitor altered the manifest. Nothing has been installed." >&2
  echo "Check the token on the app's Commands tab and try again." >&2
  rm -f "$MAN"; exit 1
fi

fetch_verified() {
  # $1 = name on the monitor, $2 = where to put it
  curl -sfS "http://${MON}/$1" -o "$2" || {
    echo "REFUSING: could not fetch $1 from ${MON}." >&2; return 1; }
  want="$(awk -v n="$1" '$2 == n {print $1}' "$MAN" | head -1)"
  got="$(sha256sum "$2" | cut -d' ' -f1)"
  if [ -z "$want" ]; then
    echo "REFUSING: $1 is not in the manifest." >&2; rm -f "$2"; return 1
  fi
  if [ "$want" != "$got" ]; then
    echo "REFUSING: $1 does not match the manifest." >&2
    echo "  expected $want" >&2
    echo "  got      $got" >&2
    echo "Something altered it in transit. Nothing has been installed." >&2
    rm -f "$2"; return 1
  fi
}

# A refusal part-way through leaves earlier .new files on disk. They are never
# moved into place, so nothing is installed either way - but clear them, so a
# failed run leaves the machine exactly as it found it.
abort_fetch() {
  rm -f "$MAN"         /etc/xxops/allowed_signers.new         /usr/local/bin/xxops-agent.py.new         /usr/local/bin/xxops-update-node.sh.new         /usr/local/bin/xxops-update-gateway.sh.new
  exit 1
}

fetch_verified allowed_signers         /etc/xxops/allowed_signers.new || abort_fetch
fetch_verified xxops-agent.py          /usr/local/bin/xxops-agent.py.new || abort_fetch
fetch_verified xxops-update-node.sh    /usr/local/bin/xxops-update-node.sh.new || abort_fetch
fetch_verified xxops-update-gateway.sh /usr/local/bin/xxops-update-gateway.sh.new || abort_fetch
rm -f "$MAN"
echo "  all four files verified against the monitor's manifest"

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

# No ambient capabilities. It used to hold CAP_DAC_READ_SEARCH, described
# here as "read what it needs" - it is not that. It bypasses discretionary
# read and search checks across the WHOLE filesystem, so a flaw in this
# network-facing service meant reading anything root can read, including
# private keys.
#
# What it needs instead is granted above with ACLs: traverse into
# /opt/xxnetwork, read on cred/ and log/. The private key beside those
# certificates is -rw------- and stays unreadable, which is the point.
#
# CAP_SETUID and CAP_SETGID remain in the bounding set because sudo needs
# them, and the sudoers allowlist is the boundary that actually holds.
CapabilityBoundingSet=CAP_SETUID CAP_SETGID
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
# /run for the pid, and one directory of its own for the nonce file that
# stops a signed request being replayed after a restart. Nothing else on the
# filesystem is writable to this service.
ReadWritePaths=/run /var/lib/xxops-agent

[Install]
WantedBy=multi-user.target
UNIT

# --- where it keeps its own state -------------------------------------------
# Used nonces, so a captured signed request cannot be replayed after the agent
# restarts - and it restarts on every update. Not secret, but it records which
# commands ran and when, so nothing else needs to read it.
install -d -m 700 -o "$AGENT_USER" -g "$AGENT_USER" /var/lib/xxops-agent

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
