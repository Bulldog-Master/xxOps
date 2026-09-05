# The wrapper stops accepting signed commands (pyOpenSSL)

**Short version:** if your node or gateway was set up or refreshed after
December 2024, its wrapper may be silently unable to verify signed management
commands. Nothing about it is visible from the outside. Check takes ten
seconds.

---

## What it looks like

In `gateway-wrapper.log` or `cmix-wrapper.log`, roughly every ten seconds:

```
[ERROR] Unable to verify command: module 'OpenSSL.crypto' has no attribute 'verify'
[ERROR] Failed to verify signature for /tmp/xxnetwork/gateway/command.jsonl!
```

And nothing else. The node or gateway binary keeps running normally: rounds
advance, gossip flows, peers connect, the dashboard looks fine and you keep
earning. No monitoring will tell you, because every signal people normally
watch is about the binary, and the binary is healthy.

## What is actually broken

The wrapper is a separate Python process from the node or gateway binary. It
polls for signed management commands and verifies their signature before
acting on them. That verification uses `OpenSSL.crypto.verify()` from
pyOpenSSL.

pyOpenSSL **24.3.0 removed that function.** Any host whose wrapper
dependencies were installed or refreshed after that release picks up a version
where the call no longer exists, and signature verification fails every time.

The wrapper's dependencies are installed with `pip install --user` into the
service user's `~/.local`, and **nothing in that bundle is version-pinned**. So
which version a host ends up with depends entirely on when it was last
touched. Across a single fleet the installed versions ranged from
21.0.0 (fine, from apt) and 24.2.1 (fine) up to 25.0.0, 25.3.0 and 26.3.0 (all
broken) — with no pattern by role, by OS, or by anything else you could
predict from the outside.

## Why it matters

Signed commands are how management instructions reach a host, including pushed
binary updates. An affected host quietly ignores them while looking perfectly
healthy. That is the same failure shape as a host silently missing a
certificate rotation: nothing breaks today, and you find out much later.

## How to check

As the **service user** on the host — not root, because the dependencies live
in that user's home:

```
python3 -c "import OpenSSL; print(OpenSSL.__version__)"
python3 -c "import OpenSSL.crypto as c; print(hasattr(c,'verify'))"
```

`True` means you are fine. `False` means the wrapper cannot verify commands.
A `DeprecationWarning` about `verify()` is harmless — it means the function is
still there.

## How to fix

As the service user:

```
pip3 install --user 'pyopenssl<24.3'
```

That installs 24.2.1 and pulls `cryptography` back below 44, which is expected
and correct.

On Ubuntu 24.04, pip refuses with `externally-managed-environment` (PEP 668).
Add `--break-system-packages`:

```
pip3 install --user --break-system-packages 'pyopenssl<24.3'
```

Despite the alarming flag name, `--user` keeps the write inside `~/.local` and
never touches system packages.

Then **restart the service** — this is not optional:

```
sudo systemctl restart xxnetwork-cmix      # on a node
sudo systemctl restart xxnetwork-gateway   # on a gateway
```

The running wrapper holds the old module in memory. Until you restart, a fresh
version check will report healthy while the live process is still broken.

## How xxOps detects it

Two metrics from the textfile producer:

| Metric | Meaning |
| --- | --- |
| `xx_wrapper_cmd_verify_ok` | `1` working, `0` broken, `-1` no wrapper running |
| `xx_pyopenssl_version_info{version="..."}` | the installed version, as a label |

The check runs in the **wrapper's** environment, not the producer's — it finds
the wrapper process, takes its user, and points `PYTHONPATH` at that user's
site-packages. Testing the producer's own environment would report healthy
while the wrapper was broken, which is the trap that makes this hard to see in
the first place.

It tests `hasattr(OpenSSL.crypto, "verify")` rather than comparing version
numbers, so it stays correct whatever the library renames next.

Alert: `WrapperCommandVerifyBroken`, on `== 0` for 15 minutes, amber. Amber
because it costs no rounds and no earnings — there is nothing to fix at 3am.
It surfaces in Needs attention and the daily digest.

The version is also tracked in the Changes tab, using the same label-change
pattern as chain builds. The alert catches the broken state; the Changes entry
catches the *move*, including a move to some future version that still has
`verify` but breaks something else.

## What this does not fix

Nothing pins the wrapper's dependency bundle. A host that re-runs the install
will drift forward again. The fix above buys you detection in fifteen minutes
instead of eight months — it is not prevention.

Real prevention is a constraints or requirements-lock file shipped alongside
the wrapper's dependencies, which is an xx-side change rather than something
each operator should be patching by hand.
