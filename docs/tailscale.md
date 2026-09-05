# Tailscale, if you need it

You do not have to use Tailscale. This document exists because the install
guide names it and a reader who has not met it before deserves more than a
brand name.

## The problem it solves

Every host pushes its metrics to the monitor, so every host needs a route to
it. That is easy when your machines have stable public addresses and awkward
when they do not.

Nodes usually sit at home, behind a router, on an address the ISP changes
whenever it likes. There is nothing fixed to point at, and forwarding a port
means opening your home network to the internet.

A mesh VPN sidesteps this. Every machine you enrol gets a private address that
does not change, reachable only by your other machines, regardless of what any
ISP does. Nothing is exposed publicly.

Tailscale is one such VPN. It is free at this scale, runs on everything you
are likely to have, and needs no router configuration.

## The honest caveat

Tailscale is a third party. The traffic between your machines is encrypted end
to end and does not pass through them, but their coordination service does
handle the key exchange and knows which of your machines exist.

**Using it means creating an account with them.** There is no anonymous
option: you sign in with Google, Microsoft, GitHub, Apple or a passkey, and
your network is tied to that identity. So it is not only that they know your
machines exist — they know them as belonging to you.

If that is a problem, it is a good reason to take one of the alternatives
below rather than a detail to discover later. Decide before you install
anything.

If that matters to you, **you do not need it.** Give your hosts stable
addresses and a firewall rule permitting each one to reach the monitor's
Prometheus port. Everything in xxOps works the same way — the guide asks for
addresses, and it does not care where they came from. Headscale, a
self-hosted coordination server, is a third option.

## Setting it up

Their documentation is better than anything reproduced here, and it stays
current: **https://tailscale.com/kb/1017/install**

The short version, on every machine — each node, each gateway, and the
monitor:

    curl -fsSL https://tailscale.com/install.sh | sh
    sudo tailscale up

`tailscale up` prints a URL. **Open it in a web browser** — on that machine if
it has one, or on any other device by copying the URL across. It is a web page,
not something to paste back into the terminal.

Sign in there and that machine joins your network. The first machine creates
the account; every one after that joins the same network.

**Use the same account for every machine.** They can only reach each other if
they are on the same network, and a second account gives you a second network
with nothing in it.

## The device you look at it from

The machines above are the ones that **send** metrics. There is one more to
think about: whatever you open the app on.

The app is a web page served by the monitor. Nothing to install — but your
browser has to be able to reach the monitor, and if the monitor is only on the
mesh VPN then so does that device.

So a Windows desktop, a Mac, a phone or a tablet needs the Tailscale client
too, signed into the same account. This is the only reason a machine that is
not a node, gateway or monitor would need it.

Their clients cover Windows, macOS, iOS, Android and Linux, from the same
install page linked above. Install whichever you plan to use — it is free at
this scale and there is no limit on how many of your own devices join.

You can add these at any time, and you can view the app from as many devices
as you like. Each signs into xxOps separately.

If instead your monitor is reachable at a public address, you do not need any
of this to view it — but read the security note in the install guide first,
because reachable by you means reachable by everyone.

## Finding a machine's address

The install guide asks for addresses in several places. On any machine:

    tailscale ip -4

That prints its address, which will start with `100.`. That is what you give
the guide when it asks for `<MONITOR-IP>` or a gateway address.

To see every machine at once, from any of them:

    tailscale status

## A note on names

Tailscale knows your machines by their system hostnames, which are often not
what you call them. A gateway you think of as a name may appear as whatever
its provider named the VPS.

That does not break anything — xxOps uses the addresses, not these names — but
do not expect the two lists to match.

## Certificates

If you want the app served over HTTPS, and your hosts are on Tailscale, it can
issue a real certificate for the machine:

    sudo tailscale cert <machine-name>.<your-network>.ts.net

`tailscale status` shows the full name to use. This is what the install guide
means when it mentions `tailscale cert`. It is optional — the app works over
plain HTTP on a private network — but browsers reserve some features,
including passkeys, for HTTPS.
