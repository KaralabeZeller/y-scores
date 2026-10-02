#!/usr/bin/env bash
# Install/update from a trusted checkout or extracted release; no GitHub token on the Pi.
set -Eeuo pipefail
umask 022 # Release code and virtualenv must be readable by the unprivileged service.
source_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
migrate_from=''
verify_dir=''
updater_stage=''
previous_updater=''
previous_updater_unit=''
updater_was_active=false
updater_stopped=false
updater_switched=false
cleanup() {
  rc=$?
  if [[ -n $verify_dir && $verify_dir == /var/tmp/y-scores-verify.* && -d $verify_dir ]]; then
    rm -rf -- "$verify_dir"
  fi
  if [[ $rc != 0 && $updater_switched == true ]]; then
    systemctl stop y-scores-updater.service 2>/dev/null || true
    if [[ -d /opt/y-scores/updater ]]; then
      mv -T /opt/y-scores/updater "/opt/y-scores/updater.failed-$$" || true
    fi
    if [[ -n $previous_updater && -d $previous_updater ]]; then
      mv -T "$previous_updater" /opt/y-scores/updater || true
    fi
    if [[ -n $previous_updater_unit && -f $previous_updater_unit ]]; then
      install -m 644 "$previous_updater_unit" /etc/systemd/system/y-scores-updater.service || true
    fi
    systemctl daemon-reload || true
  fi
  if [[ $rc != 0 && $updater_stopped == true && $updater_was_active == true ]]; then
    systemctl start y-scores-updater.service || true
  fi
  if [[ -n $previous_updater_unit && $previous_updater_unit == /var/tmp/y-scores-unit.* ]]; then
    rm -f -- "$previous_updater_unit"
  fi
}
trap cleanup EXIT
enable_network_helper=false
enable_updater=false
trust_root=''
while (( $# )); do
  case "$1" in
    --enable-network-helper) enable_network_helper=true; shift ;;
    --enable-updater) enable_updater=true; shift ;;
    --trust-root) [[ $# -ge 2 ]] || exit 2; trust_root=$(realpath -- "$2"); shift 2 ;;
    --migrate-from) [[ $# -ge 2 ]] || exit 2; migrate_from=$(realpath -- "$2"); shift 2 ;;
    *) echo 'Usage: sudo bash scripts/install.sh [--enable-network-helper] [--migrate-from PATH] [--enable-updater --trust-root PATH]' >&2; exit 2 ;;
  esac
done
if [[ $enable_updater == true ]]; then
  [[ -n $trust_root && -f $trust_root ]] || { echo 'Supply the real operator-provisioned TUF root with --trust-root.' >&2; exit 2; }
fi
[[ $EUID == 0 ]] || { echo 'Run with sudo.' >&2; exit 1; }
[[ $(uname -m) == aarch64 && -r /proc/device-tree/model ]] || { echo 'Requires a Raspberry Pi 5 running 64-bit Raspberry Pi OS.' >&2; exit 1; }
grep -aq 'Raspberry Pi 5' /proc/device-tree/model || { echo 'This driver supports the Raspberry Pi 5 only.' >&2; exit 1; }
[[ -c /dev/pio0 ]] || { echo 'Missing /dev/pio0. Install current 64-bit Raspberry Pi OS, update OS/kernel and reboot, then retry.' >&2; exit 1; }
python3 -c 'import sys; assert sys.version_info >= (3, 13), "Python 3.13+ required (current Raspberry Pi OS)"'
# Serialize installers, including upgrades invoked by different operators.
exec 9>/run/lock/y-scores-install.lock
flock -n 9 || { echo 'Another y-scores install is running.' >&2; exit 1; }
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y python3-venv python3-dev build-essential cmake pkg-config curl git avahi-daemon
systemctl enable --now avahi-daemon.service
getent group gpio >/dev/null || groupadd --system gpio
id yscores >/dev/null 2>&1 || useradd --system --user-group --home-dir /var/lib/y-scores --shell /usr/sbin/nologin yscores
usermod -a -G gpio yscores
install -d -m 755 /opt/y-scores/releases
install -d -o yscores -g yscores -m 700 /var/lib/y-scores
if [[ $enable_network_helper == true ]]; then
  command -v nmcli >/dev/null && command -v busctl >/dev/null && command -v nft >/dev/null && command -v iw >/dev/null || { echo 'Network recovery requires NetworkManager, busctl, nftables and iw.' >&2; exit 1; }
  systemctl is-active --quiet NetworkManager || { echo 'NetworkManager must manage the Pi Wi-Fi before enabling recovery.' >&2; exit 1; }
  install -d -o root -g root -m 700 /var/lib/y-scores-network
fi
# A fresh virtualenv is built before touching the running installation.
release=/opt/y-scores/releases/$(date -u +%Y%m%dT%H%M%SZ)-$$
install -d -m 755 "$release"
for file in "$source_dir"/*.py "$source_dir"/*.html "$source_dir"/*.js "$source_dir"/requirements*.txt; do
  install -m 644 "$file" "$release/"
done
if [[ -f $source_dir/release.json ]]; then
  install -m 644 "$source_dir/release.json" "$release/release.json"
fi
cp -R "$source_dir/fonts" "$source_dir/assets" "$source_dir/scripts" "$release/"
# Extracted source can have a restrictive umask; service-readable code is intentional.
chmod -R u=rwX,go=rX "$release/fonts" "$release/assets" "$release/scripts"
python3 -m venv "$release/.venv"
"$release/.venv/bin/python" -m pip install -r "$release/requirements-pi.txt"
# Publishing/TUF tests require tools that must not enter the display runtime.
# Build a disposable verification environment, then remove it before activation.
verify_dir=$(mktemp -d /var/tmp/y-scores-verify.XXXXXX)
python3 -m venv "$verify_dir/.venv"
"$verify_dir/.venv/bin/python" -m pip install -r "$release/requirements.txt" -r "$release/requirements-release.txt"
(cd "$release" && "$verify_dir/.venv/bin/python" -m unittest discover -p 'test_*.py')
rm -rf -- "$verify_dir"
verify_dir=''
# Root-run tests cannot detect unreadable assets for the actual service account.
(cd "$release" && runuser -u yscores -- .venv/bin/python -c 'from pathlib import Path; from live_scoreboard import Renderer; from PIL import Image; Renderer(Path("fonts")); Image.open("assets/logo-white.png").verify()')
# State migration is opt-in, copies only known files, and never replaces existing state.
if [[ -n $migrate_from ]]; then
  for name in device-config.json admin-pin.txt device-identity.json; do
    if [[ -f $migrate_from/$name && ! -e /var/lib/y-scores/$name ]]; then
      install -o yscores -g yscores -m 600 "$migrate_from/$name" "/var/lib/y-scores/$name"
    fi
  done
fi
(cd "$release" && .venv/bin/python -c 'import json; from pathlib import Path; from device_model import validate_config; p=Path("/var/lib/y-scores/device-config.json"); validate_config(json.loads(p.read_text())) if p.exists() else None')
if [[ ! -e /etc/y-scores.env ]]; then
  install -m 644 "$source_dir/scripts/y-scores.env.example" /etc/y-scores.env
fi
# This environment file is administrator-owned shell syntax as well as systemd syntax.
set -a
source /etc/y-scores.env
set +a
if [[ $enable_network_helper == true ]]; then
  [[ ${Y_SCORES_WIFI_COUNTRY:-} =~ ^[A-Z]{2}$ && ${Y_SCORES_WIFI_COUNTRY:-} != 00 ]] || { echo 'Set Y_SCORES_WIFI_COUNTRY to your regulatory country in /etc/y-scores.env first.' >&2; exit 1; }
  iw reg set "$Y_SCORES_WIFI_COUNTRY"
  iw reg get | grep -q "country $Y_SCORES_WIFI_COUNTRY:" || { echo 'Wi-Fi country was not accepted.' >&2; exit 1; }
fi
previous=''
if [[ -L /opt/y-scores/current ]]; then
  previous=$(readlink -f /opt/y-scores/current)
  [[ -d $previous && $previous == /opt/y-scores/releases/* ]] || { echo 'Invalid current release link.' >&2; exit 1; }
fi
legacy_active=false
if [[ -n $migrate_from ]] && systemctl is-active --quiet scoreboard-device.service; then legacy_active=true; fi
activated=false
rollback() {
  rc=$?
  if [[ $activated == true ]]; then
    systemctl stop y-scores.service || true
    if [[ -n $previous ]]; then
      ln -sfn "$previous" /opt/y-scores/current
      systemctl start y-scores.service || true
    elif [[ $legacy_active == true ]]; then
      systemctl start scoreboard-device.service || true
    fi
    echo "Deployment failed; attempted to restore the previous service. Failed release: $release" >&2
  fi
  exit "$rc"
}
trap rollback ERR
install -m 644 "$source_dir/scripts/y-scores.service" /etc/systemd/system/y-scores.service
if [[ $enable_network_helper == true ]]; then
  install -m 644 "$source_dir/scripts/y-scores-network.service" /etc/systemd/system/y-scores-network.service
fi
# Root helpers are fixed bootstrap code, never imported from mutable releases.
if [[ $enable_network_helper == true ]] || systemctl is-enabled --quiet y-scores-network.service 2>/dev/null; then
  install -d -m 755 /opt/y-scores/network-helper
  for name in network_helper.py device_identity.py; do install -m 644 "$source_dir/$name" /opt/y-scores/network-helper/; done
  install -m 644 "$source_dir/scripts/y-scores-network.service" /etc/systemd/system/y-scores-network.service
fi
if [[ $enable_updater == true ]]; then
  python3 -c 'import sys; assert sys.version_info[:2] == (3,13), "Remote updater requires Python 3.13"'
  grep -Eq '^VERSION_CODENAME="?trixie"?$' /etc/os-release || { echo 'Remote updater requires Raspberry Pi OS Trixie.' >&2; exit 1; }
  # Prepare and verify the complete fixed environment before interrupting it.
  updater_stage=$(mktemp -d /opt/y-scores/updater.next.XXXXXX)
  chmod 755 "$updater_stage"
  install -d -m 711 /var/lib/y-scores-updater
  for name in updater.py update_core.py update_local.py platform_client.py requirements-updater.txt; do install -m 644 "$source_dir/$name" "$updater_stage/"; done
  python3 -m venv "$updater_stage/.venv"
  "$updater_stage/.venv/bin/python" -m pip install -r "$updater_stage/requirements-updater.txt"
  # Existing trust is preserved; root rotation belongs to TUF, not this installer.
  selected_root=$trust_root
  if [[ -f /opt/y-scores/updater/root.json ]]; then selected_root=/opt/y-scores/updater/root.json; fi
  "$updater_stage/.venv/bin/python" -c 'import sys; from tuf.api.metadata import Metadata; m=Metadata.from_file(sys.argv[1]); m.verify_delegate("root",m)' "$selected_root"
  install -m 644 "$selected_root" "$updater_stage/root.json"
  if [[ -f /etc/systemd/system/y-scores-updater.service ]]; then
    previous_updater_unit=$(mktemp /var/tmp/y-scores-unit.XXXXXX)
    cp /etc/systemd/system/y-scores-updater.service "$previous_updater_unit"
  fi
  if systemctl is-active --quiet y-scores-updater.service; then updater_was_active=true; fi
  if [[ $updater_was_active == true ]]; then
    systemctl stop y-scores-updater.service
  else
    systemctl stop y-scores-updater.service 2>/dev/null || true
  fi
  updater_stopped=true
  if [[ -d /opt/y-scores/updater ]]; then
    previous_updater=$(mktemp -d /opt/y-scores/updater.previous.XXXXXX)
    rmdir -- "$previous_updater"
    mv -T /opt/y-scores/updater "$previous_updater"
  fi
  updater_switched=true
  mv -T "$updater_stage" /opt/y-scores/updater
  install -m 644 "$source_dir/scripts/y-scores-updater.service" /etc/systemd/system/y-scores-updater.service
fi
systemctl daemon-reload
# Only stop the prototype when explicitly migrating from it.
if [[ $legacy_active == true ]]; then systemctl stop scoreboard-device.service; fi
activated=true
systemctl stop y-scores.service || true
ln -sfn "$release" /opt/y-scores/current.next
mv -Tf /opt/y-scores/current.next /opt/y-scores/current
systemctl start y-scores.service
healthy=false
for _ in $(seq 1 25); do
  if systemctl is-active --quiet y-scores.service && curl -fsS --max-time 2 "http://127.0.0.1:${Y_SCORES_PORT:-8080}/healthz" >/dev/null; then healthy=true; break; fi
  sleep 1
done
[[ $healthy == true ]]
systemctl enable y-scores.service
if [[ $enable_updater == true ]]; then systemctl enable --now y-scores-updater.service; fi
if [[ $enable_network_helper == true ]]; then
  systemctl enable --now y-scores-network.service
elif systemctl is-enabled --quiet y-scores-network.service 2>/dev/null; then
  # Refresh the constrained helper after a compatible application upgrade.
  systemctl restart y-scores-network.service
fi
if [[ $legacy_active == true ]]; then systemctl disable scoreboard-device.service; fi
if [[ -n $previous && $previous != "$release" ]]; then ln -sfn "$previous" /opt/y-scores/previous; fi
trap - ERR
printf '\nY-Scores is ready: http://%s.local:%s/\nAdmin PIN: ' "$(hostname)" "${Y_SCORES_PORT:-8080}"
cat /var/lib/y-scores/admin-pin.txt
printf 'Settings, PIN and device identity preserved in /var/lib/y-scores.\n'
