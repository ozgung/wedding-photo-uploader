# Security and reliability boundaries

## Guest admission

The original invitation contains 192 random bits in a URL fragment. The browser removes the fragment before loading Turnstile, exchanges it plus a server-verified bot proof for an opaque session, and clears the pending invite from session storage. The token is not in HTTP request URLs or normal access logs. Only its SHA-256 hash is configured server-side. A readable event path can also be configured for the printed card; it still requires the same server-verified Turnstile proof before a session is issued. The readable path is a convenience label and must not be treated as a secret. Unknown paths return 404, directory indexes and API documentation are absent, nginx access logging is disabled, and robots/noindex directives discourage indexing. None of those discovery controls is authentication.

Sessions use host-only HttpOnly, Secure, SameSite=Strict cookies in production. Every upload operation checks session ownership; write operations also require the exact Origin and a per-session CSRF token. Rotating the invitation hash invalidates sessions created by either entry method. Anyone who passes Turnstile through the configured event path may upload, so the short event window, upload budgets, rate limits and emergency stop remain necessary abuse controls. No guest may choose a Drive parent, retrieve another guest's upload status, download media, or access owner administration routes. There are no public admin routes.

## Bytes and storage

Admission reserves the full declared file size atomically in SQLite, including global and session budgets, so simultaneous admissions cannot overcommit the disk. Chunks are bounded, exact-size and offset checked; at most four are received at once. Local filenames are opaque IDs, outside the webroot. Only a single API process is supported. Never scale Uvicorn workers without replacing the in-process chunk locks with cross-process coordination.

Retrying initiation with the same per-session client ID returns the existing upload. Disk writes are fsynced before advancing the committed offset; a crash tail is overwritten on retry. A worker checks the media in a separate systemd service sandbox with no network or credentials, CPU/address-space/file limits and a wall-clock timeout. The video probe permits file/pipe protocols only. It then sends original bytes to Drive in resumable chunks. Generated Drive IDs are saved before creation, and reconciliation checks size, MD5 and the upload ID to handle a crash after Drive commits without duplicate files. MD5 here is Drive's transport integrity check, not an authentication primitive.

Validation allows JPG/JPEG, PNG, WebP, HEIC/HEIF, MP4/MOV/M4V. Format/extension mismatch, malformed media and excessive image dimensions are rejected. No archives, SVG, HTML or executable formats are accepted. Validation is not sanitization: original media retains metadata and could contain an unknown exploit or inappropriate content. There is no antivirus guarantee or image-content classifier. The server never publishes uploaded files. Review privately in Drive and use updated media viewers.

## Failure behavior

“Saved” means Drive confirmed the file, including expected size/checksum. Drive outages trigger capped exponential backoff while keeping the bounded local copy. Parser infrastructure failures are retried, not mislabeled as unsafe photos. Ambiguous integrity/validation timeouts are held for owner review. Disk pressure refuses new uploads with an actionable busy message. Browser progress resumes at chunk boundaries; reloading requires reselecting the same file. Mobile browsers can suspend background tabs, so the page asks guests to keep it open during transfer. This is not an offline/background-upload PWA.

Expired partial uploads are removed after 24 hours. Confirmed files are removed locally; queued/retry/held files are not silently discarded. The worker uses an exclusive process lock and recovers interrupted jobs at startup. A whole-server/disk failure can still lose files waiting for Drive; guests should keep their originals. No redundant object storage is provisioned.

## Verified versus pending

Automated tests cover admission, Origin/CSRF, ownership, revocation, bounds, atomic reservation, retry offsets, invalid formats, Drive-outage handling, success cleanup and locale consistency. Google is mocked in local tests. On Ubuntu 24.04, `deploy/install-systemd-validator.sh` runs the generated-media and isolation preflight (eight media fixtures, a spoofed file, and every access-restriction assertion); it must pass on your server before uploads are enabled. Real Drive OAuth, nginx/Cloudflare routing and real phone/venue load tests are deployment gates for each installation, not claims this repository can make.

Guidance: [OWASP file uploads](https://cheatsheetseries.owasp.org/cheatsheets/File_Upload_Cheat_Sheet.html), [systemd service isolation](https://www.freedesktop.org/software/systemd/man/latest/systemd.exec.html).

## Validator boundary (systemd)

The root-managed socket accepts connections from the wedding group only. Each service instance runs as the separate non-login wedding-validator user with an empty capability set, no new privileges, no IPv4/IPv6 socket creation, and a private network. The worker has no sudo or service-management permission. The bounded request contains one read-only regular-file descriptor and an allowed extension, never a shell command or host pathname. Only one service instance runs at a time.

The restricted root filesystem exposes installed /usr and the root-owned virtual environment read-only, three validator modules, and a private 16 MiB temporary filesystem. Credentials, database, upload directories, home directories and host service sockets are absent. Library aliases and the loader cache are copied from the trusted host; rebuild the validator root after dependency upgrades. The parser is limited to 384 MiB memory, 15 seconds CPU, 30 seconds wall time, and 24 tasks. It shares the host kernel; this is defense in depth, not a separate VM or an antivirus guarantee.

Run deploy/install-systemd-validator.sh with sudo on the prepared server. It freezes the payload into a private root-owned snapshot, saves the previous application/units, requires the wedding API and worker to be stopped, and tests generated media and access restrictions. A failed preflight stops the socket; the worker never falls back to unsandboxed production validation. Local unit tests cannot prove Linux confinement.

## Why a systemd sandbox rather than bubblewrap

An earlier design ran the parser under `bwrap --unshare-all`. On Ubuntu 24.04, AppArmor restricts unprivileged user namespaces (`kernel.apparmor_restrict_unprivileged_userns=1`), so bubblewrap fails with `setting up uid map: Permission denied`. Sharing the network does not fix it, and relaxing that host-wide restriction or putting a broad bwrap profile in complain mode would weaken every other service on the machine. A root-managed systemd socket plus a per-request, heavily restricted service instance gives network isolation and a minimal filesystem without changing host policy.

Two practical lessons are built into `build-validator-root.py`: the restricted root does not expose the host's `/etc`, so only the `/etc/alternatives` library aliases (BLAS/LAPACK, needed by ffmpeg) are recreated as links to their resolved targets under `/usr`; and loader failures (exit 126/127, missing shared libraries) are reported as `validator_unavailable` and retried, never as a guest's invalid file.
