# Security

## Reporting something

Please use GitHub's private vulnerability reporting: the **Security** tab on
this repository, then **Report a vulnerability**. That opens a private thread
visible only to the maintainer.

Please don't open a public issue for anything that could be used against a
running installation before it's fixed.

There's no bounty. There is a genuine thank-you and credit in the fix, if you
want it.

## What this software is

Self-hosted monitoring for xx Network validators. Every installation is run by
the person who owns the machines. There's no service, no shared infrastructure,
and no operator with access to anyone else's data — so a vulnerability here
affects each installation separately.

## The security model

- **The agent runs actions from a fixed catalogue, never a command string.**
  Nothing arriving over the network can become a shell command. Privileged
  actions run through sudoers entries listing exact command lines, validated
  with `visudo` before installation.
- **Requests are signed.** The private key lives on the monitor alone, so a
  compromised host cannot forge instructions to another.
- **The agent holds no capabilities.** It runs as its own account with no
  write access and no shell, and everything it can change goes through exact
  sudoers lines validated with `visudo` before installation.

  It reaches the validator's own directories through ACLs the installer
  grants: traverse on `/opt/xxnetwork`, read on `cred/`, `log/` and
  `config/`. Nothing else. The certificates in `cred/` are world-readable
  already; **the private key beside them is `0600` and stays unreadable to
  the agent**, which was verified on a live host before the capability was
  removed.

  It previously held CAP_DAC_READ_SEARCH, which bypasses read and search
  checks across the whole filesystem. If you are running a version from
  before September 2026, assume a compromised agent on that host could read
  anything root could.

  One consequence worth knowing: `disk-usage` reports `/opt/xxnetwork`
  accurately but gives a floor rather than a total for `/var/lib` and
  `/var/log`, because the agent can only see what it can traverse. That is
  the trade, and it is deliberate.
- **Responses are filtered by who is asking**, on the server rather than by
  hiding controls in the interface.
- **The backend needs no sudo at all.**

## Known limits

Stated plainly, because you should be able to judge the risk rather than
discover it:

- **Whoever holds the signing key can restart services on every host that
  trusts it.** It's the most sensitive file on the monitor. Keep it at mode
  0600 and out of backups that ship to machines the key commands.
- **Prometheus has no authentication of its own.** The app protects itself,
  but Prometheus does not. Put the monitor somewhere only you can reach — a
  private network, or behind a reverse proxy with authentication. Do not
  expose its port directly.
- **The login is rate limited**, since August: five failures per
  username per fifteen minutes, then refused for fifteen. Counted against
  the username as submitted, existing or not, so it cannot be used to
  discover which accounts are real. The counters are in memory on
  purpose - restarting the app clears them, which is the way back in if
  you lock yourself out.
- **Two-factor tolerates ±60 seconds of clock drift**, so five codes
  are valid at once rather than the usual three. That is wider than
  the default and narrower than it was: an operator's authenticator
  ran about a minute ahead across two incidents, mostly because the
  same secret had been enrolled more than once on different devices.
  With that fixed the residual drift is about one step, and this
  absorbs it. Before widening it again, check the server's own clock
  (`timedatectl`) and that the secret is enrolled exactly once.
- **A host running the agent trusts whichever monitor's public key is in
  `/etc/xxops/allowed_signers`.** Re-running an installer against a different
  monitor replaces it.
- **Enrolment prefers HTTPS, and will accept a certificate it cannot
  verify.** The installers try strict HTTPS first, fall back to HTTPS with
  the certificate unverified, and only then to plain HTTP — saying which at
  each step. The middle case is the common one: a `tailscale cert` is issued
  for the machine's `.ts.net` name while installers connect by address, so
  verification fails even though the certificate is genuine. In that state
  the enrolment token cannot be read by someone watching the traffic, but
  could be collected by someone who successfully impersonates the monitor.
  On a private network that is a reasonable trade. On anything else, reach
  the monitor by its certificate's name so verification succeeds.

## Scope

In scope: anything that lets one user see or change another's data, anything
that turns a signed request into arbitrary code execution, anything that
exposes credentials, and any privilege escalation from the agent's account.

Out of scope: the limits listed above, which are documented rather than
accidental; and anything requiring an attacker to already hold the signing key
or root on the monitor.
