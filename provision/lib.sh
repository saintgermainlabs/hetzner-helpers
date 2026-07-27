#!/usr/bin/env bash
#
# Shared helpers for provision/* scripts. Sourced, not executed.
# See docs/recovery.md for the full lost-key workflow.
#
set -euo pipefail

provision_require_host() {
    local host="${1:-}"
    if [[ -z "$host" ]]; then
        echo "RESCUE_HOST is required (e.g. root@203.0.113.10) or pass it as an argument." >&2
        echo "Run recover first: hetzner-rescue recover <server-number>" >&2
        return 1
    fi
}

provision_ssh() {
    local host="$1"
    shift
    local ssh_opts=(-o StrictHostKeyChecking=accept-new -o IdentitiesOnly=yes)
    if [[ -n "${SSH_KEY:-}" ]]; then
        ssh_opts+=(-i "$SSH_KEY")
    fi
    ssh "${ssh_opts[@]}" "$host" "$@"
}

# Print lsblk + mdstat on the rescue host so the operator can pick ROOT_PART.
provision_list_parts() {
    local host="$1"
    echo "=== Block devices on $host ==="
    provision_ssh "$host" 'lsblk -o NAME,SIZE,FSTYPE,TYPE,MOUNTPOINT,LABEL,UUID; echo; echo "=== /proc/mdstat ==="; cat /proc/mdstat 2>/dev/null || true; echo; echo "=== Candidates (ext4/xfs, not already mounted as /) ==="; lsblk -nrpo NAME,FSTYPE,TYPE | awk '\''$2 ~ /^(ext4|xfs|btrfs)$/ && $3 ~ /^(part|lvm|raid)/ { print $1, $2, $3 }'\'''
    echo
    echo "Set ROOT_PART to the installed-system root device (often /dev/md2 or /dev/sda1),"
    echo "then re-run the provision script."
}

# Mount installed root at /mnt with bind mounts for chroot. Runs remotely via env.
# Expects ROOT_PART to be set in the remote environment.
provision_remote_mount_snippet() {
    cat <<'REMOTE'
set -euo pipefail
: "${ROOT_PART:?ROOT_PART must be set}"

if ! mountpoint -q /mnt; then
    mount "$ROOT_PART" /mnt
fi
for d in dev proc sys; do
    mountpoint -q "/mnt/$d" || mount --bind "/$d" "/mnt/$d"
done
REMOTE
}

collect_local_pubkeys() {
    local pubkeys
    pubkeys="$(cat "$HOME"/.ssh/*.pub 2>/dev/null || true)"
    if [[ -z "${pubkeys//[$'\n\t ']/}" ]]; then
        echo "No public keys found in $HOME/.ssh/*.pub — nothing to install." >&2
        return 1
    fi
    printf '%s\n' "$pubkeys"
}
