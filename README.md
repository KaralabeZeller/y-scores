# Y-Scores

A portable Y-Sports scoreboard: three P2.5 64x64 RGB panels, a Raspberry Pi 5,
and a local web page for choosing what to display.

## Supported device

- Raspberry Pi **5**, current **64-bit Raspberry Pi OS (Python 3.13+)**.
- Working `/dev/pio0` kernel device. The installer checks this before changing anything.
- Adafruit Triple LED Matrix Bonnet / Active-3-compatible wiring and **three 64x64**
  HUB75 panels on independent ports, arranged horizontally (192x64 logical pixels).
- Separate suitable 5 V power for the panels. HUB75 ribbon cables carry data, not
  the panels' main power. The Pi uses its own supply; signal grounds are shared.
- Network access to Y-Sports and the package repositories during installation.

Pi 3/4, other geometries, and daisy-chain wiring require a different driver/configuration;
they are not supported by this installer. It does not install kernels or alter boot overlays.
If `/dev/pio0` is missing, update the supported Raspberry Pi OS/kernel and reboot first.
The reference device uses kernel `6.18.34+rpt-rpi-2712`, Python 3.13 and Piomatter 1.0.0.

## Install a new device

The repository is private. A release bundle lets you install without putting a
GitHub credential on the Pi.

On your laptop with GitHub access:

```sh
gh release download v0.1.0 --repo KaralabeZeller/y-scores --pattern 'y-scores-*.tar.gz'
scp y-scores-v0.1.0.tar.gz YOUR_USER@YOUR_PI.local:~/
```

On the Pi:

```sh
mkdir -p ~/y-scores
tar -xzf ~/y-scores-v0.1.0.tar.gz -C ~/y-scores --strip-components=1
cd ~/y-scores
sudo bash scripts/install.sh
```

Alternatively, authenticate Git on the Pi, clone this repo, and run the same command:

```sh
git clone https://github.com/KaralabeZeller/y-scores.git
cd y-scores
sudo bash scripts/install.sh
```

The installer builds and tests a new virtual environment, creates a dedicated
unprivileged `yscores` service user, starts the display, checks readiness and enables
startup at boot. A failed activation attempts to restore the previous running release.
It prints the local admin URL and the device's generated six-digit PIN.

Open **http://YOUR_PI.local:8080/** from a phone or computer on the same network.
If `.local` does not resolve, use the Pi's LAN IP. A fresh device shows the Y-Sports
logo at 8% intensity until assigned. Each device gets its own PIN and settings.

## Display modes

- **Match:** choose a public standalone/tournament match, or paste its match UUID.
- **Court:** choose a tournament and court; show the active or paused match, then
  the next unfinished fixture between games. Completed/cancelled matches are skipped.
- **Schedule:** rotate three fixtures per page, optionally filtered by court.
- **Y-Sports:** display the official brand mark.
- **Blank:** dark panels with the admin page still available.

The page includes a live preview, intensity control and schedule timezone. Settings
are saved when you choose **Apply to display**. Names, scores and timeout markers use
team primary colours. Both penalty counters use the same yellow RGB value; physical
colour consistency still depends on the panels and pin mapping. Intensity is not a
hardware current limiter.

Court selection requires stable court IDs in the public schedule. A standalone
match with only a free-text venue is selectable by match ID, not by court. Court
routing refreshes through a ten-second schedule cache. Match snapshots use a 200 ms
cadence over a reused connection; network latency can still cause small clock corrections.
Timers freeze and show OFFLINE after 15 seconds without a fresh accepted snapshot.
The app reads existing public Y-Sports APIs and never changes match data.

## Device configuration and operation

| Location | Purpose |
|---|---|
| `/opt/y-scores/releases/` | Versioned code and isolated Python environments |
| `/opt/y-scores/current` | Active release symlink |
| `/opt/y-scores/previous` | Previous successful release |
| `/var/lib/y-scores/device-config.json` | Saved match/court, mode, intensity, timezone |
| `/var/lib/y-scores/admin-pin.txt` | Device PIN; readable only by the service user/root |
| `/etc/y-scores.env` | Port, bind address, API URL, panel order/rotation/pinout |

The default port mapping preserves the prototype: port 1 = right rotated 180 degrees,
port 2 = centre, port 3 = left. Edit `/etc/y-scores.env` for another arrangement;
`Y_SCORES_ORDER` lists logical tiles 0/1/2 for each port and `Y_SCORES_ROTATE` lists
each port's rotation. Changes take effect on service restart. The installer leaves
this file and all saved state intact on subsequent runs.

```sh
sudo systemctl status y-scores
sudo journalctl -u y-scores -n 50 --no-pager
sudo systemctl restart y-scores
sudo cat /var/lib/y-scores/admin-pin.txt
```

The PIN protects local control, with limited login attempts and 12-hour sessions.
This HTTP admin is intended for a trusted LAN; do not expose it through router port
forwarding. Restarting the service invalidates browser sessions but preserves the PIN.

## Updates and rollback

Download and extract a newer release into a fresh folder, then run its installer.
For a clean clone, `git pull --ff-only` followed by `sudo bash scripts/install.sh`
is also supported. Settings and the PIN remain in `/var/lib/y-scores`; an update
builds its environment before stopping the display. Previous releases are retained.

```sh
sudo bash scripts/rollback.sh
```

Rollback switches to the previous code/environment and checks readiness; it does not
rewind device settings. Back up `/var/lib/y-scores` and `/etc/y-scores.env` separately
if you need a complete device backup. Never put these files in Git.

## Migrate the original prototype

On the original device only:

```sh
sudo bash scripts/install.sh --migrate-from /home/zeller/scoreboard-test
```

This copies only the saved config and PIN if the new state files do not already
exist. After building and testing, it stops `scoreboard-device.service`, starts
`y-scores.service`, and disables the old service only after a successful health
check. Original prototype files remain available. A failed first activation restarts
the old service if it was running.

## Desktop preview and tests

```sh
python -m venv .venv
# Activate .venv using your shell's activation command.
python -m pip install -r requirements.txt
python -m unittest discover -p 'test_*.py'
python device_admin.py --no-hardware --bind 127.0.0.1
```

Bundled fonts/assets mean no separate emulator checkout is needed. Desktop state
is ignored under `state/`. Raspberry Pi dependencies are in `requirements-pi.txt`.
CI runs Python tests, JavaScript syntax and shell syntax checks; physical GPIO,
colours and network timing require device testing.

## Portable stand

Each P2.5 64x64 panel is 160x160 mm; three form a 480x160 mm display before the case.
The intended enclosure has three detachable sections, the Pi and bonnet behind the
centre section, removable side sections, and a separate power supply. Each side
needs its own power and data connection. Feet/kickstands should support upright or
slightly tilted desk placement. There are **no printable enclosure CAD/STL files yet**.

See `THIRD_PARTY_NOTICES.md` for font and brand-asset provenance.
