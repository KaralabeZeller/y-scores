# Y-Scores agent guide

This is the standalone Raspberry Pi scoreboard; y-sports-core contains the server API.
Preserve per-device settings, PINs and /etc/y-scores.env on every deployment.
Do not commit state/, credentials, generated images, virtualenvs or build outputs.
New devices must boot to the logo, without a hardcoded match assignment.
Run `python -m unittest discover -p 'test_*.py'`, `node --check admin.js` and
`bash -n scripts/install.sh scripts/rollback.sh` for relevant changes.
The hardware implementation supports Raspberry Pi 5 and three 64x64 panels on
an Active-3-compatible bonnet. Do not claim Pi 3/4 support without another driver.
Keep installation, migration and rollback instructions in README.md current.
