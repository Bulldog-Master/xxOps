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

**2. Run Part 2 of the install guide on each new host.** All of it, in order,
exactly as you did the first time: Alloy, the producer, the agent, logrotate,
the journal cap. The node gets the node commands, the gateway the gateway ones
— every step says which.

Give the new hosts labels that are unique across everything you already run. If
the gateway's label starts with its node's label, the app pairs them for you; if
not, you can pair them by hand in the app afterwards.

## Confirming it worked

The app discovers hosts from Prometheus, so there is nothing to tell it. Within
a minute or two of Alloy starting on the new hosts, your validator count goes
up by one and both machines appear under Hosts.

If they do not, the check at the end of Part 2 step 2 is the one that matters —
it shows whether metrics are arriving at all, which is nearly always the answer.

## Adding contacts for it

An existing contact is not automatically alerted about a new validator. Under
Settings → Contacts, tick the new validator for whoever should hear about it,
and save. A validator no contact covers falls through to the fallback under
"anything unassigned", and if you have not set one, nobody is told.
