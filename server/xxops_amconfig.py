"""Building Alertmanager configuration from notify settings.

Lifted out of xxops-server.py unchanged. Settings dict in, YAML text out - it
reads nothing, writes nothing and knows no paths.

validate() and apply_config() stayed behind on purpose: they run amtool and
write to disk, which is doing something to a machine rather than transforming
data.
"""

import re

def yq(s):
    """Quote a scalar for YAML."""
    return "'" + str(s).replace("'", "''") + "'"

def chat_id(v):
    """Telegram chat ids are integers, negative for groups. None if unusable."""
    t = str(v or "").strip()
    return t if re.fullmatch(r"-?\d+", t) else None

def slug(s):
    return re.sub(r"[^a-z0-9_]+", "_", str(s).lower()).strip("_") or "contact"

def channels_for(c, n):
    """Emit the receiver body for one contact. Returns [] if it has no channel."""
    out = []
    emails = [e.strip() for e in str(c.get("emails", "")).replace(";", ",").split(",") if e.strip()]
    if emails and n["smtp"].get("host"):
        out.append("    email_configs:")
        out.append(f"      - to: {yq(', '.join(emails))}")
        out.append("        send_resolved: true")
    cid = chat_id(c.get("telegram_chat_id"))
    if cid and n["telegram"].get("bot_token"):
        out.append("    telegram_configs:")
        out.append(f"      - bot_token: {yq(n['telegram']['bot_token'])}")
        out.append(f"        chat_id: {cid}")
        out.append("        parse_mode: HTML")
        out.append("        send_resolved: true")
        out.append("        message: |")
        out.append('          {{ if eq .Status "firing" }}\U0001F534{{ else }}\U0001F7E2 resolved:{{ end }} <b>{{ .CommonLabels.alertname }}</b>')
        out.append("          {{ range .Alerts }}{{ .Annotations.summary }}")
        out.append("          {{ if .Annotations.detail }}<i>{{ .Annotations.detail }}</i>{{ end }}")
        out.append("          {{ end }}")
    if c.get("webhook"):
        out.append("    webhook_configs:")
        out.append(f"      - url: {yq(c['webhook'])}")
        out.append("        send_resolved: true")
    return out

def build_config(n, pairs):
    """
    Render alertmanager.yml.

    Routing shape, proven with amtool before this was written:
      1. severity=amber  -> quiet          (first, no continue: amber never pages)
      2. one route per contact, continue: true, matching all their validators
         so a validator with two owners reaches both
      3. anything unassigned falls through to the fallback receiver
    """
    L = ["global:", "  resolve_timeout: 5m"]
    s = n.get("smtp", {})
    if s.get("host"):
        L.append(f"  smtp_smarthost: {yq(s['host'] + ':' + str(s.get('port') or 587))}")
        L.append(f"  smtp_from: {yq(s.get('from') or s.get('username') or 'xxops@localhost')}")
        if s.get("username"):
            L.append(f"  smtp_auth_username: {yq(s['username'])}")
        if s.get("password"):
            L.append(f"  smtp_auth_password: {yq(s['password'])}")
    L += ["", "route:", "  receiver: fallback",
          "  group_by: [alertname, instance]",
          "  group_wait: 45s", "  group_interval: 5m", "  repeat_interval: 6h",
          "  routes:",
          "    # amber is context, never a page",
          '    - matchers: [ severity="amber" ]',
          "      receiver: quiet",
          "    # A pending slash cannot be acted on and cannot clear until the",
          "    # deferral era passes - 28 eras, about 28 days. Repeating every",
          "    # 6h would be ~100 red notifications for one event nobody can do",
          "    # anything about, which teaches you to ignore red. Say it once,",
          "    # then once more when it applies (the resolved notice).",
          "    # A year is Alertmanager's idiom for never repeating.",
          '    - matchers: [ alertname="PendingSlashes" ]',
          "      receiver: fallback",
          "      repeat_interval: 8760h"]

    live = []
    for c in n.get("contacts", []):
        if not channels_for(c, n):
            continue
        hosts = []
        for node in c.get("validators", []):
            hosts.append(node)
            gw = pairs.get(node)
            if gw:
                hosts.append(gw)
        if not hosts:
            continue
        rx = "|".join(re.escape(h) for h in sorted(set(hosts)))
        name = "contact_" + slug(c.get("id") or c.get("name"))
        L.append(f'    - matchers: [ instance=~"{rx}" ]')
        L.append(f"      receiver: {name}")
        L.append("      continue: true")
        live.append((name, c))

    L += ["", "receivers:", "  - name: quiet"]

    fb = dict(n.get("fallback", {}))
    fb_body = channels_for({"emails": fb.get("emails", ""),
                            "telegram_chat_id": fb.get("telegram_chat_id", "")}, n)
    L.append("  - name: fallback")
    L += fb_body

    for name, c in live:
        L.append(f"  - name: {name}")
        L += channels_for(c, n)

    return "\n".join(L) + "\n"

def problems(n):
    """Things worth refusing to save, phrased for a person."""
    out = []
    for c in n.get("contacts", []):
        who = c.get("name") or c.get("id") or "a contact"
        raw = str(c.get("telegram_chat_id") or "").strip()
        if raw and not chat_id(raw):
            out.append(f"{who}: Telegram ID must be a number — got \"{raw}\".")
        for e in str(c.get("emails", "")).replace(";", ",").split(","):
            e = e.strip()
            if e and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", e):
                out.append(f"{who}: \"{e}\" doesn't look like an email address.")
        if str(c.get("emails", "")).strip() and not n.get("smtp", {}).get("host"):
            out.append(f"{who} has an email address but no mail server is set up yet.")
    s = n.get("smtp", {})
    if s.get("host") and not s.get("from"):
        out.append("The mail server needs a 'from' address.")

    # a config where nothing can receive is valid YAML and amtool accepts it,
    # so this is the only place it can be caught
    reaches = any(channels_for(c, n) for c in n.get("contacts", []))
    fb = n.get("fallback", {})
    if not reaches:
        reaches = bool(channels_for({"emails": fb.get("emails", ""),
                                     "telegram_chat_id": fb.get("telegram_chat_id", "")}, n))
    # An operator part-way through setup has no contacts and no fallback yet,
    # and MUST be able to save: the bot token is stored by this same save, and
    # pairing a contact reads that token from saved state. Refusing here made
    # Telegram impossible to set up at all -- no save without a channel, no
    # channel without pairing, no pairing without a saved token.
    #
    # Contacts that exist but cannot be reached is a different thing, and still
    # an error: that is someone who believes they are covered and is not.
    setting_up = (not n.get("contacts")) and not (
        str(fb.get("emails", "")).strip()
        or str(fb.get("telegram_chat_id", "")).strip())
    if not reaches and not setting_up:
        out.append("Nothing would receive alerts. Give at least one contact "
                   "a channel, or set a fallback, before saving.")
    return out
