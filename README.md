# hetzner-helpers

Tools to recover a **Hetzner Robot dedicated server** when you are locked out (lost SSH keys, broken `authorized_keys`, etc.) using the [Robot Webservice API](https://robot.hetzner.com/doc/webservice/en.html) — no browser required for the reboot into rescue.

## What it does

1. **`hetzner-rescue recover`** — upload your public key to Robot (if needed), activate Linux rescue with that key, hardware-reset the server, wait until SSH answers.
2. **`provision/root-keys`** (or **`provision/users`**) — from rescue, mount the installed root filesystem and install your keys into the disk image.
3. **`hetzner-rescue reboot-to-disk`** — deactivate one-shot rescue and reset so the installed OS boots again.

## Install

```bash
cd hetzner-helpers
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
# or: pip install -r requirements.txt && PYTHONPATH=. python3 -m hetzner_helpers …
```

Copy credentials:

```bash
cp .env.example .env
# edit ROBOT_USER / ROBOT_PASS
```

Create a webservice user in Robot: **Settings → Webservice and app settings**. That user/password is separate from your account login.

## Lost SSH keys (quick path)

```bash
# 1. Boot into rescue with your local pubkey
hetzner-rescue recover 12345 --yes
#    (server number is in the Robot UI; or set SERVER_NUMBER in .env)

# 2. Find the installed root device, then inject keys for root
hetzner-rescue install-keys --host YOUR.SERVER.IP --list-parts
hetzner-rescue install-keys --host YOUR.SERVER.IP --root-part /dev/md2 \
  --identity ~/.ssh/id_ed25519 --yes

# (Bash alternative: RESCUE_HOST=root@IP ROOT_PART=/dev/md2 ./provision/root-keys)

# 3. Boot back to the installed OS
hetzner-rescue reboot-to-disk 12345 --yes

# 4. Log in
ssh root@YOUR.SERVER.IP
```

Use `./provision/users alice` instead of `root-keys` if you want a normal sudo user rather than root login.

More detail: [docs/recovery.md](docs/recovery.md). CLI reference: [docs/cli.md](docs/cli.md).

## CLI cheat sheet

| Command | Purpose |
| --- | --- |
| `hetzner-rescue check` | Verify webservice credentials |
| `hetzner-rescue servers` | List servers |
| `hetzner-rescue keys list\|add\|delete` | Manage keys in Robot |
| `hetzner-rescue rescue activate\|status\|deactivate\|last` | Rescue lifecycle |
| `hetzner-rescue reset <n>` | Hardware/software/manual reset |
| `hetzner-rescue recover <n>` | One-shot rescue + key + wait |
| `hetzner-rescue install-keys` | SSH to rescue; install keys on disk |
| `hetzner-rescue reboot-to-disk <n>` | Leave rescue, boot from disk |

Destructive actions (`recover`, `reset`, `reboot-to-disk`, key delete, rescue activate/deactivate) ask for confirmation unless you pass `--yes`.

## Safety notes

- **Hardware reset (`hw`)** power-cycles the machine. Prefer it when the OS is wedged; `sw` is a soft reboot.
- **`provision/users`** may install a **NOPASSWD** sudo drop-in so a key-only account can elevate. Tighten that after you regain access.
- **Never commit `.env`**. The webservice password is account-scoped — treat it like a root password.
- Robot rescue with an SSH key disables password login to rescue; without a key, Robot prints a one-time root password — save it.

## Layout

```
hetzner_helpers/     Python package (API client + CLI)
provision/           Bash helpers run against a live rescue SSH session
docs/                Recovery guide + CLI reference
.env.example         Credential template
```
