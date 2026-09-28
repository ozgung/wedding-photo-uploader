#!/usr/bin/env bash
# Enable 2 GiB swap without modifying existing application limits.
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo 'Run with sudo.' >&2; exit 1; }
exec 9>/run/wedding-swap-setup.lock
flock -n 9 || { echo 'Swap setup is already running.' >&2; exit 1; }
path=/swapfile
size=2147483648
if [[ -e $path || -L $path ]]; then
  [[ -f $path && ! -L $path ]] || { echo 'Existing /swapfile is not a regular file; refusing to modify it.' >&2; exit 1; }
  [[ $(stat -c %s "$path") == "$size" && $(stat -c %u "$path") == 0 ]] || { echo 'Existing file size/owner differs; refusing to modify it.' >&2; exit 1; }
  [[ $(blkid -p -s TYPE -o value "$path") == swap ]] || { echo 'Existing /swapfile has no valid swap signature; left untouched.' >&2; exit 1; }
  echo 'Reusing existing 2 GiB swap file.'
else
  [[ $(findmnt -n -o FSTYPE -T /) == ext4 ]] || { echo 'Expected ext4; review filesystem before creating swap.' >&2; exit 1; }
  available=$(df -B1 --output=avail / | tail -1 | tr -d ' ')
  (( available >= size + 4294967296 )) || { echo 'Insufficient free disk space to retain 4 GiB after allocation.' >&2; exit 1; }
  install -o root -g root -m 0600 /dev/null "$path"
  fallocate -l "$size" "$path"
  mkswap "$path"
fi
chmod 0600 "$path"
if ! swapon --show=NAME --noheadings --raw | grep -Fxq "$path"; then
  swapon "$path"
fi
# Validate existing entries before adding persistence. Never duplicate an entry.
python3 - <<'PY'
from pathlib import Path
import shutil
import tempfile
p=Path('/etc/fstab')
text=p.read_text()
entries=[line.split() for line in text.splitlines() if line.strip() and not line.lstrip().startswith('#')]
matches=[entry for entry in entries if entry[0]=='/swapfile']
if matches:
    if len(matches)!=1 or len(matches[0])<4 or matches[0][2]!='swap' or 'noauto' in matches[0][3].split(','):
        raise SystemExit('Swap is active, but conflicting /etc/fstab entry needs review; fstab unchanged.')
else:
    backup=Path(tempfile.mkdtemp(prefix='wedding-swap.',dir='/var/backups'))/'fstab'
    shutil.copy2(p,backup)
    p.write_text(text.rstrip('\n')+'\n/swapfile none swap sw 0 0\n')
    print('Previous fstab saved to',backup)
PY
systemctl daemon-reload
swapon --show
free -m
echo '2 GiB swap enabled and configured for boot. Validator limits unchanged.'
