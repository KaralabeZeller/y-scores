# Y-Scores

A portable Y-Sports scoreboard: three P2.5 64x64 RGB panels, a Raspberry Pi 5,
with Y-Sports management assignments and a local web page for setup and backup control.

## Supported device

- Raspberry Pi **5**, current **64-bit Raspberry Pi OS (Python 3.13+)**.
- Working `/dev/pio0` kernel device. The installer checks this before changing anything.
- Adafruit Triple LED Matrix Bonnet / Active-3-compatible wiring and **three 64x64**
  HUB75 panels on independent ports or daisy-chained from port 1, arranged
  horizontally (192x64 logical pixels).
- Separate suitable 5 V power for the panels. HUB75 ribbon cables carry data, not
  the panels' main power. The Pi uses its own supply; signal grounds are shared.
- Network access to Y-Sports and the package repositories during installation.

Pi 3/4 and other geometries require a different driver/configuration;
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
If `.local` does not resolve, use the Pi's LAN IP. Setup and the idle logo alternate
the `.local` hostname and numeric admin addresses with the actual configured port.
Addresses use larger bold text, wrapping long names without cutting them off, and
refresh after a network change. Network in the unlocked admin also lists numeric addresses. Active match and
manual displays keep their scoreboard layout. A fresh device shows the Y-Sports
logo at 8% intensity until assigned. Each device gets its own PIN and settings.

Use a unique hostname such as `y-scores-5ea0` for each scoreboard. The app reads
the OS hostname; `pantracker` is not required. On the Pi, rename it with
`sudo raspi-config nonint do_hostname y-scores-5ea0`, then reboot. This is an
operator OS change; application updates and the installer do not rename devices.

The installer installs/enables Avahi for optional `.local` discovery. Client mDNS
support and local multicast forwarding are still required. Recovery hotspot isolation
intentionally blocks mDNS; use `http://192.168.4.1:PORT/` there. On normal Wi-Fi,
try the displayed numeric URL on another device first: if it works but `.local`
does not, check mDNS/client settings; if neither works, check guest Wi-Fi isolation,
VLANs or a client VPN. Sharing an SSID does not guarantee clients can reach each other.
`Y_SCORES_PORT` in `/etc/y-scores.env` is shared by the admin, network helper and updater;
the configured port must be free on the Pi, regardless of ports used on the client.

## Display control

The local admin has separate **Display**, **Manual controls**, **Network**,
**Device**, **Y-Sports account** and **Software** pages. Choose System, Light or
Dark appearance in the header; appearance and language are saved in this browser
and do not modify display brightness or device configuration. First-time setup
guides the operator through Network → Device → optional account pairing, then
opens Display. Saving a name is required before the account step or completion.
If a new Wi-Fi connection needs confirmation, the Network page opens automatically.
Recovery hotspot access details are collapsed until the operator opens them.

Assign a match, follow a court or show a scoped schedule from Y-Sports management.
The local admin shows that assignment read-only; it has no platform match/court
pickers or match UUID fields. The local `/api/config` rejects cloud selection
modes and IDs, and catalog endpoints are removed. The device fetches central
assignments and content through its authenticated outbound connection.

Local backup modes are **Manual**, **Logo** and **Blank**. Selecting and saving one
takes local control. Reconnection never interrupts this choice; explicitly choose
**Return to platform control** when ready. Returning pauses the local manual clock.
Saving brightness or timezone alone does not take control, replace an assignment
or pause manual play. A new device defaults to the logo.

Saved legacy Match/Court/Schedule configurations remain on disk for rollback,
but the new runtime does not browse or resume them. It waits on the logo for a
fresh central assignment; identity, PIN, network configuration and manual state
are unchanged. Manual/Logo/Blank takeover persists across restart.

Unpaired, revoked, disabled or disconnected devices cannot display platform data.
Platform authorization expires after at most 35 seconds without successful status.
Manual backup remains independent, including on the recovery hotspot.

### Local handball control

Unlock with the existing device PIN, open **Display**, choose **Manual**, then
**Save display settings**. Open **Manual controls** and use **Basic setup** to
select Handball, name both teams, choose period duration
and count, count up/down, timeouts allowed per team per match, and timeout/penalty
duration. Defaults are two 30-minute periods, three timeouts per team, 60-second
timeouts and 120-second penalties. Saving setup preserves an existing score;
**Reset match** clears goals, penalties, timeout use and the clock, keeping setup.

**Match controls** offers goals +/−, start/pause, next period, team timeouts and
numbered player penalties. Penalties count down only during playing time and
carry across periods. A timeout pauses the clock and consumes one allowance;
expiry or End timeout leaves the clock paused until Start. Undo timeout count
corrects the allowance; End timeout separately ends an active countdown.
Remove a penalty with its × button. In Match controls, **Display time (mm:ss)**
sets the exact time shown on the board, in either count-up or countdown mode,
within the configured period length. Pause and end any timeout before editing.
Clock corrections do not change already-served penalty time. **Reset clock**
returns to the start of the current period while keeping goals, penalties and
timeout counts. **Reset match** clears match state while keeping its setup.
Both reset actions open a confirmation dialog with Cancel as the initial focus.
Pause and end any timeout before changing periods, setup or resetting.

State is private and persistent in `/var/lib/y-scores/manual-match.json`.
Concurrent controllers must use the current revision, so a retried goal cannot
be applied twice. Leaving Manual pauses its clock and ends an active countdown.
A service restart/reboot pauses play at the last checkpoint, at most five
seconds earlier; it preserves scores, timeout use and remaining penalties and
clears an active timeout countdown. Review the clock before resuming after an
outage. Normal updates retain this state. Before rolling back to a release
without Manual support, apply Logo first; the older release will retain the
manual-match file but cannot operate it. Rolling back also restores that
release's older platform-access behavior.

The page includes a live preview, intensity control and schedule timezone. Settings
are saved when you choose **Save display settings**. Names, scores and timeout markers use
team primary colours. For dark colours (weighted RGB brightness below 60/255), names
and used timeout markers turn white, while scores keep their team colour with a
one-pixel white outline. Team colour data is unchanged. Three centred 6-by-2-pixel
timeout markers appear without a TO label, with two blank pixel rows before the
penalty area. Up to three penalty rows remain visible, cycling when there are more.
Each penalty row aligns the player number to the left and the timer to the right
of its team's panel.
The main clock uses 20-pixel-tall digits in a 60-by-26-pixel text area, keeping
its top position at row 14. The period label starts at row 40 below it. Long clock
values are fitted to the same width within the centre panel.
Both penalty counters use the same yellow RGB value; physical
colour consistency still depends on the panels and pin mapping. Intensity is not a
hardware current limiter.

Platform court resolution happens on the server using typed scope and an operating
window. The Pi receives the effective match. Match snapshots poll at 200 ms;
network latency may cause small clock corrections. Timers freeze and show OFFLINE
after 15 seconds without a fresh snapshot. Driver refresh is measured independently
of the approximately 20 Hz content loop. The Pi never writes match data.
Each frame uses one match snapshot and one sampled age for its clock, penalties
and timeout countdown. Score and penalty changes appear together as soon as their
snapshot arrives; timers keep their own event anchors without waiting for a shared
whole-second tick. Manual mode also samples its clock once for each view.

## Device configuration and operation

| Location | Purpose |
|---|---|
| `/opt/y-scores/releases/` | Versioned code and isolated Python environments |
| `/opt/y-scores/current` | Active release symlink |
| `/opt/y-scores/previous` | Previous successful release |
| `/var/lib/y-scores/device-config.json` | Saved backup mode, intensity/timezone and preserved legacy selection |
| `/var/lib/y-scores/admin-pin.txt` | Device PIN; readable only by the service user/root |
| `/var/lib/y-scores/manual-match.json` | Private local handball setup, scores and clock checkpoints |
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

### Logo repeatedly returns / platform appears offline

First distinguish a device reboot from a platform reconnect:

```sh
uptime
systemctl show y-scores -p NRestarts -p ExecMainStartTimestamp
sudo journalctl -u y-scores -n 60 --no-pager
curl -fsS http://127.0.0.1:8080/healthz
```

If uptime and the service start time remain stable, inspect the connection error
in the unlocked local admin. A heartbeat conflict can leave older clients cycling
between the assigned display and the fallback logo. Restarting `y-scores` once
opens a fresh telemetry session; install the reconnect fix to prevent recurrence.
The fixed client retains increasing report sequences when reopening the same
session and only sends an applied acknowledgement consistent with its current
display report. It still falls back to the logo when platform access is lost.
Use the normal installer and rollback procedure; preserve device identity, PIN,
settings and `/etc/y-scores.env`. Do not unpair or delete state for this symptom.

## Daisy-chain wiring

Power off the Pi and panel supply before changing ribbon connections. Connect
bonnet **port 1 → left panel IN → left OUT → centre IN → centre OUT → right IN**.
Leave bonnet ports 2/3 disconnected. Each panel still needs its own 5 V power feed;
HUB75 chaining does not distribute the panels' main power.

Set these values in `/etc/y-scores.env` and restart `y-scores.service`:

```sh
Y_SCORES_TOPOLOGY=chain
Y_SCORES_ORDER=2,1,0
Y_SCORES_ROTATE=180,180,180
```

These values correct the observed prototype chain: transmitted tiles appear in reverse physical order, and all three panels require 180-degree rotation. Order and rotation entries
refer to positions along the chain, starting at port 1. Parallel mode remains the
default for existing installations; its entries refer to bonnet ports instead.
The driver uses a 192x64 framebuffer and two RGB lanes in chain mode; parallel
uses a 64x192 framebuffer and six lanes. The logical preview remains 192x64.
Chaining offers less refresh headroom; verify flicker on the actual panels.

Both entry points accept `--topology chain --order 2,1,0 --rotate 180,180,180`.
To return to the original wiring, power down and restore the three separate ports,
then set topology `parallel`, order `2,1,0`, rotations `180,0,0`. Software rollback
alone does not restore wiring or environment settings; do both deliberately.

## Updates and rollback

Revoked account credentials cannot generate a pairing PIN. For account changes
use **Unpair device** in Y-Sports; **Revoke credential** is for lost/compromised
credentials and needs the recovery procedure below.

### Recover a revoked credential

1. An administrator opens **Management → All devices → the revoked device →
   Generate recovery code**. The twelve-digit code is bound to that device and
   valid for ten minutes; generating another invalidates the earlier code.
2. Unlock this Pi's local admin with its existing six-digit admin PIN and enter
   the code under **Recover a revoked credential**. Recovery replaces the
   platform secret and clears ownership, while preserving the locked device ID,
   name, local admin PIN, display/manual state, networking and audit history.
3. Choose **Generate pairing PIN**, then enter the new eight-digit PIN in
   Y-Sports **My devices → Add device** for the intended account.

The revoked secret stays invalid. A code alone is not the new platform secret;
the Pi generates a fresh random secret privately, saves a pending candidate
before transmission, and commits it only after server confirmation. If a
response is lost or the Pi restarts, it tests the candidate with an authenticated
status request before retrying. Neither persistent secret is returned to the
browser, and the recovery code is not saved on the Pi. A failed code preserves
the old identity and pending candidate for a safe retry. No reset by name/UUID
and no automatic reactivation of revoked credentials is supported.

This requires the Y-Sports backend/management recovery release (Flyway V58) as
well as the Pi update. Updating only the Pi cannot authorize recovery on an older
server. Keep both secrets' protected state during a rollback with recovery pending;
older software cannot complete the pending operation.

### Software updates

Download and extract a newer release into a fresh folder, then run its installer.
For a clean clone, `git pull --ff-only` followed by `sudo bash scripts/install.sh`
is also supported. Settings and the PIN remain in `/var/lib/y-scores`; an update
builds its environment before stopping the display. Previous releases are retained.

```sh
sudo bash scripts/rollback.sh
```

Rollback switches to the previous code/environment and checks readiness; it does not
rewind device settings. Compatible helper releases restart the helper after a
successful rollback; rolling back before the setup bridge disables the helper
while retaining network profiles/credentials. Re-enable it through a bridge
installer when returning to a compatible release. Back up `/var/lib/y-scores` and `/etc/y-scores.env` separately
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

## First-time setup and account pairing

New devices show paged local setup instructions and their unique six-digit admin
PIN on the physical panels until setup is completed. Open the local hostname/IP,
unlock, save a device name, optionally generate an eight-digit pairing PIN and
enter it in **My devices** in Y-Sports management. Then finish setup and choose
local display settings. There are no QR codes or public discovery directory.
The pairing PIN is temporary and separate from the local admin PIN. The first
signed-in account redeeming it becomes the owner. Restart or a lost claim response
requires a new PIN; the device secret is never sent to the local browser.

The advanced UUID field is editable until the first identity save, then locked.
Renaming changes the metadata revision. The protected, versioned
`/var/lib/y-scores/device-identity.json` stores identity and the random device
credential independently of the legacy display config. Bootstrap retries reuse
that credential. Never delete it to work around a registration conflict: contact
a platform administrator for assisted recovery. Registration and ownership are
separate. A bootstrap conflict stops this worker's outbound registration retries
until restart and reports operator recovery; a temporary connection failure still
retries with the same identity and credential. Do not repeatedly restart a board
to work around a conflict.
Existing devices keep their display settings and PIN and skip initial
credential disclosure; save a name in the setup panel before linking an account.

The account connection switch enables registration, pairing/status polling and
heartbeats. Display control remains **local** in this release. Turning the switch
off first cancels a pending claim; it keeps ownership. Server errors are reported
without secrets. HTTPS is required unless explicitly enabling local development
with `Y_SCORES_ALLOW_HTTP_PLATFORM=1`. Configure the trusted API base in
`Y_SCORES_PLATFORM_API`; credential-bearing requests never follow redirects.

## Optional protected network recovery (Pi validation required)

Ethernet and Wi-Fi provisioned using Raspberry Pi Imager work without the helper.
The narrow root helper is opt-in and has not yet been validated on a physical Pi.
Before enabling it, confirm NetworkManager manages `wlan0`, `nmcli`, `busctl`,
`iw` and `nft` are installed, the radio supports WPA AP mode, and checkpoint
create/rollback work on your supported Pi OS. Set `Y_SCORES_WIFI_COUNTRY` to the
actual two-letter country in the root-owned `/etc/y-scores.env`. The installer and
helper verify that the regulatory setting was accepted before opening an AP.

```sh
sudo bash scripts/install.sh --enable-network-helper
```

After 90 seconds without an Ethernet/Wi-Fi link, it opens a unique password-
protected `Y-Scores-Setup-XXXX` network at `http://192.168.4.1:8080/` (or the
configured admin port). New recovery passwords contain ten random uppercase
letters/digits in two groups of five separated by a hyphen, excluding confusing
characters. The hyphen is part of the password. The panels show the key in a larger,
bold font; existing longer passwords appear across consecutive lines, joined without
spaces. The alternate page shows the recovery admin address. Existing credentials
are preserved, including their letter case. The password and SSID survive updates in the root-only
`/var/lib/y-scores-network/network-state.json`. nftables allows DHCP and the local
admin page and blocks SSH, IPv6 and forwarding from/to the recovery interface;
clients cannot use the AP to reach the venue LAN. The network password grants
network access; the local admin PIN still protects settings. Cloud outages alone
never open the hotspot. Use Ethernet if recovery hardware is unavailable.

Wi-Fi changes create a new NetworkManager profile and checkpoint while retaining
saved profiles. Passwords are supplied through mode-0600 keyfiles, never process
arguments/logs. Only WPA passphrase networks are supported in this slice. Hidden
SSID entry is supported; enterprise/open networks require provisioning outside
this UI. After switching, join the new network, reopen the local address, unlock
if needed and **Confirm this network works** within 75 seconds (less if connection
setup exhausted the checkpoint window). Confirmation
checks the candidate Wi-Fi UUID and IPv4 address; Ethernet alone cannot pass it.
An unconfirmed/failed change rolls back and deletes only its trial profile.
NetworkManager has an independent 120-second checkpoint timeout if the helper
dies; the protected journal removes interrupted trials on helper restart/reboot.
The AP retries saved networks after fifteen minutes without an authenticated
local admin action, then reopens after the grace period if needed. Status polling
and unauthenticated clients do not extend this idle period. **Open recovery
hotspot** is a local PIN-protected action. Isolation stays enabled throughout
Wi-Fi checkpoint testing, including helper failure and NetworkManager's automatic
rollback to the AP. Only station DHCP and local admin remain available during
that window; normal station networking resumes after explicit confirmation.

First-run panel pages display hotspot credentials when recovery is active.
After setup, active recovery still shows the hotspot password/URL on the panels,
but never the saved admin PIN. The recovery password/URL is also available to the
PIN-protected admin UI; retain a per-unit label as a backup. Verifying the physical
recovery journey is a hardware-pilot gate.

The helper uses a root-owned service and a local Unix socket restricted by file
permissions and peer UID to `yscores`; the app has no sudo/arbitrary-shell access.
No inbound cloud access to the Pi is needed. The local web server validates its
hostname/port and Origin, and session CSRF tokens protect mutations. If using a
custom local hostname, explicitly add it to `Y_SCORES_ALLOWED_HOSTS`.

Back up `/var/lib/y-scores`, `/var/lib/y-scores-network`,
`/etc/NetworkManager/system-connections` and `/etc/y-scores.env` privately. Never
clone a provisioned SD card as a factory image: credentials must be unique.

## Platform integration follow-ups

See [the implementation handover](docs/platform-integration-handover.md) for device
identity, account pairing, platform assignments, management UI and backend/API work.
Identity, pairing, health telemetry, authorized cloud assignments and scoped
match/schedule feeds are implemented. The backend resolves automatic courts.
Signed remote software updates remain a future slice.

## Refresh tuning

The prototype chain uses `Y_SCORES_COLOR_PLANES=6` and
`Y_SCORES_TEMPORAL_PLANES=1` in `/etc/y-scores.env`: about 69 Hz measured by the
driver, versus 44.6 Hz with the original 10/2 settings. This favours steady refresh
over colour precision, without temporal dithering. Check actual team colours and
dim indicators on the hardware; this is not a measured universal panel maximum.
8/4 measured about 83 Hz, but distributes low colour bits across multiple refreshes
and can cause brightness variation. Existing installations default to 10/2 if these
variables are absent. Restart the service after changing settings.


## Match Center device health and remote selection

The `display-health-v1` and `remote-selection-v1` capabilities enable the
Match Center Device tab. Authenticated outbound heartbeats report bounded
health observations every 15 seconds. The server opens a boot-bound telemetry
session; increasing report sequences reject old or reordered reports. No inbound
Pi control port or SSH credential is required for remote match selection.

The backend resolves fixed matches and typed court assignments. It returns an
effective match plus a separate control revision. The Pi acknowledges a revision
only after rendering that match (or the requested logo). Heartbeat visibility does
not prove that the LEDs, power or cabling are physically working. Frame age and
successful hardware-output age are distinct, and simulator output is explicit.
Unsupported CPU, system uptime and throttling probes remain unavailable. Health
reports exclude PINs, device secrets, network names, private addresses and logs.

Applying local Manual, Logo or Blank takes local control and cannot be interrupted
by a cloud assignment. Match/Court/Schedule selection is performed only in Y-Sports management. Legacy
selection settings are retained for rollback but no longer resume locally. Select **Return to platform
control** in the PIN-protected local admin to allow the next authenticated server
selection. The default logo on a new, unconfigured device does not block its first
assignment. Existing saved Manual/Logo/Blank settings migrate conservatively to
local takeover. Accepted control revision and takeover are kept separately in
`/var/lib/y-scores/device-control.json`; protect and back up this file along with
identity, PIN, display settings and manual-match state. Reboot does not resume a
cloud selection until a fresh authenticated server response authorizes it.

Upgrade the Y-Sports backend/management first, then the Pi with the existing
installer. Preserve all of `/var/lib/y-scores` and `/etc/y-scores.env`. An older
backend without telemetry sessions retains basic registration and pairing; newer
health and remote-selection features remain unavailable until it is upgraded.
The ordinary rollback script selects the previous release without removing new
state; older software ignores the extra control file. No network helper privileges
or configuration are changed by this feature.


Remote assignments fetch private match metadata, snapshots and operation events
through the scoped device-authenticated API. Each read rechecks the current desired
match and paired credential. Credentials remain bound to the configured HTTPS
platform origin and redirects are rejected. The standalone legacy public-match CLI keeps its anonymous transport; the
admin service uses authenticated platform content only. Capability changes on existing installations persist
a new metadata revision before transmission, so a lost upgrade response retries
the same revision without changing identity or device credentials.


### Remote schedule support

`remote-schedule-v1` adds an authenticated `GET /scoreboard-device/schedule`
feed. The server binds it to the effective typed scope, optional court and explicit
operating window, returns at most 100 fixtures, and supplies the control revision.
The Pi caps this response at 256 KiB, refreshes every five seconds, and accepts only
the current revision. It acknowledges SCHEDULE only after a fresh schedule frame
and successful physical output submission (simulator output remains explicit).
Schedule frames never infer a selected or rendered platform match from team names.
Schedule feed age is reported separately from frame and output age. Fetch failure
or data older than 15 seconds falls back to logo with unhealthy feed status. A
session, ownership or assignment change clears cached private fixtures. Temporary
MATCH overrides and Return to assignment preserve the server's base schedule.
Upgrade backend/management before installing this capability on the Pi.
# Verified remote application updates

Remote updating is an optional bootstrap component. Install it from a trusted
checkout on Raspberry Pi 5, aarch64, Raspberry Pi OS Trixie and Python 3.13:

```bash
sudo bash scripts/install.sh --enable-updater --trust-root /protected/root.json
```

The public root must come from the operator's reviewed TUF signing ceremony.
The installer checks its self-signatures, copies it once, and does not silently
replace an existing root. Signing private keys never belong on the Pi. Updates
remain unavailable until this real trust root, signed catalog and backend update
configuration are provisioned. The bootstrap installer can install dependencies;
remote jobs never invoke it, apt, a network pip install or release shell scripts.

Owners request approved Stable versions in Y-Sports; administrators manage
approvals, interruption and compatible downgrade. The Pi verifies signed TUF
metadata, expiration and rollback protections with `tuf==7.0.1`, and verifies
the exact archive length, hash, file manifest, platform and state format before
building an offline environment from hash-pinned binary wheels. Authenticated
downloads use only the configured Y-Sports proxy and never follow redirects or
send device credentials to GitHub. Archive links, devices, unsafe paths,
duplicates and oversized contents are rejected; free space is checked before
extraction. The persistent installed sequence prevents silent downgrade.

The updater runs independently from `/opt/y-scores/updater/`. The root network
helper also runs fixed bootstrap code in `/opt/y-scores/network-helper/`, so
application releases cannot replace either privileged service. The updater has
only `CAP_DAC_READ_SEARCH` to read the existing private device identity and PIN;
state permissions remain 0700/0600. Candidate renderer checks run as `yscores`
through systemd. Application activation and operator installers share
`/run/lock/y-scores-install.lock`.

Local admin shows update state and provides 1-hour/24-hour deferral and resume.
Manual, Logo and Blank takeover protects an intentional local event even when
the clock is paused. Active or unknown match state waits for idle; explicit
administrator NOW permission may interrupt that match, but cannot override a
local deferral or network operation. Immediately before stopping the display,
the updater reserves local controls and rechecks the current server lease,
ownership, cancellation, approval, exact installed version and activation
deadline. A disconnected authorization cannot activate an update.

Activation stops the sole hardware writer, switches the `current` symlink and
starts the new display. Success requires a minute of consecutive fresh renderer,
hardware output, authenticated admin and valid-state evidence within 90 seconds,
and unchanged device ID, credential, admin PIN and environment. Identity,
settings, manual state and network profiles stay outside releases. The installed
version and sequence are read from `app/release.json`; development installations
use `Y_SCORES_VERSION` only as a fallback.

The fsynced journal, TUF cache and safe public status live in
`/var/lib/y-scores-updater/`. Restart after power loss during stop, switch or health
checks conservatively restores the retained previous release before considering
another cloud job. Failed readiness automatically rolls back without depending
on the new app or cloud. `scripts/rollback.sh` remains an operator fallback and
keeps the fixed network helper. A rollback failure is retained as an explicit
failure; it is never reported as successful installation. Remote jobs update the
application only: OS/kernel, privileged helpers, services and TUF bootstrap
changes require an operator installation. Keep the last-good release and do not
manually delete updater journals or trusted metadata to bypass replay checks.

## Permanent application release publication

Numeric vMAJOR.MINOR.PATCH tags on main trigger the ARM64 release workflow.
Configure immutable GitHub releases, the pi-releases/pi-catalog environments,
three online TUF role secrets and the reviewed public update-trust/root.json
first. Keep the three root private keys offline and outside the checkout.

The workflow publishes the application archive, release.json and SHA256SUMS,
then commits signed metadata to updates-catalog. Daily metadata renewal does
not rebuild application files. If catalog publication fails after immutable
assets were published, use Recover immutable release catalog publication with
the existing tag; never replace published assets. Approve Pilot/Stable in
Y-Sports Management > Devices after catalog sync.

Operator tools: scripts/provision_update_trust.py provisions new trust;
scripts/renew_update_root.py performs threshold-signed expiry renewal with
unchanged keys. Both are offline ceremonies, never automatic Pi trust changes.
See the Y-Sports docs/scoreboard-software-updates.md runbook for the complete
production bootstrap, signing custody and Pilot hardware acceptance steps.
