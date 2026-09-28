#!/usr/bin/env bash
# Owner-requested private rehearsal, after confirming Cloudflare Full (strict).
set -euo pipefail
[[ $(id -u) == 0 ]] || { echo 'Run with sudo.'; exit 1; }
OWNER=${SUDO_USER:-}
[[ -n $OWNER && $OWNER != root ]] || { echo 'Run with sudo from the owner account that should receive the invitation copy.'; exit 1; }
OWNER_HOME=$(getent passwd "$OWNER" | cut -d: -f6)
cd /opt/wedding
ORIGIN=$(/opt/wedding/.venv/bin/python -c 'from dotenv import dotenv_values; print(dotenv_values("/opt/wedding/.env").get("PUBLIC_ORIGIN") or "")')
systemctl is-active --quiet wedding-api.service wedding-worker.service wedding-validator.socket
BACKUP=$(mktemp -d /var/backups/wedding-rehearsal.XXXXXX)
cp -a .env "$BACKUP/.env"
PASSED=false
cleanup() {
  if [[ $PASSED != true ]]; then
    cp -a "$BACKUP/.env" /opt/wedding/.env
    systemctl restart wedding-api.service
    echo 'Rehearsal setup failed; previous admission settings restored.'
  fi
}
trap cleanup EXIT
/opt/wedding/.venv/bin/python -I - <<'PY'
import hashlib, os, sys
from pathlib import Path
from urllib.parse import urlsplit, parse_qs
sys.path.insert(0, '/opt/wedding')
from app.settings import Settings
from dotenv import set_key
import shutil
s=Settings()
if s.dev or not s.ready or not s.origin.startswith('https://'):
    raise SystemExit('Production configuration is not ready.')
url=urlsplit(Path('secrets/invitation-url.txt').read_text().strip())
token=parse_qs(url.fragment).get('invite',[''])[0]
if f'{url.scheme}://{url.netloc}'!=s.origin or hashlib.sha256(token.encode()).hexdigest()!=s.invite_hash:
    raise SystemExit('Invitation does not match configured admission hash.')
set_key('.env','UPLOADS_ENABLED','true')
Path('.env').chmod(0o600)
shutil.chown('.env',user='wedding',group='wedding')
PY
systemctl restart wedding-api.service
READY=false
for attempt in {1..10}; do
  if curl --fail --silent --max-time 5 "$ORIGIN/api/config" -o "$BACKUP/config.json" && \
     /opt/wedding/.venv/bin/python -c 'import json,sys; assert json.load(open(sys.argv[1]))["open"] is True' "$BACKUP/config.json" 2>/dev/null; then
    READY=true
    break
  fi
  sleep 1
done
[[ $READY == true ]] || { echo 'Public API did not become ready.'; exit 1; }
# A public visitor without the invitation must still be refused admission.
STATUS=$(curl --silent --show-error --max-time 10 -o "$BACKUP/denied.json" -w '%{http_code}' \
  -H "Origin: $ORIGIN" -H 'Content-Type: application/json' \
  --data '{"invite":"invalid-rehearsal-check","turnstile":"invalid"}' "$ORIGIN/api/session")
[[ $STATUS == 403 ]] || { echo 'Invitation admission check did not return 403.'; exit 1; }
install -d -o "$OWNER" -g "$(id -gn "$OWNER")" -m 700 "$OWNER_HOME/wedding-private"
install -o "$OWNER" -g "$(id -gn "$OWNER")" -m 600 /opt/wedding/secrets/invitation-url.txt "$OWNER_HOME/wedding-private/invitation-url.txt"
PASSED=true
printf '%s\n' 'Private rehearsal enabled; invalid invitations are rejected.' 'Your invitation is in ~/wedding-private/invitation-url.txt (owner-only permissions).' 'Upload one photo and one short video first. Keep the browser open until Drive confirmation.' 'After rehearsal, disable new uploads again until the event.'
