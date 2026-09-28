# Installation guide

This guide deploys one event to a small Ubuntu server behind nginx and Cloudflare. Replace `photos.example.com` with your subdomain throughout. Commands marked **laptop** run in your local clone; **server** commands run over SSH.

Budget an evening for setup and a separate rehearsal day. Keep `UPLOADS_ENABLED=false` until the rehearsal passes.

## 1. What you need

- **A server.** Ubuntu 24.04 with Python 3.11+ (24.04 ships 3.12), at least 1 vCPU, 1 GiB RAM and 6 GB free disk. The service ceilings are 192 MiB for the API, 500 MiB for the Drive worker and 384 MiB for the validator. Measure real usage during the rehearsal, and add swap on 1 GiB machines (`sudo bash deploy/configure-swap.sh`). nginx and Certbot are installed. Other sites on the same nginx are fine; the installers only add a `wedding` virtual host.
- **A domain on Cloudflare.** Add a proxied A/AAAA record for `photos.example.com` pointing at the server.
- **A Google account** whose Drive stores the album. Guests never sign in to Google.
- **On your laptop:** [uv](https://docs.astral.sh/uv/) and a browser. Node.js is optional; it is only needed for the JS tests.
- **Somewhere to host two static pages** (homepage and privacy policy) for Google's OAuth consent screen, for example your personal site or GitHub Pages.

## 2. Configure the event (laptop)

```bash
git clone https://github.com/ozgung/wedding-photo-uploader.git && cd wedding-photo-uploader
uv sync
cp .env.example .env && chmod 600 .env
```

Edit `.env`. The [README](../README.md#configuration) documents every variable. At minimum set `EVENT_TITLE`, `EVENT_MONOGRAM`, `EVENT_DATE`, `CLOSE_AT`, `DEFAULT_LANG`, `CONTACT_EMAIL` and `PUBLIC_ORIGIN=https://photos.example.com`.

To preview locally, run `uv run python scripts/browser_fixture.py` and open http://127.0.0.1:8766/__test_entry. This local-only fixture uses the real API and validator with a fake Drive that writes into `tmp/`. Never deploy it.

## 3. Google Drive authorization (laptop)

1. In [Google Cloud Console](https://console.cloud.google.com/), create a project and enable **APIs & Services → Library → Google Drive API**.
2. Render the public OAuth pages from your `.env` and publish them on your static host:

   ```bash
   uv run python deploy/render-oauth-pages.py --hosting-provider "Your VPS provider" \
     --hosting-privacy-url https://provider.example/privacy
   # → output/oauth-pages/{index.html,privacy.html,site.css,assets/}
   ```

3. Under **Google Auth Platform → Branding**, set the app name (e.g. "Alex & Sam Wedding Photos"), your support email, the homepage and privacy URLs from step 2, and their authorized domain. Choose **External** audience for a personal account.
4. Under **Data Access**, add only `https://www.googleapis.com/auth/drive.file`. The app can then manage only the files it creates, not your whole Drive.
5. Under **Clients → Create client**, choose **Desktop app**. Download the JSON to `secrets/google-client.json` (`chmod 700 secrets; chmod 600 secrets/google-client.json`).
6. Under **Audience**, click **Publish app** so the status is **In production**. Refresh tokens in Testing mode expire after 7 days. Publishing changes OAuth availability only; it doesn't publish your album.
7. Authorize once:

   ```bash
   uv run python -m app.manage authorize
   ```

   Sign in as the Drive owner. The command creates the private album folder (`DRIVE_FOLDER_NAME`), writes `secrets/google-token.json` and sets `DRIVE_ROOT_ID` in `.env`. It never shares the folder. Reauthorizing creates a new root folder, so don't reconnect an active album casually.

References: [Branding requirements](https://support.google.com/cloud/answer/15549049), [Desktop OAuth quickstart](https://developers.google.com/workspace/drive/api/quickstart/python), [drive.file scope](https://developers.google.com/workspace/drive/api/guides/api-specific-auth), [Testing vs Production](https://support.google.com/cloud/answer/15549945).

## 4. Prepare the server

Copy the repository to the server without `.env`, `secrets/`, `data/`, `tmp/` or `.venv/`. For example:

```bash
# laptop
rsync -a --exclude .env --exclude secrets --exclude data --exclude tmp --exclude .venv --exclude .git ./ server:wedding-src/
```

```bash
# server
sudo bash ~/wedding-src/deploy/prepare-server.sh           # /opt/wedding, venv, hash-pinned deps, API/worker units (not started)
sudo bash ~/wedding-src/deploy/install-systemd-validator.sh  # sandboxed validator + media/isolation preflight
sudoedit /opt/wedding/.env                                   # copy your EVENT_*, CLOSE_AT, LANGUAGES, CONTACT_EMAIL... values
```

The validator preflight must pass. It exercises JPEG, PNG, WebP, HEIC, HEIF, two MP4 codecs and MOV, rejects a spoofed image, and checks for:

- an unprivileged identity with no capabilities
- no network
- no host files visible to the parser
- read-only inputs

If it fails, read `sudo journalctl --since "10 minutes ago" -u "wedding-validator*" --no-pager`. Never relax host protections to make it pass. Re-run the installer after OS or dependency upgrades. [SECURITY.md](SECURITY.md) explains the design.

## 5. Install Drive credentials (server)

Stage the token and folder ID privately, then install them:

```bash
# server
install -d -m 700 ~/wedding-drive && cp ~/wedding-src/deploy/install-drive.py ~/wedding-drive/
# laptop
scp secrets/google-token.json server:wedding-drive/
grep ^DRIVE_ROOT_ID= .env | cut -d= -f2 | ssh server 'cat > wedding-drive/drive-root-id.txt'
# server
sudo /opt/wedding/.venv/bin/python ~/wedding-drive/install-drive.py
rm -r ~/wedding-drive
```

The installer checks that the token has only `drive.file`, verifies the folder is writable without uploading anything, and keeps the services stopped.

## 6. Cloudflare, Turnstile, TLS and nginx (server)

1. In Cloudflare, create a managed **Turnstile** widget restricted to `photos.example.com`.
2. Set SSL/TLS to **Full (strict)** once the origin certificate exists. Never use Flexible.
3. Bypass cache for HTML and `/api/*`, and disable Rocket Loader and script rewriting for the hostname.
4. Stage the web installer:

   ```bash
   install -d -m 700 ~/wedding-web
   cp ~/wedding-src/deploy/{install-web.sh,configure-web.py,create-invitation.py,nginx.conf} ~/wedding-web/
   python3 ~/wedding-src/scripts/cloudflare_ips.py > ~/wedding-web/cloudflare.conf    # review it
   printf '{"TURNSTILE_SITEKEY":"%s","TURNSTILE_SECRET":"%s"}\n' '<sitekey>' '<secret>' > ~/wedding-web/turnstile.json
   chmod 600 ~/wedding-web/turnstile.json
   sudo bash ~/wedding-web/install-web.sh photos.example.com
   ```

   With no Let's Encrypt account yet, prefix the command with `LE_EMAIL=you@example.com`. With several accounts, set `LE_ACCOUNT=<id>`.

The installer then:

- proves the ACME challenge reaches this server through Cloudflare
- issues or reuses the certificate
- installs the nginx virtual host from the `nginx.conf` template
- generates the private invitation into `/opt/wedding/secrets/invitation-url.txt` (its hash goes into `.env`)
- starts the API and worker
- checks origin and Cloudflare HTTPS

Uploads stay disabled throughout. On failure it rolls back its own changes and leaves other sites untouched.

Every file chunk is at most 4 MiB, below Cloudflare's request limit. Never raise nginx's body limit to accept whole videos.

## 7. Rehearse (server + phones)

```bash
sudo bash ~/wedding-src/deploy/start-rehearsal.sh
```

This enables uploads, confirms that an invalid invitation is refused, and copies the invitation URL to `~/wedding-private/invitation-url.txt` (owner-only). Then test on real iPhone Safari and Android Chrome over mobile data:

- **File types and sizes:** upload JPG, HEIC, PNG and a representative MOV/MP4, including one close to 250 MB.
- **Names and folders:** use a name with non-ASCII letters and an anonymous upload. Check that the Drive folders are correct, the files are byte-identical, and the Drive permissions are private.
- **Interrupted upload:** cut Wi-Fi mid-upload, retry, then reload and reselect the files. There should be no duplicate Drive file and no false "saved".
- **Access checks:** the bare domain without an invitation cannot upload, and invalid file types fail.
- **Load:** watch RAM and the backlog during several simultaneous uploads (`cd /opt/wedding && sudo -u wedding .venv/bin/python -m app.manage status`, or `sudo /opt/wedding/.venv/bin/python ~/wedding-src/deploy/diagnose-queue.py`).

Disable uploads again afterwards: `sudoedit /opt/wedding/.env` → `UPLOADS_ENABLED=false`, then `sudo systemctl restart wedding-api`.

## 8. QR cards

```bash
# laptop — copy the invitation URL privately from the server first
scp server:wedding-private/invitation-url.txt secrets/
uv run python scripts/print_cards.py --invite-file secrets/invitation-url.txt --second-lang en
```

This writes the A6 table card and A4 entrance sign to `output/private/`. Without `--invite-file` it writes clearly marked, non-live proofs to `output/pdf/`. Scan the printed cards at actual size in venue-like lighting before printing them all. Treat the QR code and link as an upload credential: share them privately, not on public social media. `EVENT_SLUG` adds a readable path you can type from a card. Guests still pass Turnstile, but the slug isn't a secret.

## 9. Event day and closing

- **Event day:** turn uploads back on (`UPLOADS_ENABLED=true`, then restart `wedding-api`). Check `app.manage status` and `journalctl -u wedding-worker` during the event and daily while uploads stay open. Logs contain IDs and error classes, not guest names or invitation tokens.
- **Emergency stop:** set `UPLOADS_ENABLED=false` and restart the API. New uploads stop, and the worker keeps draining accepted files.
- **Leaked invitation:** rotate it. Stop `wedding-api`, move `/opt/wedding/secrets/invitation-url.txt` aside, clear `INVITE_HASH=` in `/opt/wedding/.env`, run `sudo /opt/wedding/.venv/bin/python ~/wedding-src/deploy/create-invitation.py`, and start the API. Sessions admitted with the old link stop working. Replace the shared links and printed QR codes.
- **After `CLOSE_AT`:** uploads refuse new files, and queued media keeps transferring.
  - Resolve retries and held files before stopping the worker. `retry-held ID` is for use only after you understand the cause.
  - Share the Drive folder with family manually; the app never changes sharing.
  - Revoke the OAuth grant in your Google account, remove `/opt/wedding` and its data, and retire the subdomain. None of this deletes the Drive album.
