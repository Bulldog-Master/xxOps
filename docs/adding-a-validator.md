# Adding a validator to a monitor you already have

You installed xxOps, it is running, and you have another validator. This is
what changes.

## The short version

**Nothing on the monitor.** Two new hosts, Part 2 of the install guide on each,
and the app finds them by itself.

## What you skip, and why

Part 1 of the install guide is monitor setup. All of it is already done:

- **Prometheus and Alertmanager** are installed and running. A second validator
  does not need a second copy of either.
- **install-monitor.sh** has already configured this monitor. Re-running it is
  harmless but pointless — it changes nothing about how new hosts are found.
- **The signing key** already exists. Do not regenerate it: every agent you
  already have trusts the current one, and a new key would lock you out of all
  of them until each was reinstalled.

Sizing is worth a thought if you are growing past ten validators — the install
guide's table says when the monitor deserves its own machine — but adding one
or two changes nothing.

## What you do

**1. Put the new hosts on your network.** Whatever your hosts use to reach the
monitor — a mesh VPN, or firewall rules and static addresses — the two new
machines need it too, before anything else. See [tailscale.md](tailscale.md) if
you are using a mesh VPN. Nothing changes on the monitor: it does not need to
reach the new hosts, they reach it.

If you use firewall rules rather than a VPN, this is the one place the monitor
IS touched — each new host needs its own allow line on the monitor's firewall,
or it will silently never report.

**2. Run one command on each new host.** The same command on the node and on
the gateway — it works out which it is:

    curl -sL https://raw.githubusercontent.com/Bulldog-Master/xxOps/main/fixes/xxops-host-install.sh \
      | sudo bash -s -- --label <THIS-HOST-LABEL> --monitor <MONITOR-IP> --token <TOKEN>

**That is a dry run.** It prints what it would do on that host and changes
nothing. Read it, then run the same command again with `--apply` on the end:

    curl -sL https://raw.githubusercontent.com/Bulldog-Master/xxOps/main/fixes/xxops-host-install.sh \
      | sudo bash -s -- --label <THIS-HOST-LABEL> --monitor <MONITOR-IP> --token <TOKEN> --apply

`--token` is your **enrolment token**, on the app's **Commands** tab. It is
what puts the new hosts on that tab so you can run actions on them from the
app. Leave it out and everything else still works — metrics, alerts, the lot —
the hosts just will not appear there. You can add them later by re-running
this with the token.

That is the whole of it. The script installs Grafana Alloy, the metric
producer, the xxOps agent, a logrotate rule and a journal cap — plus the gossip
watchdog if the host is a gateway — and then waits to confirm the monitor is
actually receiving that host before it finishes.

`--monitor` is the same address your existing hosts already use.

`--label` is what this machine is called in the app. **Give the new hosts
labels that are unique across everything you already run.** If the gateway's
label starts with its node's label — `newone` and `newone_gt`, say — the app
pairs them for you; if not, you can pair them by hand in the app afterwards.

It announces each step as it goes, so if something fails you know where. Safe
to re-run, which is also how you change a label later.

If it guesses the role wrong, add `--role node` or `--role gateway`.

**Optionally**, the link-speed test. On the new gateway:

    curl -sL https://raw.githubusercontent.com/Bulldog-Master/xxOps/main/fixes/xxops-linkspeed-install.sh \
      | sudo bash -s -- --listener-only

and on the new node, pointing at its own gateway:

    curl -sL https://raw.githubusercontent.com/Bulldog-Master/xxOps/main/fixes/xxops-linkspeed-install.sh \
      | sudo bash -s -- --gateway <ITS-GATEWAY-ADDRESS>

**3. Tell a contact about it.** This does not happen by itself — an existing
contact is **not** automatically alerted about a validator you add later.

Under **Settings → Contacts**, open whoever should hear about it, tick the new
validator alongside their existing ones, and **Save settings**. They do not
need to pair again; the Telegram chat they already have stays linked.

A validator that no contact covers falls through to the fallback under
"anything unassigned". If you have not set one, **nobody is told about it at
all** — the alerts are still evaluated and still visible in the app, but
nothing is delivered.

## Confirming it worked

The app discovers hosts from Prometheus, so there is nothing to tell it. Within
a minute or two of Alloy starting on the new hosts, your validator count goes
up by one and both machines appear under Hosts.

If they do not, the check at the end of Part 2 step 2 is the one that matters —
it shows whether metrics are arriving at all, which is nearly always the answer.
