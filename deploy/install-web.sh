#!/usr/bin/env bash
# Add only the wedding virtual host for DOMAIN, e.g. sudo deploy/install-web.sh photos.example.com
# Reuses the registered Certbot account; set LE_ACCOUNT when there are several, or LE_EMAIL to register one.
set -euo pipefail
[[ $(id -u) == 0 ]] || { echo 'Run with sudo.'; exit 1; }
DOMAIN=${1:-}
[[ $DOMAIN =~ ^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$ ]] || { echo 'Usage: sudo install-web.sh <domain>, e.g. photos.example.com'; exit 1; }
SOURCE_DIR=$(cd -- "$(dirname -- "$0")" && pwd)
# Freeze scripts/config and keys before privileged work.
if [[ $SOURCE_DIR != /root/wedding-web-install.* ]]; then
  SNAPSHOT=$(mktemp -d /root/wedding-web-install.XXXXXX)
  for file in install-web.sh configure-web.py create-invitation.py nginx.conf cloudflare.conf turnstile.json; do
    install -o root -g root -m 600 "$SOURCE_DIR/$file" "$SNAPSHOT/$file"
  done
  exec env LE_ACCOUNT="${LE_ACCOUNT:-}" LE_EMAIL="${LE_EMAIL:-}" bash "$SNAPSHOT/install-web.sh" "$DOMAIN"
fi
for unit in wedding-api.service wedding-worker.service; do
  state=$(systemctl show "$unit" -p ActiveState --value)
  [[ $state == inactive || $state == failed ]] || { echo "$unit must be stopped before setup."; exit 1; }
done
[[ ! -e /etc/nginx/sites-available/wedding && ! -e /etc/nginx/sites-enabled/wedding ]] || { echo 'Wedding virtual host already exists; stop for review.'; exit 1; }
nginx -t
BACKUP=$(mktemp -d /var/backups/wedding-web.XXXXXX)
cp -a /opt/wedding/.env "$BACKUP/.env"
PASSED=false
INVITE_CREATED=false
PROBE=
cleanup() {
  if [[ -n $PROBE ]]; then rm -f "/var/www/wedding-acme/.well-known/acme-challenge/$PROBE"; fi
  if [[ $PASSED != true ]]; then
    systemctl stop wedding-api.service wedding-worker.service 2>/dev/null || true
    rm -f /etc/nginx/sites-enabled/wedding /etc/nginx/sites-available/wedding
    cp -a "$BACKUP/.env" /opt/wedding/.env
    if [[ $INVITE_CREATED == true ]]; then rm -f /opt/wedding/secrets/invitation-url.txt; fi
    if nginx -t; then systemctl reload nginx; fi
    echo 'Setup failed; wedding routing removed and previous settings restored. Existing sites remain configured.'
  fi
}
trap cleanup EXIT
/opt/wedding/.venv/bin/python "$SOURCE_DIR/configure-web.py" "$SOURCE_DIR" "$DOMAIN"
install -d -m 755 /var/www/wedding-acme/.well-known/acme-challenge /etc/nginx/snippets
CERT=/etc/letsencrypt/live/$DOMAIN/fullchain.pem
KEY=/etc/letsencrypt/live/$DOMAIN/privkey.pem
if [[ -s $CERT && -s $KEY ]] && \
   openssl x509 -in "$CERT" -noout -checkend 604800 >/dev/null && \
   openssl x509 -in "$CERT" -noout -checkhost "$DOMAIN" >/dev/null; then
  echo 'Reusing the existing unexpired wedding certificate.'
else
cat > /etc/nginx/sites-available/wedding <<NGINX
server {
    listen 80;
    listen [::]:80;
    server_name $DOMAIN;
    access_log off;
    location ^~ /.well-known/acme-challenge/ {
        root /var/www/wedding-acme;
        default_type text/plain;
        try_files \$uri =404;
    }
    location / { return 503; }
}
NGINX
ln -s /etc/nginx/sites-available/wedding /etc/nginx/sites-enabled/wedding
nginx -t
systemctl reload nginx
# Prove public DNS/Cloudflare reaches this server before attempting certificate issuance.
PROBE=$(openssl rand -hex 12)
printf '%s' "$PROBE" > "/var/www/wedding-acme/.well-known/acme-challenge/$PROBE"
chmod 644 "/var/www/wedding-acme/.well-known/acme-challenge/$PROBE"
# nginx reload returns before replacement workers necessarily serve new requests.
RESPONSE=
for attempt in {1..10}; do
  RESPONSE=$(curl --fail --silent --max-time 5 "http://$DOMAIN/.well-known/acme-challenge/$PROBE") || RESPONSE=
  [[ $RESPONSE == "$PROBE" ]] && break
  sleep 1
done
[[ $RESPONSE == "$PROBE" ]] || { echo 'Public HTTP challenge does not reach this server after retries. Check the Cloudflare DNS target/redirect rules.'; exit 1; }
rm -f "/var/www/wedding-acme/.well-known/acme-challenge/$PROBE"
PROBE=
shopt -s nullglob
ACCOUNTS=(/etc/letsencrypt/accounts/acme-v02.api.letsencrypt.org/directory/*/regr.json)
if [[ -n ${LE_ACCOUNT:-} ]]; then
  ACCOUNT_ARGS=(--account "$LE_ACCOUNT")
elif [[ ${#ACCOUNTS[@]} == 1 ]]; then
  ACCOUNT_ARGS=(--account "$(basename -- "$(dirname -- "${ACCOUNTS[0]}")")")
elif [[ ${#ACCOUNTS[@]} == 0 && -n ${LE_EMAIL:-} ]]; then
  ACCOUNT_ARGS=(--agree-tos -m "$LE_EMAIL")
else
  echo 'Choose a Let’s Encrypt account: set LE_ACCOUNT=<id>, or LE_EMAIL=<address> to register the first one.'; exit 1
fi
certbot certonly --non-interactive --webroot -w /var/www/wedding-acme -d "$DOMAIN" \
  --cert-name "$DOMAIN" --keep-until-expiring "${ACCOUNT_ARGS[@]}" \
  --deploy-hook '/usr/sbin/nginx -t && /usr/bin/systemctl reload nginx'
fi
install -o root -g root -m 644 "$SOURCE_DIR/cloudflare.conf" /etc/nginx/snippets/wedding-cloudflare.conf
sed "s/__DOMAIN__/$DOMAIN/g" "$SOURCE_DIR/nginx.conf" > /etc/nginx/sites-available/wedding
chmod 644 /etc/nginx/sites-available/wedding
ln -sfn /etc/nginx/sites-available/wedding /etc/nginx/sites-enabled/wedding
nginx -t
cd /opt/wedding
# Generate the invitation privately; never print it or rotate an existing invitation.
if [[ ! -f /opt/wedding/secrets/invitation-url.txt ]]; then
  INVITE_CREATED=true
fi
/opt/wedding/.venv/bin/python "$SOURCE_DIR/create-invitation.py"
systemctl start wedding-api.service
systemctl reload nginx
wait_for_config() {
  label=$1
  shift
  for attempt in {1..10}; do
    if curl --fail --silent --show-error --max-time 5 "$@" "https://$DOMAIN/api/config" \
       -o "$BACKUP/web-check.json" 2>"$BACKUP/web-check-error.txt" && \
       /opt/wedding/.venv/bin/python -c 'import json,sys; from dotenv import dotenv_values; v=json.load(open(sys.argv[1])); c=dotenv_values("/opt/wedding/.env"); assert v["open"] is False and v["sitekey"]==c["TURNSTILE_SITEKEY"] and v["chunk_size"]==4194304' \
       "$BACKUP/web-check.json" 2>/dev/null; then
      echo "$label reaches the configured wedding API; uploads are disabled."
      return 0
    fi
    sleep 1
  done
  echo "$label did not reach the expected wedding API after retries."
  cat "$BACKUP/web-check-error.txt"
  return 1
}
# TLS verification stays enabled; both the origin and Cloudflare must serve this app.
wait_for_config 'Origin HTTPS' --resolve "$DOMAIN:443:127.0.0.1"
wait_for_config 'Cloudflare HTTPS'
systemctl start wedding-worker.service
PASSED=true
# Keep the backup private; remove the extra root-owned staging key copy.
rm -f "$SOURCE_DIR/turnstile.json"
printf '%s\n' 'Wedding HTTPS, API and worker are running with uploads disabled.' 'Verify Cloudflare SSL/TLS is Full (strict), then perform the private upload rehearsal.' "Previous settings: $BACKUP"
