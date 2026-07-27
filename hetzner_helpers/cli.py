#!/usr/bin/env python3
"""CLI for Hetzner Robot rescue and recovery."""

from __future__ import annotations

import argparse
import socket
import time
from pathlib import Path

from hetzner_helpers.api import RobotAPIError, RobotClient
from hetzner_helpers.env import (
    load_env,
    optional_pubkey_path,
    optional_root_part,
    optional_server_ip,
    optional_server_number,
    require_credentials,
)
from hetzner_helpers.remote import (
    SSHError,
    collect_pubkey_material,
    default_identity_path,
    install_keys,
    list_parts,
    normalize_host,
)


DEFAULT_PUBKEY_CANDIDATES = (
    Path.home() / ".ssh" / "id_ed25519.pub",
    Path.home() / ".ssh" / "id_rsa.pub",
)


def client_from_env() -> RobotClient:
    user, password = require_credentials()
    return RobotClient(user, password)


def wait_for_ssh(host: str, port: int = 22, timeout: int = 300, interval: int = 10) -> bool:
    deadline = time.time() + timeout
    print(f"Waiting for SSH on {host}:{port} (up to {timeout}s)...")
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=5):
                print("SSH is up.")
                return True
        except OSError:
            print(f"  not yet, retrying in {interval}s...")
            time.sleep(interval)
    return False


def read_pubkey(path: str | Path) -> str:
    p = Path(path).expanduser()
    if not p.is_file():
        raise SystemExit(f"Public key file not found: {p}")
    text = p.read_text().strip()
    if not text or " " not in text:
        raise SystemExit(f"Does not look like an OpenSSH public key: {p}")
    # Use first non-empty line (ignore trailing comments/blank lines).
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            return line
    raise SystemExit(f"No key material in {p}")


def default_pubkey_path() -> Path | None:
    env_path = optional_pubkey_path()
    if env_path:
        return Path(env_path).expanduser()
    for candidate in DEFAULT_PUBKEY_CANDIDATES:
        if candidate.is_file():
            return candidate
    return None


def key_name_from_pubkey(pubkey: str, override: str | None = None) -> str:
    if override:
        return override
    parts = pubkey.split()
    if len(parts) >= 3:
        return parts[-1][:64]
    return "rescue-key"


def ensure_key_registered(
    api: RobotClient,
    *,
    fingerprint: str | None = None,
    pubkey_path: str | Path | None = None,
    key_name: str | None = None,
) -> str:
    """Return a Robot key fingerprint, uploading the pubkey if needed."""
    if fingerprint:
        return fingerprint

    path = Path(pubkey_path).expanduser() if pubkey_path else default_pubkey_path()
    if path is None:
        raise SystemExit(
            "No SSH public key found. Pass --ssh-key (fingerprint), --pubkey PATH, "
            "or set SSH_PUBKEY / place a key at ~/.ssh/id_ed25519.pub."
        )

    pubkey = read_pubkey(path)
    existing = api.find_key_by_data(pubkey)
    if existing:
        fp = existing["fingerprint"]
        print(f"Using existing Robot key: {existing.get('name')} ({fp})")
        return fp

    name = key_name_from_pubkey(pubkey, key_name)
    try:
        created = api.add_key(name, pubkey)
    except RobotAPIError as e:
        if e.status == 409:
            # Name or content clash — try to find by data again, else rename.
            existing = api.find_key_by_data(pubkey)
            if existing:
                return existing["fingerprint"]
            raise SystemExit(
                f"Key upload conflict ({e}). Try --key-name with a unique name, or --list via keys list."
            ) from e
        raise SystemExit(f"Failed to upload key: {e}") from e

    fp = created["fingerprint"]
    print(f"Uploaded key {created.get('name')} → {fp}")
    return fp


def confirm_action(prompt: str, *, yes: bool) -> None:
    if yes:
        return
    try:
        answer = input(f"{prompt} [y/N] ").strip().lower()
    except EOFError:
        raise SystemExit("Aborted (no TTY). Re-run with --yes.") from None
    if answer not in ("y", "yes"):
        raise SystemExit("Aborted.")


def print_server_line(srv: dict) -> None:
    print(
        f"    #{srv.get('server_number')}  {srv.get('server_ip')}  "
        f"{srv.get('product')}  ({srv.get('dc')})  "
        f"status={srv.get('status')}  rescue={srv.get('rescue')}  reset={srv.get('reset')}"
    )


def cmd_check(_args: argparse.Namespace) -> None:
    api = client_from_env()
    print("Verifying credentials (read-only)...")
    try:
        servers = api.list_servers()
    except RobotAPIError as e:
        if e.status == 401:
            raise SystemExit("401 Unauthorized — wrong webservice user/password.") from e
        raise SystemExit(str(e)) from e
    print(f"Credentials OK — {len(servers)} server(s):")
    for entry in servers:
        print_server_line(entry.get("server", entry))


def cmd_servers(args: argparse.Namespace) -> None:
    api = client_from_env()
    if args.server_number is not None:
        try:
            srv = api.get_server(args.server_number)
        except RobotAPIError as e:
            raise SystemExit(str(e)) from e
        print_server_line(srv)
        return
    servers = api.list_servers()
    if not servers:
        print("No servers on this account.")
        return
    print(f"{len(servers)} server(s):")
    for entry in servers:
        print_server_line(entry.get("server", entry))


def cmd_keys(args: argparse.Namespace) -> None:
    api = client_from_env()
    action = args.keys_action

    if action == "list":
        keys = api.list_keys()
        if not keys:
            print("No SSH keys registered in Robot. Add one with: hetzner-rescue keys add PATH")
            return
        print(f"{len(keys)} key(s):")
        for entry in keys:
            k = entry.get("key", entry)
            print(
                f"    {k.get('fingerprint')}  {k.get('name')}  "
                f"({k.get('type')} {k.get('size')})"
            )
        return

    if action == "add":
        pubkey = read_pubkey(args.pubkey)
        name = key_name_from_pubkey(pubkey, args.key_name)
        try:
            created = api.add_key(name, pubkey)
        except RobotAPIError as e:
            if e.status == 409:
                raise SystemExit("A key with this name or content already exists — see keys list.") from e
            raise SystemExit(str(e)) from e
        print(f"Key uploaded: {created.get('name')}")
        print(f"  Fingerprint: {created.get('fingerprint')}")
        return

    if action == "delete":
        confirm_action(f"Delete Robot key {args.fingerprint}?", yes=args.yes)
        try:
            api.delete_key(args.fingerprint)
        except RobotAPIError as e:
            raise SystemExit(str(e)) from e
        print(f"Deleted key {args.fingerprint}")
        return

    raise SystemExit(f"Unknown keys action: {action}")


def cmd_rescue(args: argparse.Namespace) -> None:
    api = client_from_env()
    n = args.server_number
    action = args.rescue_action

    try:
        if action == "status":
            rescue = api.get_rescue(n)
            _print_rescue(rescue)
            return
        if action == "last":
            rescue = api.get_rescue_last(n)
            _print_rescue(rescue)
            return
        if action == "deactivate":
            confirm_action(f"Deactivate rescue for server #{n}?", yes=args.yes)
            rescue = api.deactivate_rescue(n)
            print("Rescue deactivated.")
            _print_rescue(rescue)
            return
        if action == "activate":
            fp = None
            if args.ssh_key or args.pubkey:
                fp = ensure_key_registered(
                    api,
                    fingerprint=args.ssh_key,
                    pubkey_path=args.pubkey,
                    key_name=args.key_name,
                )
            ip = args.server_ip or optional_server_ip()
            try:
                resolved = api.server_ip(n)
                ip = ip or resolved
            except RobotAPIError:
                pass
            confirm_action(
                f"Activate rescue on #{n}" + (f" ({ip})" if ip else "") + "?",
                yes=args.yes,
            )
            rescue = api.activate_rescue(n, ssh_fingerprint=fp)
            print("Rescue activated.")
            _print_rescue(rescue)
            if not fp and rescue.get("password"):
                print(f"  Password: {rescue['password']}  ← save this!")
            if args.reset:
                print(f"Triggering {args.reset_type} reset...")
                api.reset(n, args.reset_type)
                print("Reset sent.")
                if args.wait and ip:
                    ok = wait_for_ssh(ip, timeout=args.timeout)
                    if ok:
                        print(f"\nConnect: ssh root@{ip}")
                    else:
                        print("SSH did not come up within the timeout.")
            return
    except RobotAPIError as e:
        raise SystemExit(str(e)) from e

    raise SystemExit(f"Unknown rescue action: {action}")


def _print_rescue(rescue: dict) -> None:
    print(f"  server:   #{rescue.get('server_number')}  {rescue.get('server_ip')}")
    print(f"  active:   {rescue.get('active')}")
    print(f"  os:       {rescue.get('os')}")
    if rescue.get("password"):
        print(f"  password: {rescue.get('password')}")
    keys = rescue.get("authorized_key") or []
    if keys:
        print("  authorized_key:")
        for k in keys:
            if isinstance(k, dict):
                print(f"    {k.get('fingerprint', k)}")
            else:
                print(f"    {k}")


def cmd_reset(args: argparse.Namespace) -> None:
    api = client_from_env()
    n = args.server_number
    ip = None
    try:
        ip = api.server_ip(n)
    except RobotAPIError:
        pass
    label = f"#{n}" + (f" ({ip})" if ip else "")
    confirm_action(f"Send {args.reset_type} reset to {label}?", yes=args.yes)
    try:
        result = api.reset(n, args.reset_type)
    except RobotAPIError as e:
        raise SystemExit(str(e)) from e
    print(f"Reset sent: type={result.get('type', args.reset_type)}")


def cmd_recover(args: argparse.Namespace) -> None:
    api = client_from_env()
    n = args.server_number

    try:
        srv = api.get_server(n)
    except RobotAPIError as e:
        raise SystemExit(str(e)) from e

    ip = args.server_ip or optional_server_ip() or srv.get("server_ip")
    if not ip:
        raise SystemExit("Could not resolve server IP; pass --server-ip.")

    warn = api.firewall_may_block_ssh(n)
    if warn:
        print(f"Warning: {warn}")

    fp = ensure_key_registered(
        api,
        fingerprint=args.ssh_key,
        pubkey_path=args.pubkey,
        key_name=args.key_name,
    )

    confirm_action(
        f"Recover #{n} ({ip}): activate rescue with key {fp}, then {args.reset_type} reset?",
        yes=args.yes,
    )

    try:
        rescue = api.activate_rescue(n, ssh_fingerprint=fp)
        print("Rescue activated.")
        _print_rescue(rescue)
        print(f"Triggering {args.reset_type} reset...")
        api.reset(n, args.reset_type)
        print("Reset sent.")
    except RobotAPIError as e:
        raise SystemExit(str(e)) from e

    if args.no_wait:
        print(f"\nSkipped wait. When ready: ssh root@{ip}")
    else:
        ok = wait_for_ssh(ip, timeout=args.timeout)
        if not ok:
            print("SSH did not come up within the timeout — check the server manually.")
            return

    print()
    print("Next steps (from this machine, with the matching private key):")
    print(f"  ssh -o IdentitiesOnly=yes -i ~/.ssh/id_ed25519 root@{ip}")
    print()
    print("  # Inject keys into the installed system (list disks, then install):")
    print(f"  hetzner-rescue install-keys --host {ip} --list-parts")
    print(
        f"  hetzner-rescue install-keys --host {ip} --root-part /dev/mdX "
        f"--identity ~/.ssh/id_ed25519"
    )
    print()
    print("  # Boot back to the installed OS (clears one-shot rescue):")
    print(f"  hetzner-rescue reboot-to-disk {n} --yes")


def _resolve_ssh_host(args: argparse.Namespace) -> str:
    if args.host:
        return normalize_host(args.host)
    ip = args.server_ip or optional_server_ip()
    if ip:
        return normalize_host(ip)
    if getattr(args, "server_number", None) is not None:
        api = client_from_env()
        try:
            ip = api.server_ip(args.server_number)
        except (RobotAPIError, ValueError) as e:
            raise SystemExit(f"Could not resolve server IP: {e}") from e
        return normalize_host(ip)
    raise SystemExit("Pass --host HOST, --server-ip, or a server number.")


def cmd_install_keys(args: argparse.Namespace) -> None:
    host = _resolve_ssh_host(args)
    identity = Path(args.identity).expanduser() if args.identity else default_identity_path(
        Path(args.pubkey[0]).expanduser() if args.pubkey else None
    )
    if identity is None and not args.list_parts:
        raise SystemExit(
            "Pass --identity PATH (private key for rescue SSH), or set SSH_KEY in the environment."
        )

    if args.list_parts:
        try:
            out = list_parts(host, identity=identity)
        except SSHError as e:
            raise SystemExit(f"SSH failed: {e}") from e
        print(out)
        print("Set --root-part to the installed-system root device (often /dev/md2).")
        return

    root_part = args.root_part or optional_root_part()
    if not root_part:
        raise SystemExit(
            "ROOT_PART is required. Discover devices with: "
            f"hetzner-rescue install-keys --host {host.split('@')[-1]} --list-parts"
        )

    try:
        pubkeys = collect_pubkey_material(
            [Path(p).expanduser() for p in args.pubkey] if args.pubkey else None
        )
    except (FileNotFoundError, ValueError) as e:
        raise SystemExit(str(e)) from e

    key_count = sum(1 for line in pubkeys.splitlines() if line.strip())
    target = args.user
    label = f"{target}@{host.split('@', 1)[-1]} on {root_part}"
    confirm_action(
        f"Install {key_count} public key(s) for {label}?",
        yes=args.yes,
    )

    try:
        out = install_keys(
            host,
            root_part,
            pubkeys,
            identity=identity,
            target_user=target,
            append=args.append,
            grant_sudo=not args.no_grant_sudo,
            permit_root_login=not args.no_permit_root_login,
        )
    except SSHError as e:
        raise SystemExit(
            f"SSH failed: {e}\n"
            "Tip: use --identity with the private key matching your rescue authorized_key, "
            "and -o IdentitiesOnly=yes (this command sets that automatically)."
        ) from e

    print(out.strip())
    disk_ip = host.split("@", 1)[-1]
    if target == "root":
        print(f"\nAfter reboot to disk: ssh root@{disk_ip}")
    else:
        print(f"\nAfter reboot to disk: ssh {target}@{disk_ip}")


def cmd_reboot_to_disk(args: argparse.Namespace) -> None:
    api = client_from_env()
    n = args.server_number
    ip = None
    try:
        ip = api.server_ip(n)
    except RobotAPIError:
        pass
    label = f"#{n}" + (f" ({ip})" if ip else "")
    confirm_action(
        f"Deactivate rescue and {args.reset_type}-reset {label} so it boots from disk?",
        yes=args.yes,
    )
    try:
        status = api.get_rescue(n)
        if status.get("active"):
            api.deactivate_rescue(n)
            print("Rescue deactivated.")
        else:
            print("Rescue already inactive.")
        api.reset(n, args.reset_type)
        print(f"Reset sent ({args.reset_type}). Server should boot the installed OS.")
    except RobotAPIError as e:
        raise SystemExit(str(e)) from e

    if args.wait and ip:
        wait_for_ssh(ip, timeout=args.timeout)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hetzner-rescue",
        description="Activate Hetzner rescue mode and recover locked-out Robot servers.",
    )
    parser.add_argument(
        "--env-file",
        help="Path to .env (default: project .env or cwd)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_check = sub.add_parser("check", help="Verify webservice credentials (read-only)")
    p_check.set_defaults(func=cmd_check)

    p_servers = sub.add_parser("servers", help="List servers on the account")
    p_servers.add_argument(
        "server_number",
        type=int,
        nargs="?",
        default=None,
        help="Optional: show one server by number",
    )
    p_servers.set_defaults(func=cmd_servers)

    p_server = sub.add_parser("server", help="Show one server (alias of servers <n>)")
    p_server.add_argument("server_number", type=int)
    p_server.set_defaults(func=cmd_servers)

    p_keys = sub.add_parser("keys", help="Manage SSH keys registered in Robot")
    keys_sub = p_keys.add_subparsers(dest="keys_action", required=True)

    k_list = keys_sub.add_parser("list", help="List registered keys")
    k_list.set_defaults(func=cmd_keys)

    k_add = keys_sub.add_parser("add", help="Upload a public key file")
    k_add.add_argument("pubkey", help="Path to OpenSSH .pub file")
    k_add.add_argument("--key-name", help="Name in Robot (default: key comment)")
    k_add.set_defaults(func=cmd_keys)

    k_del = keys_sub.add_parser("delete", help="Delete a key by fingerprint")
    k_del.add_argument("fingerprint", help="Key fingerprint (aa:bb:...)")
    k_del.add_argument("--yes", action="store_true", help="Skip confirmation")
    k_del.set_defaults(func=cmd_keys)

    p_rescue = sub.add_parser("rescue", help="Rescue system lifecycle")
    rescue_sub = p_rescue.add_subparsers(dest="rescue_action", required=True)

    for name, help_text in (
        ("status", "Show current rescue configuration"),
        ("last", "Show last rescue activation"),
        ("deactivate", "Deactivate rescue for next boot"),
    ):
        rp = rescue_sub.add_parser(name, help=help_text)
        rp.add_argument("server_number", type=int)
        rp.add_argument("--yes", action="store_true")
        rp.set_defaults(func=cmd_rescue)

    r_act = rescue_sub.add_parser("activate", help="Activate rescue (optionally reset)")
    r_act.add_argument("server_number", type=int)
    r_act.add_argument("--ssh-key", help="Existing Robot key fingerprint")
    r_act.add_argument("--pubkey", help="Local .pub file to register/use")
    r_act.add_argument("--key-name", help="Name when uploading a new key")
    r_act.add_argument("--server-ip", help="IP for wait-for-SSH (default: from API)")
    r_act.add_argument("--reset", action="store_true", help="Trigger reset after activate")
    r_act.add_argument("--reset-type", default="hw", choices=["sw", "hw", "man"])
    r_act.add_argument("--wait", action="store_true", help="Wait for SSH after reset")
    r_act.add_argument("--timeout", type=int, default=300, help="SSH wait timeout seconds")
    r_act.add_argument("--yes", action="store_true")
    r_act.set_defaults(func=cmd_rescue)

    p_reset = sub.add_parser("reset", help="Trigger a server reset")
    p_reset.add_argument("server_number", type=int, nargs="?", default=None)
    p_reset.add_argument("--reset-type", default="hw", choices=["sw", "hw", "man"])
    p_reset.add_argument("--yes", action="store_true")
    p_reset.set_defaults(func=cmd_reset)

    p_recover = sub.add_parser(
        "recover",
        help="One-shot: ensure key → activate rescue → reset → wait for SSH",
    )
    p_recover.add_argument("server_number", type=int, nargs="?", default=None)
    p_recover.add_argument("--ssh-key", help="Existing Robot key fingerprint")
    p_recover.add_argument("--pubkey", help="Local .pub file (default: ~/.ssh/id_ed25519.pub)")
    p_recover.add_argument("--key-name", help="Name when uploading a new key")
    p_recover.add_argument("--server-ip", help="Override server IP")
    p_recover.add_argument("--reset-type", default="hw", choices=["sw", "hw", "man"])
    p_recover.add_argument("--timeout", type=int, default=300)
    p_recover.add_argument("--no-wait", action="store_true", help="Do not poll for SSH")
    p_recover.add_argument("--yes", action="store_true")
    p_recover.set_defaults(func=cmd_recover)

    p_rtd = sub.add_parser(
        "reboot-to-disk",
        help="Deactivate rescue and reset so the installed OS boots",
    )
    p_rtd.add_argument("server_number", type=int, nargs="?", default=None)
    p_rtd.add_argument("--reset-type", default="hw", choices=["sw", "hw", "man"])
    p_rtd.add_argument("--wait", action="store_true", help="Wait for SSH after reset")
    p_rtd.add_argument("--timeout", type=int, default=300)
    p_rtd.add_argument("--yes", action="store_true")
    p_rtd.set_defaults(func=cmd_reboot_to_disk)

    p_install = sub.add_parser(
        "install-keys",
        help="SSH into rescue and install public keys on the installed system",
    )
    p_install.add_argument(
        "server_number",
        type=int,
        nargs="?",
        default=None,
        help="Robot server number (resolves IP for root@IP); optional if --host is set",
    )
    p_install.add_argument(
        "--host",
        help="Rescue SSH target (IP or user@host; default user root)",
    )
    p_install.add_argument("--server-ip", help="Server IP (alias for --host without user)")
    p_install.add_argument(
        "--list-parts",
        action="store_true",
        help="List block devices on the rescue host (no keys written)",
    )
    p_install.add_argument(
        "--root-part",
        help="Installed root device (e.g. /dev/md2); or set ROOT_PART in .env",
    )
    p_install.add_argument(
        "--identity",
        help="Private SSH key for rescue login (default: SSH_KEY or ~/.ssh/id_ed25519)",
    )
    p_install.add_argument(
        "--pubkey",
        action="append",
        metavar="PATH",
        help="Public key file to install (repeatable; default: all ~/.ssh/*.pub)",
    )
    p_install.add_argument(
        "--user",
        default="root",
        help="Account on the installed system (default: root). Other users are created if missing.",
    )
    p_install.add_argument(
        "--append",
        action="store_true",
        help="Append keys instead of replacing authorized_keys",
    )
    p_install.add_argument(
        "--no-grant-sudo",
        action="store_true",
        help="For non-root --user: do not add NOPASSWD sudo",
    )
    p_install.add_argument(
        "--no-permit-root-login",
        action="store_true",
        help="For root: do not adjust PermitRootLogin in sshd_config",
    )
    p_install.add_argument("--yes", action="store_true", help="Skip confirmation")
    p_install.set_defaults(func=cmd_install_keys)

    return parser


def _apply_server_number_default(args: argparse.Namespace) -> None:
    """Fill server_number from SERVER_NUMBER env when omitted."""
    if getattr(args, "server_number", None) is not None:
        return
    if args.command in ("reset", "recover", "reboot-to-disk"):
        env_n = optional_server_number()
        if env_n is None:
            raise SystemExit("server_number is required (argument or SERVER_NUMBER in .env).")
        args.server_number = env_n
    if args.command == "install-keys" and getattr(args, "server_number", None) is None:
        env_n = optional_server_number()
        if env_n is not None:
            args.server_number = env_n


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    load_env(args.env_file)
    _apply_server_number_default(args)
    args.func(args)


if __name__ == "__main__":
    main()
