#!/usr/bin/env bash
# Run with sudo from the staged package. Installs only; does not start services.
set -euo pipefail
[[ $(id -u) == 0 ]] || { echo 'Run with sudo.'; exit 1; }
SOURCE_DIR=$(cd -- "$(dirname -- "$0")/.." && pwd)
[[ ! -e /opt/wedding ]] || { echo '/opt/wedding already exists; stop for review.'; exit 1; }
FREE_KB=$(df -Pk /opt | awk 'NR==2 {print $4}')
(( FREE_KB >= 6000000 )) || { echo 'Need at least 6 GB available.'; exit 1; }
apt-get update
apt-get install -y --no-install-recommends python3-venv ffmpeg
if ! id wedding >/dev/null 2>&1; then
  useradd --system --user-group --home-dir /opt/wedding --shell /usr/sbin/nologin wedding
fi
install -d -m 755 /opt/wedding
cp -R "$SOURCE_DIR/app" /opt/wedding/app
cp "$SOURCE_DIR/deploy/requirements.txt" /opt/wedding/requirements.txt
chown -R root:root /opt/wedding
chmod -R u=rwX,go=rX /opt/wedding/app
python3 -m venv /opt/wedding/.venv
/opt/wedding/.venv/bin/pip install --no-cache-dir --require-hashes --only-binary=:all: -r /opt/wedding/requirements.txt
install -d -o wedding -g wedding -m 700 /opt/wedding/data /opt/wedding/data/media /opt/wedding/secrets
install -o wedding -g wedding -m 600 "$SOURCE_DIR/.env.example" /opt/wedding/.env
install -m 644 "$SOURCE_DIR/deploy/wedding-api.service" /etc/systemd/system/wedding-api.service
install -m 644 "$SOURCE_DIR/deploy/wedding-worker.service" /etc/systemd/system/wedding-worker.service
systemctl daemon-reload
printf '%s\n' 'Installed, but services are not started or enabled. Uploads are disabled.' 'Next: run deploy/install-systemd-validator.sh, then memory review, Drive and Turnstile credentials, nginx/TLS.'
