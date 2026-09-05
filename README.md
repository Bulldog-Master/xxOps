# xxOps

Self-hosted monitoring for xx Network validators.

Community software. **Not affiliated with, endorsed by, or supported by the xx
network team.**

---

## What it is

A validator is a node and a gateway on two separate machines. xxOps collects
from every host, rolls up per validator, and tells you when something needs
you — in language that says what to do, not just what fired.

It runs entirely on your own machines. Nothing is sent anywhere. There is no
service, no account, no telemetry, and no operator but you.

**What it watches:** cMix rounds and their progress, gateway peering and
liveness, chain height and block authoring, certificate expiry, disk and its
trajectory, service state, log growth and rotation, DNS resolution of the
hostnames xx network depends on, and whether the monitoring itself is still
running.

**What it does:** alerts by Telegram, per owner, so someone managing validators
for other people can route each one to its owner. A daily digest. A web app for
the fleet at a glance. A record of what broke before and what fixed it. And a
command layer that can run a fixed set of read-only checks and service actions
on any host without giving anyone a shell.

**What it will not do:** anything involving your wallet or your keys. Recovery
from some failures genuinely requires re-validating in the xx network wallet.
xxOps will tell you that is what is needed. It cannot and should not be able to
do it for you.

## The shape of it

    the monitor        Prometheus, Alertmanager and the xxOps app.
                       One machine. For a small operator this is a
                       gateway you already pay for, not a new VPS.

    each host          Grafana Alloy ships metrics outbound. A small
                       producer writes the things no exporter covers.
                       An agent answers signed requests from the monitor.

Metrics are pushed from hosts to the monitor, so hosts behind home routers on
dynamic addresses work without any inbound access.

## Installing

See [docs/install-guide.md](docs/install-guide.md). Roughly:

    # on the monitor, after installing Prometheus and Alertmanager
    git clone https://github.com/Bulldog-Master/xxOps.git
    cd xxOps
    sudo ./install-monitor.sh --bind <MONITOR-IP>          # shows its plan
    sudo ./install-monitor.sh --bind <MONITOR-IP> --apply

    # on each node and gateway
    curl -sL https://raw.githubusercontent.com/Bulldog-Master/xxOps/main/agent/install.sh \
      | sudo bash -s -- <MONITOR-IP>:8099

Both installers describe what they will do before doing it. Read the second
one before piping it into a shell — you should not run anything from the
internet as root without looking, and it is short.

## Security model

- **The agent takes actions from a fixed catalogue, never a command string.**
  Nothing arriving over the network can become a shell command. Privileged
  actions run through sudoers entries listing exact command lines, validated
  with `visudo` before installation.
- **Requests are signed.** The private key lives on the monitor alone, so a
  compromised host cannot forge instructions to the others.
- **The agent is unprivileged**, with one capability that lets it read the
  files it needs and nothing else.
- **Responses are filtered by who is asking.** A contact invited to the app
  sees their own validators and no trace of anyone else's, enforced on the
  server rather than by hiding tabs.
- **The backend needs no sudo at all.**

The honest limits: whoever holds the signing key can restart services on every
host. The app protects itself, but Prometheus on the monitor has no
authentication of its own, so put the monitor somewhere only you can reach.

## Design notes

**Amber never pages.** cMix crashes and recovers by design; a phone that buzzes
for that is a phone you stop reading. Amber appears in the app and the daily
digest. Only a genuine stop reaches you.

**Thresholds come from measurement.** Every number in the shipped rules was
derived from a real fleet's behaviour over weeks. Guessed thresholds are what
made the early versions noisy.

**A dead producer freezes metrics rather than removing them**, so a stalled
host can look healthy. There is a rule for exactly that, because it happened.

## Status

Running on production validator nodes and gateways since July 2026. Expect
rough edges in installation on machines unlike those — the installers have been
tested against their own failure paths, not against every distribution.

Issues and pull requests welcome.

## Licence

Apache 2.0. See [LICENSE](LICENSE).
