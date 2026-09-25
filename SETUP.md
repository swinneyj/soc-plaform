# Mac-to-Mac Setup: Tailscale Network + Shared Folder

*Last verified: Sep 25, 2026. Maintained by Dalton & Buffy (Codebuff).*

## What this is

Two MacBook Pros (Dalton's and Jay's) connected via a private [Tailscale](https://tailscale.com) mesh VPN, with a shared file drop zone. Everything runs over an encrypted WireGuard tunnel — **nothing is exposed to the public internet**.

## The live components

| Component | Detail |
|---|---|
| Tailscale daemon | Open-source `tailscaled` (Homebrew), runs as root LaunchDaemon `sh.brew.tailscale` — **auto-starts at boot** (survived a real reboot test) |
| CLI binary | `/opt/homebrew/bin/tailscale` (v1.102.4) |
| Dalton's node | `daltons-macbook-pro-1` — `100.84.93.19` |
| Jay's node | `justins-macbook-pro` — `100.88.143.23` (shared-in from his tailnet `jayswin143@gmail.com`) |
| SMB file sharing | macOS File Sharing (`smbd`, enabled system-wide) |
| Shared folder | `/Users/Shared/Exchange` — share name **Exchange**, guest access ON, permissions `2777` (group+world writable, setgid) |
| SSH server | macOS Remote Login enabled (`com.openssh.sshd`) — works for **daltonlewis** (owner key installed) |
| Tailscale SSH | `RunSSH: true` on the node, but effectively owner-only (see quirks) |

## How Jay connects (the message that worked)

> Finder → ⌘K → `smb://100.84.93.19` → Connect as **Guest** → open the **Exchange** folder.

Drag and drop, both directions. If it fails: check Tailscale is connected first — that's 90% of failures.

## How Dalton connects to Jay's Mac (reverse direction) — OPTIONAL

> **When to use this at all: rarely.** Exchange is the canonical drop zone — the everyday workflow never needs this section. Only relevant for reading Jay's files *in place* on his disk without copying them into Exchange. Note the asymmetry: Exchange lives on Dalton's disk (Jay writes consume Dalton's space; Jay can't reach Exchange when Dalton's Mac is asleep). Mounting Jay's share makes *his* files available whenever *his* Mac is up.

Jay's node: **`justins-macbook-pro`** — `100.88.143.23` (full name `justins-macbook-pro.tail3e59ce.ts.net`; the short name won't resolve across tailnets, use the IP).

**Status as of Sep 25, 2026:** SMB (445) is **already open** on his Mac — File Sharing is on. SSH (22) is closed.

**Files (works now):**
1. Finder → **⌘K** → `smb://100.88.143.23`
2. Connect as **Registered User** with *Jay's* macOS username + password (he must share them with you directly — guest was rejected when last tested)
3. Open the share(s) his Mac offers. **If you only see his Public folder:** he needs to add the folder he intends to share — System Settings → General → Sharing → File Sharing → **(i)** → **Shared Folders** → **+**. Also check **Options…** there: "Share files and folders using SMB" checked AND his user account checked beneath it (the #1 silent breaker).
4. Mounted volumes appear in Finder sidebar under **Locations**. Eject when done; re-mount anytime with ⌘K (or add to Login Items for auto-mount).

**SSH into his Mac (needs him to flip one toggle first):**
- He enables: System Settings → General → Sharing → **Remote Login**
- Password path (works immediately): `ssh <jays-macos-username>@100.88.143.23` with his password
- Key path (no password): send him YOUR public key (`cat ~/.ssh/id_ed25519.pub` — the `.pub` file only); he appends it to HIS `~/.ssh/authorized_keys`; then you connect with `ssh <his-username>@100.88.143.23` keyless. (Key generation happens on the client — yours already exists.)
- ⚠️ If he ever creates a new account for you via terminal commands, the SecureToken phantom-expiry trap applies on HIS machine too — insist on GUI-created accounts (System Settings → Users & Groups).

**Quick probes from Dalton's Mac:**
```bash
for p in 445 548 22; do nc -z -G 2 100.88.143.23 $p >/dev/null 2>&1 && echo "$p OPEN" || echo "$p closed"; done
/opt/homebrew/bin/tailscale ping justins-macbook-pro.tail3e59ce.ts.net   # expect direct, ~4-12ms (same LAN)
```

## Accounts on Dalton's Mac

| Account | Purpose | Status |
|---|---|---|
| `daltonlewis` | Owner (admin) | SSH works via `~/.ssh/authorized_keys` (ed25519 key) |
| `remoteguest` (full name "Remote Guest", pw `12345`) | Future SSH guest account, created via GUI | **Exists & token-enabled, but SSH blocked by macOS bug — see below. Will start working after an OS fix, no re-setup needed.** |
| `friend` (full name "Friend Access") | Dead leftover from headless experiments | Home dir deleted; record may still show in Users & Groups — delete via right-click whenever (GUI deletion is the only path that works) |

**Golden rule discovered today:** accounts created via **System Settings GUI** get a SecureToken automatically and work; accounts created via terminal (`sysadminctl`/`dscl`) get a broken half-initialized password record. Always use the GUI for new accounts on this Mac.

## Known macOS 26 quirks (all documented the hard way)

1. **SSH to non-owner accounts fails pre-auth.** PAM reports phantom "account has expired" (or silently closes pre-auth) for `remoteguest` despite: SecureToken enabled, valid password, no policy, no expiry attrs. Owner account works fine. This is an OS bug — retest `ssh remoteguest@100.84.93.19` after each macOS point update. Key file is already installed at `/Users/remoteguest/.ssh/authorized_keys` (owner's key), perms correct.
2. **Tailscale SSH (server) is owner-only on personal nodes.** SSH policy cannot target external shared users on untagged personal devices (`autogroup:shared` rejected; "users in dst only from same user"). Fix would require tagging the Mac (`tag:*`), which converts it to a shared device.
3. **Self-connection loopback:** dialing your *own* tailnet IP from the same Mac bypasses the tunnel (loopback short-circuit) — you can't test Tailscale SSH against yourself; use `ssh` to loopback (regular sshd) instead.
4. **Trackpad "Natural scrolling" label is inverted on this machine.** Correct state is Natural **ON** (which scrolls traditionally). Every settings key says `0`/off while the OS behaves as on — leave it ON and don't "fix" it.
5. **`sysadminctl` interactive password prompt is broken** ("Password is required!" with `-adminPassword -`) and its CLI password ops on tokenless accounts silently no-op ("Operation is not permitted without secure token unlock"). `pwpolicy`/`dscl` record-deletes hit `eDSPermissionError` even as root. GUI flows are the reliable path for anything account-related.
6. **Elevated shell in Freebuff** can't set cwd (`shell-init: getcwd` errors) — prefix commands with `cd /tmp &&` and expect exit-code quirks. Never include `sudo` in `request_elevation` commands (the tool adds privilege itself).
7. **Taildrop cannot work** between Dalton & Jay (different users/tailnets) — that's why SMB. Taildrop is same-user-only per Tailscale docs.

## Maintenance cheat-sheet

```bash
# Tailscale status / health
/opt/homebrew/bin/tailscale status
/opt/homebrew/bin/tailscale netcheck

# Restart the VPN daemon (needs admin)
sudo launchctl kickstart -k system/sh.brew.tailscale

# Verify the share is registered
sharing -l | grep -A6 Exchange

# Test guest SMB locally (no password)
mkdir -p /tmp/smbtest && mount_smbfs //guest:@localhost/Exchange /tmp/smbtest && ls /tmp/smbtest && umount /tmp/smbtest

# Test SSH to Jay (reverse direction; needs his Remote Login enabled)
ssh <jays-user>@100.88.143.23

# Who's on the tailnet admin console
open https://login.tailscale.com/admin/machines
```

## Future upgrades (when wanted, not needed)

- **Syncthing** — auto-synced folder, no mounts, no passwords. The durable evolution of the drop zone.
- **SSH for Jay** — either (a) wait for the macOS fix and his key lands in `remoteguest`, or (b) now: install his `.pub` into `~/.ssh/authorized_keys` (owner account, full access, revocable by deleting one line).
- **Cleanup** — delete the `friend` husk in Users & Groups; optional: rotate `12345` to something stronger via GUI (it's tailnet-only exposure, low risk).
- **Rotate guest SMB off** if a third party ever joins the tailnet — guest folder is writable by anyone on the tailnet.

## Fail-safe: if everything breaks

1. Reboot → all services self-start (verified)
2. If Tailscale down: `sudo launchctl kickstart -k system/sh.brew.tailscale`
3. If share missing: System Settings → General → Sharing → File Sharing on; verify `sharing -l`
4. If Jay can't connect: his Tailscale icon connected? Then `⌘K` again.
5. If Dalton can't reach Jay's share: probe ports (see reverse-direction section) — if 445 is closed, *his* File Sharing got toggled off.
