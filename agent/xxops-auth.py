#!/usr/bin/env python3
"""xxops-auth - manage accounts from the monitor, and get back in when locked out.

This is the escape hatch. Two factor introduces a new way to lose access - a
lost phone - so there has to be a path that never depends on the browser, the
network, or a device you might not have. That path is this, run on the monitor.

  xxops-auth status            who exists, and who has two factor on
  xxops-auth passwd <user>     set someone's password
  xxops-auth 2fa-off <user>    turn two factor off for someone
  xxops-auth rm <user>         delete an account
  xxops-auth logout            end every session on every device
  xxops-auth reset             remove ALL accounts, back to first-run setup

reset is the last resort: the app then walks you through creating an account
again, exactly as on a fresh install.
"""
import getpass, hashlib, json, os, re, secrets, sys

AUTH = os.environ.get("XXOPS_AUTH_FILE", "/var/lib/xxops/auth.json")
SESSIONS = os.environ.get("XXOPS_SESSIONS_FILE", "/var/lib/xxops/sessions.json")


def load():
    try:
        with open(AUTH) as f:
            d = json.load(f)
        if isinstance(d, dict) and isinstance(d.get("users"), dict):
            return d
    except Exception:
        pass
    return {"users": {}, "version": 1}


def save(store):
    os.makedirs(os.path.dirname(AUTH), exist_ok=True)
    tmp = AUTH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(store, f, indent=1)
    os.chmod(tmp, 0o600)
    os.replace(tmp, AUTH)


def hash_password(password):
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1, dklen=32)
    return {"algo": "scrypt", "n": 16384, "r": 8, "p": 1,
            "salt": salt.hex(), "hash": dk.hex()}


def clear_sessions():
    try:
        os.remove(SESSIONS)
    except FileNotFoundError:
        pass


def ask_password():
    p1 = getpass.getpass("New password: ")
    if len(p1) < 8:
        print("Use at least 8 characters.")
        return None
    if p1 != getpass.getpass("Again: "):
        print("Those did not match.")
        return None
    return p1


def main(argv):
    cmd = argv[1] if len(argv) > 1 else "status"
    who = argv[2].strip().lower() if len(argv) > 2 else None
    store = load()
    users = store["users"]

    if cmd == "status":
        if not users:
            print("No accounts yet. The app will walk you through creating one.")
            return 0
        print(f"{len(users)} account(s):")
        for name in sorted(users):
            u = users[name]
            bits = [u.get("role", "?")]
            if u.get("totp"):
                bits.append(f"2FA on, {len(u.get('recovery') or [])} recovery codes left")
            else:
                bits.append("2FA off")
            print(f"   {name:<20} {' · '.join(bits)}")
        return 0

    if cmd == "passwd" and who:
        if who not in users:
            print(f"No account called {who}.")
            return 1
        pw = ask_password()
        if not pw:
            return 1
        users[who]["pw"] = hash_password(pw)
        save(store)
        clear_sessions()
        print(f"Password set for {who}. Every session has been ended.")
        return 0

    if cmd == "2fa-off" and who:
        if who not in users:
            print(f"No account called {who}.")
            return 1
        users[who]["totp"] = None
        users[who]["recovery"] = []
        save(store)
        print(f"Two factor is off for {who}. They can sign in with just a password,")
        print("and turn it back on from settings.")
        return 0

    if cmd == "rm" and who:
        if who not in users:
            print(f"No account called {who}.")
            return 1
        if len(users) == 1:
            print("That is the only account. Use reset if you really mean it.")
            return 1
        del users[who]
        save(store)
        clear_sessions()
        print(f"Removed {who}.")
        return 0

    if cmd == "logout":
        clear_sessions()
        print("Every session ended. Each device will need to sign in again.")
        return 0

    if cmd == "reset":
        print(f"This removes ALL {len(users)} account(s). The app will show first-run setup.")
        if input("Type RESET to confirm: ").strip() != "RESET":
            print("Nothing changed.")
            return 1
        save({"users": {}, "version": 1})
        clear_sessions()
        print("All accounts removed. Open the app to create a new one.")
        return 0

    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
