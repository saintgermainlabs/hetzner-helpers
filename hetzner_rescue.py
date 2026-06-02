#!/usr/bin/env python3
"""
hetzner_rescue.py — activate rescue mode and reset a Hetzner dedicated server
via the Robot Webservice API. No browser required.

Usage:
  export ROBOT_USER="your-webservice-user"
  export ROBOT_PASS="your-webservice-password"
  python3 hetzner_rescue.py <server-number> [--ssh-key <fingerprint>]
"""

import os
import sys
import time
import argparse
import requests

ROBOT_BASE = "https://robot-ws.your-server.de"


def get_session(user: str, password: str) -> requests.Session:
    s = requests.Session()
    s.auth = (user, password)
    return s


def check_credentials(session: requests.Session) -> list:
    """Read-only call to verify the webservice credentials work. Reboots nothing."""
    url = f"{ROBOT_BASE}/server"
    resp = session.get(url)
    resp.raise_for_status()
    return resp.json()


def list_keys(session: requests.Session) -> list:
    """List SSH public keys registered in Robot key management."""
    resp = session.get(f"{ROBOT_BASE}/key")
    if resp.status_code == 404:
        return []  # no keys registered yet
    resp.raise_for_status()
    return resp.json()


def add_key(session: requests.Session, name: str, public_key: str) -> dict:
    """Register a public key in Robot. Returns the key entry (incl. fingerprint)."""
    resp = session.post(f"{ROBOT_BASE}/key", data={"name": name, "data": public_key})
    resp.raise_for_status()
    return resp.json()


def activate_rescue(session: requests.Session, server_number: int, ssh_key: str | None = None) -> dict:
    url = f"{ROBOT_BASE}/boot/{server_number}/rescue"
    data = {"os": "linux", "arch": "64"}
    if ssh_key:
        data["authorized_key"] = ssh_key

    resp = session.post(url, data=data)
    resp.raise_for_status()
    return resp.json()


def trigger_reset(session: requests.Session, server_number: int, reset_type: str = "hw") -> dict:
    """
    reset_type options:
      sw   — software reboot (ACPI)
      hw   — hardware reset (power cycle)
      man  — manual reset request to Hetzner support
    """
    url = f"{ROBOT_BASE}/reset/{server_number}"
    resp = session.post(url, data={"type": reset_type})
    resp.raise_for_status()
    return resp.json()


def wait_for_ssh(host: str, port: int = 22, timeout: int = 300, interval: int = 10) -> bool:
    import socket
    deadline = time.time() + timeout
    print(f"Waiting for SSH on {host}:{port} (up to {timeout}s)...")
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=5):
                print("SSH is up!")
                return True
        except (OSError, ConnectionRefusedError):
            print(f"  not yet, retrying in {interval}s...")
            time.sleep(interval)
    return False


def main():
    parser = argparse.ArgumentParser(description="Activate Hetzner rescue mode via Robot API")
    parser.add_argument("server_number", type=int, nargs="?", help="Robot server number (not IP)")
    parser.add_argument("--check", action="store_true",
                        help="Only verify credentials via a read-only API call, then exit")
    parser.add_argument("--list-keys", action="store_true",
                        help="List SSH keys registered in Robot (name + fingerprint), then exit")
    parser.add_argument("--add-key", metavar="PUBKEY_PATH",
                        help="Upload a public key file to Robot, print its fingerprint, then exit")
    parser.add_argument("--key-name", help="Name for the uploaded key (default: the key's comment)")
    parser.add_argument("--ssh-key", help="SSH key fingerprint to inject (e.g. aa:bb:cc:...)")
    parser.add_argument("--reset-type", default="hw", choices=["sw", "hw", "man"],
                        help="Reset type: sw=software, hw=hardware (default), man=manual")
    parser.add_argument("--server-ip", help="Server IP to poll for SSH readiness after reset")
    args = parser.parse_args()

    user = os.environ.get("ROBOT_USER")
    password = os.environ.get("ROBOT_PASS")
    if not user or not password:
        sys.exit("Set ROBOT_USER and ROBOT_PASS environment variables.")

    session = get_session(user, password)

    if args.check:
        print("Verifying credentials (read-only, nothing will reboot)...")
        try:
            servers = check_credentials(session)
        except requests.HTTPError as e:
            code = e.response.status_code if e.response is not None else "?"
            if code == 401:
                sys.exit("✗ 401 Unauthorized — wrong webservice user/password.")
            sys.exit(f"✗ API returned HTTP {code}: {e}")
        print(f"✓ Credentials OK — {len(servers)} server(s) on this account:")
        for entry in servers:
            srv = entry.get("server", {})
            print(f"    #{srv.get('server_number')}  {srv.get('server_ip')}  "
                  f"{srv.get('product')}  ({srv.get('dc')})")
        return

    if args.list_keys:
        keys = list_keys(session)
        if not keys:
            print("No SSH keys registered in Robot yet. Add one with --add-key.")
            return
        print(f"{len(keys)} key(s) registered:")
        for entry in keys:
            k = entry.get("key", {})
            print(f"    {k.get('fingerprint')}  {k.get('name')}  ({k.get('type')} {k.get('size')})")
        return

    if args.add_key:
        with open(args.add_key) as f:
            pub = f.read().strip()
        name = args.key_name or (pub.split()[-1] if len(pub.split()) >= 3 else "rescue-key")
        try:
            result = add_key(session, name, pub)
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == 409:
                sys.exit("✗ A key with this name or content already exists — see --list-keys.")
            raise
        k = result.get("key", {})
        print(f"✓ Key uploaded: {k.get('name')}")
        print(f"  Fingerprint: {k.get('fingerprint')}")
        print(f"\nUse it with:  python3 hetzner_rescue.py <server-number> --ssh-key {k.get('fingerprint')}")
        return

    if args.server_number is None:
        sys.exit("server_number is required unless --check is used.")

    print(f"[1/2] Activating rescue mode for server {args.server_number}...")
    rescue_info = activate_rescue(session, args.server_number, args.ssh_key)
    rescue = rescue_info.get("rescue", {})
    print(f"      OS:       {rescue.get('os')}")
    print(f"      Arch:     {rescue.get('arch')}")
    if not args.ssh_key:
        print(f"      Password: {rescue.get('password')}  ← save this!")

    print(f"\n[2/2] Triggering {args.reset_type} reset...")
    reset_info = trigger_reset(session, args.server_number, args.reset_type)
    print(f"      Reset type sent: {reset_info.get('reset', {}).get('type')}")

    if args.server_ip:
        ok = wait_for_ssh(args.server_ip)
        if ok:
            key_flag = f"-i ~/.ssh/your_rescue_key " if args.ssh_key else ""
            print(f"\nConnect with: ssh {key_flag}root@{args.server_ip}")
        else:
            print("\nSSH did not come up within the timeout — check the server manually.")
    else:
        print("\nDone. Reboot in progress; SSH will be available once the rescue system boots.")


if __name__ == "__main__":
    main()