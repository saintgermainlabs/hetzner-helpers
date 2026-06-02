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
    parser.add_argument("server_number", type=int, help="Robot server number (not IP)")
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