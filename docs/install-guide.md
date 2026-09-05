# Installing xxOps

Everything here uses placeholders. `<MONITOR-IP>` is the address your validator
hosts can reach the monitor on.

---

## What you are building

**The monitor.** One machine running Prometheus, Alertmanager and the xxOps
app. It receives metrics, evaluates rules, sends notifications and serves the
interface.

**Every validator host.** Each node and gateway runs Grafana Alloy, a small
metric producer, and the xxOps agent.

A validator is a node and a gateway on **two separate machines**, so even one
validator means two hosts and one network link between them. There is no
single-machine install.

---

## Two decisions first

### Where the monitor runs

> [!WARNING]
> **The monitor goes on ONE machine. Only one.**
>
> Everything in Part 1 is done on that single machine and nowhere else. If you
> install it twice you get two monitors that each see part of your fleet, two
> sets of alerts, and two apps disagreeing with each other.
>
> Pick which machine now, before Part 1, and do every step of Part 1 there.

You probably do not need a new machine.

- **1-4 validators:** put it on a gateway you already pay for. Gateways are
  public VPSes with static addresses and lighter workloads than nodes. Prefer a
  gateway over a node.
- **5-10 validators:** still fine, but if that machine dies you go blind to
  everything at once.
- **10+ validators:** give it its own box. Losing visibility then costs more
  than the machine.

### How your hosts reach it

Metrics are **pushed** from each host, so every host needs a route to the
monitor. Tailscale or another mesh VPN is the simplest answer if your nodes sit
behind home routers on dynamic addresses. A firewall rule works if your hosts
have stable addresses.

If you have not used a mesh VPN before, or you want the reasons for and
against one, see [tailscale.md](tailscale.md). It also covers finding the
addresses this guide keeps asking you for.

Either way, Prometheus on the monitor cannot listen only on loopback.

### Do the network before anything else

**If you are using a mesh VPN, set it up now — on the monitor and on every
node and gateway — before you start Part 1.**

Everything from here on asks you for `<MONITOR-IP>`, and on a mesh VPN that
address does not exist until the VPN is running. Write it into the Prometheus
unit before then and the service has nothing to bind to.

It matters at boot too. Prometheus cannot bind to an address that has not come
up yet, so its unit needs the VPN's service named in `Wants=` and `After=` —
otherwise it starts first, fails, and the monitor comes back from a reboot
blind.

If you are using firewall rules and static addresses instead, there is nothing
to install first. Have the addresses to hand before you start.

---

## Sizing

Measured on a production fleet.

| Validators | Prometheus memory |
| --- | --- |
| 1 | ~160 MB |
| 4 | ~200 MB |
| 10 | ~270 MB |

About 2,500 series and 5 MB of memory per host. Memory is never the limit.

Disk is: roughly **13 MB per host per day**, so about 400 MB per host per month
at 30-day retention. Retention is a dial — halve it and you halve the disk.

---

## Part 1: the monitor

> [!NOTE]
> **Everything in this part runs on the monitor machine only.** Not on your
> nodes, not on your other gateways. One machine, start to finish.


### 1. Install Prometheus and Alertmanager

First, the things this assumes you have. On a fresh VPS you will not:

    sudo apt update
    sudo apt install -y git curl tar

**Do the rest of this step in one terminal session.** The version numbers
below are shell variables, and they are gone if you open a new window or come
back tomorrow. If a download turns out to be a few bytes instead of a hundred
megabytes, that is what happened -- set them again and retry.


Use the upstream binaries. Both are a single Go binary with no dependencies,
and distribution packages are usually too old for the remote-write receiver
this needs. Built against Prometheus 3.x and Alertmanager 0.28.

Check the current version numbers on the two releases pages first, then set
them once:

    PROM_VER=3.1.0
    ALERT_VER=0.28.0

**Prometheus:**

    cd /tmp
    curl -LO https://github.com/prometheus/prometheus/releases/download/v${PROM_VER}/prometheus-${PROM_VER}.linux-amd64.tar.gz
    tar xzf prometheus-${PROM_VER}.linux-amd64.tar.gz
    sudo install -m 755 prometheus-${PROM_VER}.linux-amd64/prometheus /usr/local/bin/
    sudo install -m 755 prometheus-${PROM_VER}.linux-amd64/promtool /usr/local/bin/

**Alertmanager:**

    curl -LO https://github.com/prometheus/alertmanager/releases/download/v${ALERT_VER}/alertmanager-${ALERT_VER}.linux-amd64.tar.gz
    tar xzf alertmanager-${ALERT_VER}.linux-amd64.tar.gz
    sudo install -m 755 alertmanager-${ALERT_VER}.linux-amd64/alertmanager /usr/local/bin/
    sudo install -m 755 alertmanager-${ALERT_VER}.linux-amd64/amtool /usr/local/bin/

**Accounts and directories.** Neither should run as root:

    sudo useradd --system --no-create-home --shell /usr/sbin/nologin prometheus
    sudo useradd --system --no-create-home --shell /usr/sbin/nologin alertmanager
    sudo mkdir -p /etc/prometheus /var/lib/prometheus /etc/alertmanager /var/lib/alertmanager
    sudo chown prometheus:prometheus /var/lib/prometheus
    sudo chown alertmanager:alertmanager /var/lib/alertmanager

**The Prometheus unit.** Set your address once, then paste the block --
the shell fills it in for you, so there is nothing left to replace:

    MON=<MONITOR-IP>

    sudo tee /etc/systemd/system/prometheus.service >/dev/null <<EOF
    [Unit]
    Description=Prometheus
    Wants=network-online.target
    After=network-online.target

    [Service]
    User=prometheus
    Group=prometheus
    Type=simple
    ExecStart=/usr/local/bin/prometheus \
      --config.file=/etc/prometheus/prometheus.yml \
      --storage.tsdb.path=/var/lib/prometheus \
      --storage.tsdb.retention.time=30d \
      --web.listen-address=${MON}:9090 \
      --web.enable-remote-write-receiver \
      --web.enable-lifecycle
    Restart=on-failure
    RestartSec=5

    [Install]
    WantedBy=multi-user.target
    EOF

**Check it went in:**

    grep listen-address /etc/systemd/system/prometheus.service

That must show your address, not `${MON}` and not empty. Empty means the
variable was not set when you pasted -- set it and paste the block again.

Those last two flags matter. `--web.enable-remote-write-receiver` is what lets
hosts push to it; without it nothing arrives. `--web.enable-lifecycle` lets a
config change take effect without a restart.

If your hosts reach the monitor over a mesh VPN, add that VPN's unit to
`Wants=` and `After=` — Prometheus cannot bind to an address that does not
exist yet, and will fail at boot if it starts first.

**The Alertmanager unit:**

    sudo tee /etc/systemd/system/alertmanager.service >/dev/null <<'EOF'
    [Unit]
    Description=Alertmanager
    Wants=network-online.target
    After=network-online.target

    [Service]
    User=alertmanager
    Group=alertmanager
    Type=simple
    ExecStart=/usr/local/bin/alertmanager \
      --config.file=/etc/alertmanager/alertmanager.yml \
      --storage.path=/var/lib/alertmanager \
      --web.listen-address=127.0.0.1:9093 --cluster.listen-address=
    Restart=on-failure
    RestartSec=5

    [Install]
    WantedBy=multi-user.target
    EOF

`--web.listen-address=127.0.0.1:9093` keeps it on loopback — nothing outside
this machine talks to Alertmanager directly. The empty
`--cluster.listen-address=` switches off clustering, which is on by default
and otherwise opens port 9094 **on every interface, including your public
one**. You do not need clustering for one Alertmanager.

**Do not start them yet.** Neither has a configuration file — the installer in
the next step writes both. Starting now just gives you two failed units.

    sudo systemctl daemon-reload

### 2. Run the installer

    git clone https://github.com/Bulldog-Master/xxOps.git
    cd xxOps
    sudo ./install-monitor.sh --bind <MONITOR-IP>

`--host` is **optional** and left off above on purpose. It names the DNS name
you would open the app on, and it only matters if you want HTTPS. Without it
the app runs over plain HTTP, which is fine on a private network. Add it later
alongside a certificate if you want one.

**That is a dry run.** It prints exactly what it would do and changes nothing.
Read it, then:

    sudo ./install-monitor.sh --bind <MONITOR-IP> --apply

It writes `/etc/xxops/xxops.conf`, generates the agent signing key, copies the
app, backend, units, Prometheus config, rules and backup script into place,
substitutes your address into `prometheus.yml`, validates everything with
`promtool` and `amtool` before enabling anything, and then checks the app
actually answers rather than merely starting.

It backs up whatever it replaces, never regenerates an existing signing key,
and never touches an existing `alertmanager.yml`.

**Now start Prometheus and Alertmanager**, in that order — both have their
configuration at this point:

    sudo systemctl enable --now prometheus alertmanager
    systemctl is-active prometheus alertmanager

Both must say `active`. If either does not, read `journalctl -u prometheus -n
30` — a bad address in the unit file is the usual cause, and it says so
plainly.

Add `--backup-dests "user@host:/path user@host2:/path"` to enable the nightly
config backup. Choose two machines that cannot fail together — one node and one
VPS, so a single power cut cannot take both copies.

### 3. The signing key

`/etc/xxops/cmd_key` can restart services on every host you manage. It stays on
the monitor, mode 0600, and is deliberately excluded from the backup — the
backup ships to machines that key commands.

---

## Part 2: every node and gateway

> [!NOTE]
> **Everything in this part runs on EVERY validator host** — each node and
> each gateway, one at a time. Two validators means four hosts, so you do this
> part four times.
>
> That includes the machine running the monitor, if it is also a node or a
> gateway. It needs these too — being the monitor does not exempt it from
> being monitored.


Do these **in order**. Alloy will not pick up a configuration written before it
is installed, and it fails quietly.

### 1. Network first

**Run on: every host.**

Confirm the host can reach the monitor:

    curl -sf http://<MONITOR-IP>:9090/-/healthy

### 2. Install Grafana Alloy, then configure it

**Run on: every host.** The configuration is the same on a node and a
gateway; only the label differs.

Alloy collects host metrics from its own exporter, chain metrics from
`127.0.0.1:9615` (which every node and gateway exposes natively), and a
textfile directory the producer writes to — then remote-writes outbound.

**Install it** from Grafana's repository. Check their current instructions if
this fails; they change the key handling from time to time.

    sudo apt-get install -y gpg
    sudo mkdir -p /etc/apt/keyrings
    wget -q -O - https://apt.grafana.com/gpg.key \
      | gpg --dearmor | sudo tee /etc/apt/keyrings/grafana.gpg >/dev/null
    echo "deb [signed-by=/etc/apt/keyrings/grafana.gpg] https://apt.grafana.com stable main" \
      | sudo tee /etc/apt/sources.list.d/grafana.list
    sudo apt-get update && sudo apt-get install -y alloy

**The textfile directory** the producer writes into. Alloy reads it, so it has
to exist before Alloy starts or the scrape fails:

    sudo mkdir -p /var/lib/alloy/textfile
    sudo chown alloy:alloy /var/lib/alloy/textfile

**The configuration.** The same file works on a node and on a gateway. Set
two variables first and the block fills itself in:

- `LABEL` is what you want this machine called in the app — short, lowercase,
  and **unique across your hosts**. If a gateway's label starts with its
  node's label, xxOps pairs them automatically; if not, you can pair them by
  hand in the app later.
- `MON` is your monitor's address.

<!-- -->

    LABEL=<THIS-HOST-LABEL>
    MON=<MONITOR-IP>

    sudo tee /etc/alloy/config.alloy >/dev/null <<EOF
    logging {
      level  = "info"
      format = "logfmt"
    }

    prometheus.exporter.self "alloy" {}

    prometheus.scrape "alloy" {
      targets         = prometheus.exporter.self.alloy.targets
      forward_to      = [prometheus.relabel.add_host_label.receiver]
      job_name        = "alloy"
      scrape_interval = "60s"
    }

    prometheus.exporter.unix "system" {
      include_exporter_metrics = true

      filesystem {
        fs_types_exclude = "^(autofs|binfmt_misc|bpf|cgroup2?|configfs|debugfs|devpts|devtmpfs|fusectl|hugetlbfs|iso9660|mqueue|nsfs|overlay|proc|procfs|pstore|rpc_pipefs|securityfs|selinuxfs|squashfs|sysfs|tracefs|tmpfs)$"
        mount_points_exclude = "^/(dev|proc|sys|run|var/lib/docker/.+)($|/)"
        mount_timeout = "5s"
      }
    }

    prometheus.scrape "system" {
      targets         = prometheus.exporter.unix.system.targets
      forward_to      = [prometheus.relabel.add_host_label.receiver]
      job_name        = "node"
      scrape_interval = "30s"
    }

    prometheus.scrape "xx_chain" {
      targets         = [{ __address__ = "127.0.0.1:9615" }]
      forward_to      = [prometheus.relabel.add_host_label.receiver]
      job_name        = "xx_chain"
      scrape_interval = "30s"
    }

    prometheus.relabel "add_host_label" {
      forward_to = [prometheus.remote_write.xxops.receiver]

      rule {
        target_label = "instance"
        replacement  = "${LABEL}"
      }
      rule {
        target_label = "pilot"
        replacement  = "xxops"
      }
    }

    prometheus.remote_write "xxops" {
      endpoint {
        url = "http://${MON}:9090/api/v1/write"

        queue_config {
          capacity             = 10000
          max_samples_per_send = 2000
          batch_send_deadline  = "5s"
        }
      }

      wal {
        truncate_frequency = "2h"
      }
    }

    prometheus.exporter.unix "xx_textfile" {
      set_collectors = ["textfile"]

      textfile {
        directory = "/var/lib/alloy/textfile"
      }
    }

    prometheus.scrape "xx_textfile" {
      targets         = prometheus.exporter.unix.xx_textfile.targets
      forward_to      = [prometheus.relabel.add_host_label.receiver]
      job_name        = "xx"
      scrape_interval = "60s"
    }
    EOF

Then start it:

    sudo systemctl enable --now alloy
    systemctl is-active alloy

**Check both values went in** before you go further. This is the single most
common way to get stuck, and it fails quietly — Alloy runs, and nothing
arrives:

    sudo grep -n 'replacement  = \|url = ' /etc/alloy/config.alloy

You want your label and your monitor's address. If you see `${LABEL}` or
`<MONITOR-IP>`, the variables were not set when you pasted — set them and
paste the block again.

Two things that bite, both visible in the file above:

- Static targets need a **bare map key** — `targets = [{ __address__ =
  "127.0.0.1:9615" }]`. Quote the key and the target is silently skipped, with
  no error anywhere.
- **Set the instance label explicitly**, as the `add_host_label` block does.
  Deriving it from the username breaks wherever two machines share one, and
  you end up with two hosts fighting over one name in the app.

**Check it before moving on.** On the monitor:

    curl -s 'http://<MONITOR-IP>:9090/api/v1/query?query=up' | grep -o '<THIS-HOST-LABEL>'

If that prints your host label, metrics are arriving. If it prints nothing,
Alloy is running but not reaching the monitor — `journalctl -u alloy -n 30` on
the host will say why, and a blocked port or a wrong address are the usual two.

### 3. Install the producer

**Run on: every host** — plus a watchdog on gateways only, at the end of
this step.

This is the script that reads the things no exporter knows about — cMix round
numbers, certificate expiry, log sizes, storage counters — and writes them
into the textfile directory Alloy scrapes.

**On every node and gateway:**

    curl -sL https://raw.githubusercontent.com/Bulldog-Master/xxOps/main/producer/xxops-textfile.sh -o /tmp/xxops-textfile.sh
    bash -n /tmp/xxops-textfile.sh && sudo install -m 755 /tmp/xxops-textfile.sh /usr/local/bin/

`bash -n` checks it parsed before you install it. A truncated download that
still runs is worse than one that fails.

    sudo tee /etc/systemd/system/xxops-textfile.service >/dev/null <<'EOF'
    [Unit]
    Description=xxOps textfile metric producer
    [Service]
    Type=oneshot
    ExecStart=/usr/local/bin/xxops-textfile.sh
    EOF

    sudo tee /etc/systemd/system/xxops-textfile.timer >/dev/null <<'EOF'
    [Unit]
    Description=Run the xxOps textfile producer every 60s
    [Timer]
    OnBootSec=30
    OnUnitActiveSec=60
    [Install]
    WantedBy=timers.target
    EOF

    sudo systemctl daemon-reload
    sudo systemctl enable --now xxops-textfile.timer

**Enable the timer, not the service.** A host with the script but no timer
produces metrics that freeze at whatever they were rather than disappearing —
which looks healthy from the outside and is the worst way for monitoring to
fail.

The same script runs on either role and emits whatever applies. Do not branch
it by role.

**On gateways only**, add the gossip watchdog. It restarts a gateway that has
gone deaf to its peers while still looking alive:

    curl -sL https://raw.githubusercontent.com/Bulldog-Master/xxOps/main/producer/xxops-gateway-watchdog.sh -o /tmp/xxops-gateway-watchdog.sh
    bash -n /tmp/xxops-gateway-watchdog.sh && sudo install -m 755 /tmp/xxops-gateway-watchdog.sh /usr/local/bin/

    sudo tee /etc/systemd/system/xxops-gateway-watchdog.service >/dev/null <<'EOF'
    [Unit]
    Description=xxOps gateway gossip watchdog
    [Service]
    Type=oneshot
    ExecStart=/usr/local/bin/xxops-gateway-watchdog.sh
    EOF

    sudo tee /etc/systemd/system/xxops-gateway-watchdog.timer >/dev/null <<'EOF'
    [Unit]
    Description=Run the xxOps gateway watchdog every 5 minutes
    [Timer]
    OnBootSec=10min
    OnUnitActiveSec=5min
    [Install]
    WantedBy=timers.target
    EOF

    sudo systemctl daemon-reload
    sudo systemctl enable --now xxops-gateway-watchdog.timer

**Check it before moving on.** Run the producer once by hand and look at what
it wrote:

    sudo /usr/local/bin/xxops-textfile.sh
    sudo head -5 /var/lib/alloy/textfile/xx.prom

You should see metric lines. If the file is missing, the producer could not
write to the directory — check you created it in the previous step.

### 4. Install the agent

**Run on: every host.**

    curl -sL https://raw.githubusercontent.com/Bulldog-Master/xxOps/main/agent/install.sh \
      | sudo bash -s -- <MONITOR-IP>:8080/agent

Or fetch it, read it, then run it — which is what you should do with anything
you are about to pipe into a root shell:

    curl -O https://raw.githubusercontent.com/Bulldog-Master/xxOps/main/agent/install.sh
    less install.sh
    sudo bash install.sh <MONITOR-IP>:8080/agent

The monitor address is an **argument**, not an environment variable — `sudo`
strips the environment, so a variable in front of the pipe never arrives.

The installer creates an unprivileged `xxops-agent` user with one capability,
writes a sudoers file permitting a fixed list of service commands and nothing
else, and **validates it with `visudo -cf` before installing it**. If that
fails it says so and leaves the agent read-only rather than risking your
ability to use sudo at all.

Safe to re-run — that is also how you upgrade.

### 5. Add a logrotate rule

**Run on: every host — but the rule is DIFFERENT on a node and a gateway.**
They write different log files, so a gateway rule on a node rotates files that
do not exist and misses the ones that do.

Do this at build time. A host without one fills its disk months later.

    U=$(id -un); printf '%s\n' '/opt/xxnetwork/log/gateway.log' '/opt/xxnetwork/log/gateway-wrapper.log' '/opt/xxnetwork/log/chain.log' '{' '    size 200M' '    rotate 7' '    compress' '    missingok' '    notifempty' '    copytruncate' "    su $U $U" '}' | sudo tee /etc/logrotate.d/xxnetwork >/dev/null && sudo logrotate -d /etc/logrotate.d/xxnetwork 2>&1 | tail -3

`$(id -un)` derives the username per host — the `su` line is host-specific and
a copy from another machine will not work. `logrotate -d` is a dry run; run it,
because a syntax error there sits silent until the disk is full.

**On a node**, the paths and the settings both differ — a node writes more,
faster, so it rotates daily rather than by size alone:

    U=$(id -un); printf '%s\n' '/opt/xxnetwork/log/cmix.log' '/opt/xxnetwork/log/cmix-err.log' '/opt/xxnetwork/log/cmix-wrapper.log' '/opt/xxnetwork/log/chain.log' '{' '    daily' '    maxsize 250M' '    rotate 7' '    compress' '    missingok' '    notifempty' '    copytruncate' "    su $U $U" '}' | sudo tee /etc/logrotate.d/xxnetwork >/dev/null && sudo logrotate -d /etc/logrotate.d/xxnetwork 2>&1 | tail -3

Check the paths against your own machine first — `ls /opt/xxnetwork/log/*.log`
— since a node that also writes `metrics.log` may want that line too. The dry
run at the end prints what it would rotate, and `missingok` means a path that
does not exist is skipped rather than erroring.

### 6. If a gateway reaches its node by IP

**Run on: gateways only, and only some of them.** Skip this if your
gateway reaches its node by a dynamic DNS name, which most do.

Most gateways reach their node by a dynamic DNS name. If yours uses a literal
address, create `/etc/xxops/node-name` on that gateway containing the node's
label. Without it the producer derives the label from the address and you get a
host called `173`.

### 7. Cap the systemd journal

**Run on: every host.** Nodes are the worst offenders, which is why the
note below talks about them, but a gateway benefits just as much.

Nodes accumulate several GB of journal with no limit by default:

    sudo mkdir -p /etc/systemd/journald.conf.d && printf '%s\n' '[Journal]' 'SystemMaxUse=1G' | sudo tee /etc/systemd/journald.conf.d/xxops.conf >/dev/null && sudo systemctl restart systemd-journald && sudo journalctl --vacuum-size=1G

Only applies where `/var/log/journal` exists. Where the journal is volatile
this does nothing, harmlessly.

---

### 8. Measure the link speed

**Run on: every host** — a different command on a node than on a
gateway. Optional.

Optional, but it answers a question that is otherwise guesswork: is this
host's connection actually fast enough, and if something is slow, which end
is at fault?

On a **gateway**, all that is needed is a listener. The node dials it.

    curl -sL https://raw.githubusercontent.com/Bulldog-Master/xxOps/main/fixes/xxops-linkspeed-install.sh \
      | sudo bash -s -- --listener-only

On a **node**, give it the address of the gateway it is paired with:

    curl -sL https://raw.githubusercontent.com/Bulldog-Master/xxOps/main/fixes/xxops-linkspeed-install.sh \
      | sudo bash -s -- --gateway <GATEWAY-ADDRESS>

That measures the **production path** — the node to its own gateway — twice a
day, and puts the result in the host's spec panel and the daily digest. It is
the path your validator actually uses, so it is the one worth watching.

It is also capped by whatever your gateway's provider gives you, which varies
enormously. A low figure here does **not** mean your node is at fault.

#### If you run three or more validators

Add the other nodes and you get a second measurement: node to node, where
both ends are fast machines, which is the only way to see a node's real link
speed.

    curl -sL https://raw.githubusercontent.com/Bulldog-Master/xxOps/main/fixes/xxops-linkspeed-install.sh \
      | sudo bash -s -- --gateway <GATEWAY-ADDRESS> --peers <NODE-2>,<NODE-3>

The node picks a different peer each day. That rotation is the point: every
reading is limited by the *slower* of the two machines, so a single result
cannot tell you which end was slow. Several results against different partners
can.

With one or two validators there are not enough partners for that to work, so
leave `--peers` off. You still get the gateway measurement.

#### Notes

`iperf3` is installed if it is missing. The listener binds to **one** address,
not all of them — these machines have a public address too, and an
unauthenticated bandwidth test should not be reachable from the internet. The
installer checks the bind afterwards and stops itself if it got that wrong.

If the address cannot be worked out automatically, pass `--bind <address>`.
Tailscale is not required; any address your hosts can reach each other on will
do.

Safe to re-run, which is also how you change the peers.

## Part 3: first run

1. Open the app at **`http://<MONITOR-IP>:8080`** — the same address you have
   been using, on port 8080. With no account it walks you through creating one.

   **The device you open it on has to reach the monitor.** If your hosts are on
   a mesh VPN, so must the laptop, phone or tablet you are looking at it from —
   see [tailscale.md](tailscale.md). A device that is not on it gets
   "site can't be reached", which looks like the app is down when it is not.
2. If you enable two-factor, **enrol once from one QR code**, then add other
   devices from that same code. Scanning a fresh code on a second device gives
   you two different secrets and neither will reliably work.
3. Save your recovery codes somewhere off the machine.
4. Add contacts under "Who gets told" and assign each their validators. Save
   before inviting — a contact that has not been saved cannot be invited.
5. **Telegram notifications** need your own bot — alerts go through it and
   nobody else's. In Telegram, message `@BotFather`, send `/newbot`, pick a
   name, and it hands you a token. Paste that into Settings, under "Who gets
   told". Then tap Pair beside each contact — that gives you a short code. They send that code to your bot in Telegram, and the bot links their chat so their alerts reach them.

You can use the app from as many of your own devices as you like — each signs
in separately. The limit is network reach: any device you want to use it from
has to be able to reach the monitor.


The app discovers hosts from Prometheus and pairs nodes to gateways
automatically. Check the pairing and correct it in settings if a name confuses
it.

---

## Verifying it worked

Not "did it start" — did it answer.

    # every host reporting?
    curl -s 'http://<MONITOR-IP>:9090/api/v1/query?query=count(up{pilot="xxops"})'

    # producers running on their timers, not just once by hand?
    # wait two minutes after install, then:
    curl -s 'http://<MONITOR-IP>:9090/api/v1/query?query=count(xx_textfile_producer_last_run)'

    # agents reachable?
    sudo xxops-cmd discover

    # can an alert actually be delivered?
    amtool --alertmanager.url=http://127.0.0.1:9093 alert add xxops_delivery_test \
      severity=red instance=delivery-test --annotation=summary='delivery test'

That last one matters more than it looks. Prometheus pointing at Alertmanager
proves configuration, not delivery. Use `severity=red` — amber routes to a
silent receiver by design, so an amber test proves nothing.

---

## Things that will bite

**Amber never pages.** Deliberate. Amber appears in the app and the daily
digest and nowhere else.

**A dead producer freezes metrics rather than removing them.** Alloy keeps
republishing the last values it saw, so a host with a stopped producer reports
stale numbers and looks fine. `ProducerStale` exists for this.

**A host that dies vanishes from Prometheus**, it does not report zero. Any
query counting how many hosts are down must account for absent series.

**cMix crashes and recovers by design.** Panics and FATAL lines are usually
peer round failures; the process restarts itself in about twenty seconds. An
error file on disk means transient churn unless rounds have also stopped.

**Every gateway carries hundreds of WARN lines as normal background.**

**Some recovery needs your wallet.** If a node drops out of the active set it
must be re-validated in the xx network wallet, then it waits for the next era.
No amount of restarting substitutes for that, and xxOps cannot do it for you.

**Thresholds should come from your own measurements.** If a shipped rule is
noisy on your fleet, measure before changing it.

---

## Removing it

If you want a host back to how it was:

    sudo ./agent/uninstall.sh              # shows what it would remove
    sudo ./agent/uninstall.sh --apply      # backs up first, then removes

It tars everything it is about to delete into `/root` first, so the host can be
put back exactly as it was — including the key your monitor is trusted by. Add
`--purge-alloy` to remove the Alloy package as well as its config.

It never touches `/opt/xxnetwork`. Your validator keeps running throughout, and
the script prints the state of your services at the end so you can see that.

To restore: `sudo tar -xzf /root/xxops-uninstall-<stamp>.tar.gz -C /`, then
`systemctl daemon-reload` and re-enable the units.


## What this guide does not cover

- **Exact package commands for Prometheus and Alertmanager.** They vary by
  distribution; use upstream's instructions with the flags listed above.
- **TLS.** The app can serve HTTPS, which browsers require before exposing
  passkeys. On a Tailscale network `tailscale cert` issues a real certificate.
  Put the paths in a systemd drop-in rather than the unit.
- **Grafana.** Optional. The shipped Prometheus config scrapes it; remove that
  job if you are not running it.
- **How an invited contact reaches the app** when it is on a private network.
  A real open question, not an oversight.
