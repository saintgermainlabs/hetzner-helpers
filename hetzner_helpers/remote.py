"""SSH into rescue and install SSH keys on the installed system."""

from __future__ import annotations

import base64
import os
import subprocess
from pathlib import Path

ROOT_REMOTE_SCRIPT = r"""
set -euo pipefail
: "${ROOT_PART:?}"

if ! mountpoint -q /mnt; then
    mount "$ROOT_PART" /mnt
fi

install -d -m 700 /mnt/root/.ssh
if [[ "${APPEND_KEYS:-false}" == "true" ]] && [[ -f /mnt/root/.ssh/authorized_keys ]]; then
    printf '%s' "$KEYS_B64" | base64 -d >> /mnt/root/.ssh/authorized_keys
    printf '\n' >> /mnt/root/.ssh/authorized_keys
else
    printf '%s' "$KEYS_B64" | base64 -d > /mnt/root/.ssh/authorized_keys
fi
chmod 600 /mnt/root/.ssh/authorized_keys
chown -R root:root /mnt/root/.ssh

installed="$(grep -cve '^[[:space:]]*$' /mnt/root/.ssh/authorized_keys || true)"
echo "installed $installed key(s) -> /root/.ssh/authorized_keys"

if [[ "${PERMIT_ROOT_LOGIN:-true}" == "true" ]]; then
    CFG=/mnt/etc/ssh/sshd_config
    cp -a "$CFG" "$CFG.bak.$(cat /proc/sys/kernel/random/uuid | cut -c1-8)" 2>/dev/null || cp -a "$CFG" "$CFG.bak"
    sed -i -E 's/^([[:space:]]*PermitRootLogin[[:space:]].*)$/#\1/I' "$CFG"
    echo "PermitRootLogin prohibit-password" >> "$CFG"
    shopt -s nullglob
    for f in /mnt/etc/ssh/sshd_config.d/*.conf; do
        if grep -qiE '^[[:space:]]*PermitRootLogin' "$f"; then
            sed -i -E 's/^([[:space:]]*PermitRootLogin[[:space:]].*)$/#\1/I' "$f"
        fi
    done
    echo "set PermitRootLogin prohibit-password"
fi
"""

USER_REMOTE_SCRIPT = r"""
set -euo pipefail
: "${ROOT_PART:?}" "${NEW_USER:?}"

if ! mountpoint -q /mnt; then
    mount "$ROOT_PART" /mnt
fi
for d in dev proc sys; do
    mountpoint -q "/mnt/$d" || mount --bind "/$d" "/mnt/$d"
done

if ! chroot /mnt id -u "$NEW_USER" >/dev/null 2>&1; then
    chroot /mnt useradd -m -s /bin/bash "$NEW_USER"
    echo "created user $NEW_USER"
else
    echo "user $NEW_USER already exists — updating keys"
fi

if [[ "$GRANT_SUDO" == "true" ]]; then
    chroot /mnt usermod -aG sudo "$NEW_USER" 2>/dev/null \
        || chroot /mnt usermod -aG wheel "$NEW_USER" 2>/dev/null || true
    echo "$NEW_USER ALL=(ALL) NOPASSWD:ALL" > "/mnt/etc/sudoers.d/90-$NEW_USER"
    chmod 440 "/mnt/etc/sudoers.d/90-$NEW_USER"
fi

install -d -m 700 "/mnt/home/$NEW_USER/.ssh"
if [[ "${APPEND_KEYS:-false}" == "true" ]] && [[ -f "/mnt/home/$NEW_USER/.ssh/authorized_keys" ]]; then
    printf '%s' "$KEYS_B64" | base64 -d >> "/mnt/home/$NEW_USER/.ssh/authorized_keys"
    printf '\n' >> "/mnt/home/$NEW_USER/.ssh/authorized_keys"
else
    printf '%s' "$KEYS_B64" | base64 -d > "/mnt/home/$NEW_USER/.ssh/authorized_keys"
fi
chmod 600 "/mnt/home/$NEW_USER/.ssh/authorized_keys"
chroot /mnt chown -R "$NEW_USER:$NEW_USER" "/home/$NEW_USER/.ssh"

installed="$(grep -cve '^[[:space:]]*$' "/mnt/home/$NEW_USER/.ssh/authorized_keys" || true)"
echo "installed $installed key(s) -> /home/$NEW_USER/.ssh/authorized_keys"
"""

LIST_PARTS_REMOTE = r"""
lsblk -o NAME,SIZE,FSTYPE,TYPE,MOUNTPOINT,LABEL,UUID
echo
echo "=== /proc/mdstat ==="
cat /proc/mdstat 2>/dev/null || true
echo
echo "=== Candidates (ext4/xfs/btrfs) ==="
lsblk -nrpo NAME,FSTYPE,TYPE | awk '$2 ~ /^(ext4|xfs|btrfs)$/ && $3 ~ /^(part|lvm|raid)/ { print $1, $2, $3 }'
"""


class SSHError(Exception):
    pass


def normalize_host(host: str, *, user: str = "root") -> str:
    host = host.strip()
    if "@" in host:
        return host
    return f"{user}@{host}"


def default_identity_path(pubkey_hint: Path | None = None) -> Path | None:
    key_env = os.environ.get("SSH_KEY", "").strip()
    if key_env:
        p = Path(key_env).expanduser()
        if p.is_file():
            return p
    if pubkey_hint is not None:
        priv = Path(str(pubkey_hint).removesuffix(".pub"))
        if priv.is_file():
            return priv
    for candidate in (Path.home() / ".ssh" / "id_ed25519", Path.home() / ".ssh" / "id_rsa"):
        if candidate.is_file():
            return candidate
    return None


def collect_pubkey_material(pubkey_paths: list[Path] | None) -> str:
    if pubkey_paths:
        lines: list[str] = []
        for path in pubkey_paths:
            p = path.expanduser()
            if not p.is_file():
                raise FileNotFoundError(f"Public key not found: {p}")
            for line in p.read_text().splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    lines.append(line)
        if not lines:
            raise ValueError("No key material in the given --pubkey file(s).")
        return "\n".join(lines) + "\n"

    pub_dir = Path.home() / ".ssh"
    blobs = sorted(pub_dir.glob("*.pub"))
    if not blobs:
        raise FileNotFoundError(f"No public keys in {pub_dir}/*.pub")
    lines = []
    for p in blobs:
        for line in p.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                lines.append(line)
    if not lines:
        raise ValueError(f"No key material in {pub_dir}/*.pub")
    return "\n".join(lines) + "\n"


def _ssh_run(
    host: str,
    remote_script: str,
    *,
    identity: Path | None,
    remote_env: dict[str, str] | None = None,
) -> str:
    env_prefix = ""
    if remote_env:
        parts = []
        for k, v in remote_env.items():
            safe = v.replace("'", "'\"'\"'")
            parts.append(f"{k}='{safe}'")
        env_prefix = " ".join(parts) + " "

    cmd = [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=accept-new",
        "-o",
        "IdentitiesOnly=yes",
    ]
    if identity is not None:
        cmd.extend(["-i", str(identity)])
    cmd.extend([host, f"{env_prefix}bash -s"])

    proc = subprocess.run(
        cmd,
        input=remote_script,
        text=True,
        capture_output=True,
    )
    if proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip() or f"exit {proc.returncode}"
        raise SSHError(detail)
    return proc.stdout


def list_parts(host: str, *, identity: Path | None) -> str:
    return _ssh_run(host, LIST_PARTS_REMOTE, identity=identity)


def install_keys(
    host: str,
    root_part: str,
    pubkeys: str,
    *,
    identity: Path | None,
    target_user: str = "root",
    append: bool = False,
    grant_sudo: bool = True,
    permit_root_login: bool = True,
) -> str:
    keys_b64 = base64.b64encode(pubkeys.encode()).decode()
    common = {
        "ROOT_PART": root_part,
        "KEYS_B64": keys_b64,
        "APPEND_KEYS": "true" if append else "false",
    }

    if target_user == "root":
        env = {
            **common,
            "PERMIT_ROOT_LOGIN": "true" if permit_root_login else "false",
        }
        return _ssh_run(host, ROOT_REMOTE_SCRIPT, identity=identity, remote_env=env)

    env = {
        **common,
        "NEW_USER": target_user,
        "GRANT_SUDO": "true" if grant_sudo else "false",
    }
    return _ssh_run(host, USER_REMOTE_SCRIPT, identity=identity, remote_env=env)
