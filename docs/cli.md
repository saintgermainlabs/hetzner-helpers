# CLI reference

Entry point after `pip install -e .`:

```bash
hetzner-rescue <command> …
```

From a checkout without installing:

```bash
PYTHONPATH=. python3 -m hetzner_helpers <command> …
```

Credentials come from the environment or a `.env` file (`ROBOT_USER`, `ROBOT_PASS`). Use `--env-file PATH` to point at a specific file.

Optional defaults: `SERVER_NUMBER`, `SERVER_IP`, `SSH_PUBKEY`.

---

## `check`

Verify webservice credentials with a read-only server list.

```bash
hetzner-rescue check
```

## `servers` / `server`

```bash
hetzner-rescue servers
hetzner-rescue servers 12345
hetzner-rescue server 12345
```

Shows server number, IP, product, datacenter, status, and whether rescue/reset are available.

## `keys`

### `keys list`

```bash
hetzner-rescue keys list
```

### `keys add`

```bash
hetzner-rescue keys add ~/.ssh/id_ed25519.pub
hetzner-rescue keys add ~/.ssh/id_ed25519.pub --key-name laptop
```

### `keys delete`

```bash
hetzner-rescue keys delete aa:bb:cc:… --yes
```

## `rescue`

### `rescue status` / `rescue last`

```bash
hetzner-rescue rescue status 12345
hetzner-rescue rescue last 12345
```

### `rescue activate`

```bash
hetzner-rescue rescue activate 12345 --pubkey ~/.ssh/id_ed25519.pub --yes
hetzner-rescue rescue activate 12345 --ssh-key aa:bb:… --reset --wait --yes
hetzner-rescue rescue activate 12345 --yes   # password auth; prints password
```

Flags: `--ssh-key`, `--pubkey`, `--key-name`, `--server-ip`, `--reset`, `--reset-type {sw,hw,man}`, `--wait`, `--timeout`, `--yes`.

### `rescue deactivate`

```bash
hetzner-rescue rescue deactivate 12345 --yes
```

## `reset`

```bash
hetzner-rescue reset 12345 --reset-type hw --yes
```

`SERVER_NUMBER` in `.env` may omit the positional argument.

## `recover`

One-shot lost-key flow: ensure key registered → activate rescue → reset → wait for SSH → print next steps.

```bash
hetzner-rescue recover 12345 --yes
hetzner-rescue recover 12345 --pubkey ~/.ssh/id_ed25519.pub --timeout 600 --yes
hetzner-rescue recover --yes   # uses SERVER_NUMBER from .env
```

Flags: `--ssh-key`, `--pubkey`, `--key-name`, `--server-ip`, `--reset-type`, `--timeout`, `--no-wait`, `--yes`.

## `reboot-to-disk`

Deactivate rescue (if active) and reset so the installed OS boots.

```bash
hetzner-rescue reboot-to-disk 12345 --yes
hetzner-rescue reboot-to-disk 12345 --wait --yes
```

Also available as `./provision/reboot-to-disk 12345 --yes`.

---

## `install-keys`

SSH into **rescue** and write public keys onto the **installed** system (mounted at `/mnt`). Uses `IdentitiesOnly=yes` so ssh-agent does not cause “too many authentication failures”.

```bash
hetzner-rescue install-keys --host 203.0.113.10 --list-parts --identity ~/.ssh/id_ed25519
hetzner-rescue install-keys 12345 --root-part /dev/md2 --identity ~/.ssh/id_ed25519 --yes
hetzner-rescue install-keys --host root@203.0.113.10 --root-part /dev/md2 \
  --pubkey ~/.ssh/id_ed25519.pub --yes
hetzner-rescue install-keys --host 203.0.113.10 --root-part /dev/md2 --user alice --yes
```

| Flag | Meaning |
| --- | --- |
| `--host` | Rescue target (`IP` or `root@IP`) |
| `--list-parts` | Show `lsblk` / RAID candidates only |
| `--root-part` | Installed root device (or `ROOT_PART` in `.env`) |
| `--identity` | Private key for rescue SSH (or `SSH_KEY`) |
| `--pubkey` | Public key file(s); default all `~/.ssh/*.pub` |
| `--user` | `root` or a user to create/update |
| `--append` | Append to `authorized_keys` instead of replace |
| `--no-grant-sudo` | Skip NOPASSWD sudo for non-root users |
| `--no-permit-root-login` | Skip `PermitRootLogin` tweak for root |
| `--yes` | Skip confirmation |

---

## Provision scripts

These SSH into an **already running** rescue system. They do not call the Robot API (except `reboot-to-disk`).

| Script | Role |
| --- | --- |
| `provision/root-keys` | Install local `~/.ssh/*.pub` as root `authorized_keys` on disk |
| `provision/users` | Create a user + keys (+ optional sudo / permit-root-login) |
| `provision/reboot-to-disk` | Wrapper for `hetzner-rescue reboot-to-disk` |

Common environment:

| Variable | Meaning |
| --- | --- |
| `RESCUE_HOST` | SSH target, e.g. `root@203.0.113.10` |
| `ROOT_PART` | Block device of the installed root (e.g. `/dev/md2`) |
| `SSH_KEY` | Private key for rescue SSH (optional if agent/default works) |
| `GRANT_SUDO` | `users` only — default `true` |
| `PERMIT_ROOT_LOGIN` | `root-keys` only — default `true` |

```bash
RESCUE_HOST=root@IP ./provision/root-keys --list-parts
RESCUE_HOST=root@IP ROOT_PART=/dev/md2 ./provision/root-keys
RESCUE_HOST=root@IP ROOT_PART=/dev/md2 ./provision/users alice
RESCUE_HOST=root@IP ROOT_PART=/dev/md2 ./provision/users --permit-root-login
```
