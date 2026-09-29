#!/usr/bin/env bash
set -Eeuo pipefail
[[ $EUID == 0 ]] || { echo 'Run with sudo.' >&2; exit 1; }
exec 9>/run/lock/y-scores-install.lock
flock -n 9 || { echo 'Another install/rollback is running.' >&2; exit 1; }
current=$(readlink -f /opt/y-scores/current)
previous=$(readlink -f /opt/y-scores/previous)
[[ -d $previous && $previous == /opt/y-scores/releases/* && $previous != "$current" ]] || { echo 'No previous release available.' >&2; exit 1; }
source /etc/y-scores.env
systemctl stop y-scores
ln -sfn "$previous" /opt/y-scores/current
systemctl start y-scores
for _ in $(seq 1 25); do
  if systemctl is-active --quiet y-scores && curl -fsS --max-time 2 "http://127.0.0.1:${Y_SCORES_PORT:-8080}/healthz" >/dev/null; then
    ln -sfn "$current" /opt/y-scores/previous
    echo "Restored $previous; settings unchanged."; exit 0
  fi
  sleep 1
done
systemctl stop y-scores
ln -sfn "$current" /opt/y-scores/current
systemctl start y-scores
echo 'Rollback failed its health check; restored original release.' >&2
exit 1
