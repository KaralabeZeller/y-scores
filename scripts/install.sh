#!/usr/bin/env bash
# Install/update from a trusted checkout or extracted release; no GitHub token on the Pi.
set -Eeuo pipefail
source_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
migrate_from=''
if [[ ${1:-} == --migrate-from && $# == 2 ]]; then
  migrate_from=$(realpath -- "$2")
elif [[ $# != 0 ]]; then
  echo "Usage: sudo bash scripts/install.sh [--migrate-from /path/to/prototype]" >&2; exit 2
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
apt-get install -y python3-venv python3-dev build-essential cmake pkg-config curl
getent group gpio >/dev/null || groupadd --system gpio
id yscores >/dev/null 2>&1 || useradd --system --user-group --home-dir /var/lib/y-scores --shell /usr/sbin/nologin yscores
usermod -a -G gpio yscores
install -d -m 755 /opt/y-scores/releases
install -d -o yscores -g yscores -m 700 /var/lib/y-scores
# A fresh virtualenv is built before touching the running installation.
release=/opt/y-scores/releases/$(date -u +%Y%m%dT%H%M%SZ)-$$
install -d -m 755 "$release"
for file in "$source_dir"/*.py "$source_dir"/*.html "$source_dir"/*.js "$source_dir"/requirements*.txt; do
  install -m 644 "$file" "$release/"
done
cp -R "$source_dir/fonts" "$source_dir/assets" "$release/"
python3 -m venv "$release/.venv"
"$release/.venv/bin/python" -m pip install -r "$release/requirements-pi.txt"
(cd "$release" && .venv/bin/python -m unittest discover -p 'test_*.py')
# State migration is opt-in, copies only known files, and never replaces existing state.
if [[ -n $migrate_from ]]; then
  for name in device-config.json admin-pin.txt; do
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
if [[ $legacy_active == true ]]; then systemctl disable scoreboard-device.service; fi
if [[ -n $previous && $previous != "$release" ]]; then ln -sfn "$previous" /opt/y-scores/previous; fi
trap - ERR
printf '\nY-Scores is ready: http://%s.local:%s/\nAdmin PIN: ' "$(hostname)" "${Y_SCORES_PORT:-8080}"
cat /var/lib/y-scores/admin-pin.txt
printf 'Settings and PIN preserved in /var/lib/y-scores.\n'
