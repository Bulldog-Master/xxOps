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
- **The agent is unprivileged**, with one capability that lets it read the
  files it needs and nothing else. It cannot write, cannot change ownership,
  and cannot execute as another user.
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
- **The login is not rate limited.** Behind a private network that's a
  reasonable trade. On anything reachable from the internet it is not — put a
  proxy in front of it.
- **Two-factor tolerates ±90 seconds of clock drift**, which is wider than the
  usual ±30. That was a workaround for authenticator apps whose own time
  correction had skewed, and it means seven codes are valid at once rather
  than three. Acceptable behind a password on a private network; worth knowing.
- **A host running the agent trusts whichever monitor's public key is in
  `/etc/xxops/allowed_signers`.** Re-running an installer against a different
  monitor replaces it.

## Scope

In scope: anything that lets one user see or change another's data, anything
that turns a signed request into arbitrary code execution, anything that
exposes credentials, and any privilege escalation from the agent's account.

Out of scope: the limits listed above, which are documented rather than
accidental; and anything requiring an attacker to already hold the signing key
or root on the monitor.
