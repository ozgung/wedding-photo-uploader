#!/usr/bin/env bash
# Upgrade the prepared /opt/wedding installation. Never enables guest uploads.
set -euo pipefail
[[ $(id -u) == 0 ]] || { echo 'Run with sudo.'; exit 1; }
SOURCE_DIR=$(cd -- "$(dirname -- "$0")/.." && pwd)
APP_FILES=(settings.py worker.py validate.py validation_client.py validation_protocol.py validator_service.py)
DEPLOY_FILES=(install-systemd-validator.sh build-validator-root.py check-validator.py wedding-validator.socket wedding-validator@.service wedding-worker.service)

# Freeze the small reviewed payload into a private root-owned directory before use.
if [[ $SOURCE_DIR != /root/wedding-validator-install.* ]]; then
  SNAPSHOT=$(mktemp -d /root/wedding-validator-install.XXXXXX)
  install -d -m 755 "$SNAPSHOT/app" "$SNAPSHOT/deploy"
  for file in "${APP_FILES[@]}"; do install -m 644 "$SOURCE_DIR/app/$file" "$SNAPSHOT/app/$file"; done
  for file in "${DEPLOY_FILES[@]}"; do install -m 644 "$SOURCE_DIR/deploy/$file" "$SNAPSHOT/deploy/$file"; done
  exec bash "$SNAPSHOT/deploy/install-systemd-validator.sh"
fi
[[ -x /opt/wedding/.venv/bin/python && -d /opt/wedding/app ]] || { echo 'Run prepare-server.sh first.'; exit 1; }
for unit in wedding-api.service wedding-worker.service; do
  state=$(systemctl show "$unit" -p ActiveState --value)
  [[ $state == inactive || $state == failed ]] || { echo "$unit must be stopped before this update."; exit 1; }
done
command -v ffprobe >/dev/null
# This installer intentionally targets Ubuntu's merged-/usr layout.
[[ $(readlink -f /lib) == /usr/lib && $(readlink -f /lib64) == /usr/lib64 ]] || { echo 'Unexpected library layout; review before installing.'; exit 1; }
if ! id wedding-validator >/dev/null 2>&1; then
  useradd --system --user-group --home-dir /nonexistent --no-create-home --shell /usr/sbin/nologin wedding-validator
fi
[[ $(id -u wedding-validator) != 0 && $(id -G wedding-validator | wc -w) == 1 ]] || { echo 'Validator account must have no extra groups.'; exit 1; }

BACKUP=$(mktemp -d /var/backups/wedding-validator.XXXXXX)
cp -a /opt/wedding/app "$BACKUP/app"
install -d "$BACKUP/units"
for file in wedding-worker.service wedding-validator.socket wedding-validator@.service; do
  if [[ -e /etc/systemd/system/$file ]]; then cp -a "/etc/systemd/system/$file" "$BACKUP/units/"; fi
done
printf 'Previous application and units saved to %s\n' "$BACKUP"
systemctl stop wedding-validator.socket 2>/dev/null || true
systemctl stop 'wedding-validator@*.service' 2>/dev/null || true
PASSED=false
finish() {
  if [[ $PASSED != true ]]; then
    systemctl stop wedding-validator.socket 2>/dev/null || true
    systemctl stop 'wedding-validator@*.service' 2>/dev/null || true
    echo 'Validation setup did not pass. Wedding services remain stopped; no uploads enabled.'
    echo 'Inspect: sudo journalctl --since "10 minutes ago" -u "wedding-validator*" --no-pager'
  fi
}
trap finish EXIT
for file in "${APP_FILES[@]}"; do install -o root -g root -m 644 "$SOURCE_DIR/app/$file" "/opt/wedding/app/$file"; done
python3 "$SOURCE_DIR/deploy/build-validator-root.py"
for file in wedding-worker.service wedding-validator.socket wedding-validator@.service; do
  install -o root -g root -m 644 "$SOURCE_DIR/deploy/$file" "/etc/systemd/system/$file"
done
install -d -o root -g root -m 755 /opt/wedding/deploy
install -o root -g root -m 644 "$SOURCE_DIR/deploy/check-validator.py" /opt/wedding/deploy/check-validator.py
systemd-analyze verify /etc/systemd/system/wedding-validator.socket /etc/systemd/system/wedding-validator@.service /etc/systemd/system/wedding-worker.service
systemctl daemon-reload
systemctl reset-failed wedding-validator.socket 2>/dev/null || true
systemctl start wedding-validator.socket
# Generated media only; no credentials, real uploads, or Google calls.
cd /opt/wedding
runuser -u wedding -- env PYTHON_DOTENV_DISABLED=1 /opt/wedding/.venv/bin/python -B /opt/wedding/deploy/check-validator.py
PASSED=true
printf '%s\n' 'Systemd media and isolation checks passed.' 'Only the private validator socket is running. API/worker remain stopped; credentials and end-to-end rehearsal are next.'
