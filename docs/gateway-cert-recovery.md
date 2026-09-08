# Recovering an expired gateway certificate

**Short version:** if your gateway cannot peer and its certificate expired in
2024, the replacement may already be sitting on the paired node. Check there
before assuming it needs reissuing. This can go unnoticed for a very long time.

---

## What it looks like

- Zero `Gossip received` lines in the entire gateway log
- `Local round data` still appearing normally, so the node link is fine
- Peers answering `No host set up with <id>, refusing contact`
- `WaitAuthorization: context deadline exceeded`
- Restarts change nothing — 14 watchdog restarts and a full two-host recovery
  all failed

In xxOps this shows as `GatewayIsolatedPersistent`, and now also
`CertificateExpired`.

## What it is

An expired identity certificate fails authentication in **both** directions.
The gateway cannot reach peers and peers cannot reach it. No restart can fix
that, which is why the watchdog exhausts its attempts and gives up.

The October 2023 rotation moved gateways from 2-year certificates to 10-year
ones expiring 2033. A host restored from a pre-rotation image quietly goes back
to the old certificate and keeps running it until it expires — then goes deaf,
while every other signal looks healthy.

## Check the gateway

```
openssl x509 -in /opt/xxnetwork/cred/gateway-cert.crt -noout -dates
```

`notAfter` in 2033 is correct. 2024 means this is your problem.

Another tell: properly rotated gateways carry `new.gateway-cert.crt` and
`old.gateway-cert.crt` beside the active one. A host that missed the rotation
has neither.

## Then check the node — this is the part that matters

**Nodes carry a `gateway-cert.crt` too.** The rotation delivered a gateway
certificate to both machines of the validator pair. If the gateway's copy was
reverted by a restore, the node's copy may still be the rotated one.

On the node:

```
openssl x509 -in /opt/xxnetwork/cred/gateway-cert.crt -noout -dates
```

If that says 2033, you probably already have your certificate.

## Verify before copying anything

A certificate is useless without its matching private key. The private key
(`gateway-key.key`) lives on the gateway and is not part of the rotation, so
the question is whether the node's certificate matches the key you still have.

On the node:
```
openssl x509 -in /opt/xxnetwork/cred/gateway-cert.crt -noout -pubkey | openssl pkey -pubin -outform DER | sha256sum
```

On the gateway:
```
openssl pkey -in /opt/xxnetwork/cred/gateway-key.key -pubout -outform DER | sha256sum
```

**If those hashes differ, stop.** The rotation issued a new keypair and the
certificate alone will not help — you need a reissue.

If they match, the rotation extended validity on your existing keypair and the
certificate will work. Worth confirming the rest lines up too:

```
openssl x509 -in /opt/xxnetwork/cred/gateway-cert.crt -noout -subject -issuer -serial
openssl x509 -in /opt/xxnetwork/cred/gateway-cert.crt -noout -text | grep -A2 "Subject Alternative Name"
```

Subject, issuer, serial and SAN should be identical on both machines, with only
the validity window differing. An identical serial across a renewal is normal
here — it is the same certificate with extended dates.

## Install it

Move the file between machines however you normally would. If you use the
temporary-http-server approach, **copy the certificate to a scratch directory
first** — do not serve `/opt/xxnetwork/cred`, it contains your private keys.

Verify the file arrived intact:

```
sha256sum gateway-cert.crt
```

Then, on the gateway:

```
sudo systemctl stop xxnetwork-gateway
cp /opt/xxnetwork/cred/gateway-cert.crt /opt/xxnetwork/cred/old.gateway-cert.crt
cp /path/to/new/gateway-cert.crt /opt/xxnetwork/cred/gateway-cert.crt
openssl x509 -in /opt/xxnetwork/cred/gateway-cert.crt -noout -dates
sudo systemctl start xxnetwork-gateway
```

Three things worth getting right:

- **Stop the service first.** The running process holds the certificate
  loaded; swapping it underneath is how you get a half-working state that
  wastes an afternoon.
- **Name the backup `old.gateway-cert.crt`.** That is the convention on
  properly rotated gateways, and it is the prefix the xxOps producer skips —
  any other `.crt` name and `CertificateExpired` will fire on your backup
  forever.
- **Check ownership and mode survived the copy.** While you are in there,
  `gateway-key.key` should be `0600`.

## Confirm it worked

```
grep -c "Gossip received" /opt/xxnetwork/log/gateway.log
```

Take that count *before* the change as your baseline. Gossip should appear
within about two minutes of starting the service.

**Do not treat an alert clearing as proof.** An alert can go quiet while gossip
is still at zero, and the problem gets recorded as fixed when it is not. The
log line is the evidence; the alert resolving is corroboration.

Afterwards, re-enable the watchdog timer if you disabled it, and clear any
mute you set.

## The general lesson

The certificate was never lost. It sat on the paired machine the whole time,
while the problem was recorded as needing a reissue from xx. **When a
credential looks unrecoverable, check the paired machine before asking for a
replacement.**
