# Recovery guide — lost SSH keys on a Hetzner Robot server

This walkthrough restores SSH access when your private keys are gone or `authorized_keys` on the server is wrong. It uses the Robot webservice API and the Linux rescue system.

## Prerequisites

- A Hetzner **Robot** dedicated server (not Cloud Console).
- Robot **webservice** credentials (Settings → Webservice and app settings).
- The **server number** (Robot UI) and ideally the main IPv4.
- A machine that can reach the server on port 22 and call `https://robot-ws.your-server.de`.
- An SSH key pair on that machine (`ssh-keygen -t ed25519` if you need a new one).

```bash
cp .env.example .env   # set ROBOT_USER, ROBOT_PASS, optional SERVER_NUMBER
pip install -e .
hetzner-rescue check
```

## 1. Boot into rescue with your public key

```bash
hetzner-rescue recover SERVER_NUMBER --yes
# or with an explicit key:
hetzner-rescue recover SERVER_NUMBER --pubkey ~/.ssh/id_ed25519.pub --yes
```

What this does:

1. Uploads the public key to Robot key management if it is not already there.
2. Warns if the Robot firewall looks like it might block port 22.
3. Activates Linux rescue for the next boot, authorized for that key fingerprint.
4. Issues a hardware reset.
5. Polls until SSH answers on the server’s main IP.

Connect:

```bash
ssh -o StrictHostKeyChecking=accept-new root@SERVER_IP
```

If you used a non-default private key:

```bash
ssh -i ~/.ssh/your_private_key root@SERVER_IP
```

### Manual alternative

```bash
hetzner-rescue keys add ~/.ssh/id_ed25519.pub
hetzner-rescue rescue activate SERVER_NUMBER --ssh-key aa:bb:... --reset --wait --yes
```

Password-only rescue (no key): omit `--ssh-key` / `--pubkey`; the activate response prints a root password. Save it.

## 2. Find the installed root filesystem

```bash
hetzner-rescue install-keys --host SERVER_IP --list-parts \
  --identity ~/.ssh/id_ed25519
```

Or the bash helper: `RESCUE_HOST=root@SERVER_IP ./provision/root-keys --list-parts`

Typical layouts:

| Setup | Common `ROOT_PART` |
| --- | --- |
| Software RAID1 (Hetzner default) | `/dev/md2` or `/dev/md1` |
| Single disk | `/dev/sda1` / `/dev/nvme0n1p2` |
| LVM | `/dev/mapper/...` |

Confirm by mounting in rescue and checking contents:

```bash
# on the rescue host
mount /dev/md2 /mnt
ls /mnt/etc/os-release /mnt/root /mnt/home
```

Set `ROOT_PART` to that device for the next step. If the root is LVM or encrypted, assemble/unlock it in rescue before provisioning (out of scope for the helper scripts).

## 3. Inject keys into the installed system

**Root account** (simplest when you only need to get back in):

```bash
hetzner-rescue install-keys --host SERVER_IP --root-part /dev/md2 \
  --identity ~/.ssh/id_ed25519 --yes
```

Bash equivalent: `RESCUE_HOST=root@SERVER_IP ROOT_PART=/dev/md2 ./provision/root-keys`

This writes your local `~/.ssh/*.pub` into `/root/.ssh/authorized_keys` on the disk image and sets `PermitRootLogin prohibit-password` (unless `PERMIT_ROOT_LOGIN=false`).

**Named user with sudo** (closer to a normal admin account):

```bash
hetzner-rescue install-keys --host SERVER_IP --root-part /dev/md2 \
  --user alice --identity ~/.ssh/id_ed25519 --yes
```

That creates `alice` if needed, installs keys, and (by default) adds a NOPASSWD sudoers drop-in so the locked password does not block `sudo`. Tighten `/etc/sudoers.d/` after recovery.

Optional: enable password-style root login policy via:

```bash
RESCUE_HOST=root@SERVER_IP ROOT_PART=/dev/md2 ./provision/users --permit-root-login
```

`SSH_KEY` may be set to the **private** key path used to enter rescue if it is not your ssh-agent default.

## 4. Boot back to the installed OS

Rescue is one-shot for the next boot once deactivated. Clear it and reset:

```bash
hetzner-rescue reboot-to-disk SERVER_NUMBER --yes
# same thing:
./provision/reboot-to-disk SERVER_NUMBER --yes
```

Wait a few minutes for the disk OS to come up, then:

```bash
ssh root@SERVER_IP
# or: ssh alice@SERVER_IP
```

If the host key changed (new install or first connection after a long time), remove the old entry:

```bash
ssh-keygen -R SERVER_IP
```

## 5. Aftercare

- Remove temporary NOPASSWD sudo rules if you do not want them long-term.
- Confirm Robot firewall still allows your SSH source (or disable it if you rely on the host firewall only).
- Store a spare pubkey in Robot (`hetzner-rescue keys add`) so the next `recover` is faster.
- Prefer keeping an emergency key or console access plan; rescue still requires webservice credentials.

## Troubleshooting

| Symptom | What to try |
| --- | --- |
| `401 Unauthorized` | Wrong `ROBOT_USER` / `ROBOT_PASS` (webservice user, not account login). |
| SSH never comes up after recover | Check Robot firewall; try `hetzner-rescue rescue status N`; confirm IP with `hetzner-rescue server N`. |
| `Permission denied (publickey)` into rescue | Wrong private key; or rescue was activated without your fingerprint — re-run `recover`. |
| Mount fails / empty `/mnt` | Wrong `ROOT_PART`; re-run `--list-parts`; assemble RAID/LVM first. |
| Login works in rescue but not after reboot | Keys written to wrong filesystem; rescue still active — run `reboot-to-disk`; check `PermitRootLogin` / `PasswordAuthentication`. |
| Rate limit errors | Wait and retry; the client retries once on `RATE_LIMIT_EXCEEDED`. |

## Related commands

See [cli.md](cli.md) for the full subcommand list.
