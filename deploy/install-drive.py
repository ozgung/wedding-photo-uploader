"""Install owner Drive credentials without changing other settings or starting services."""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from dotenv import dotenv_values, set_key

CHECK = '''import sys
sys.path.insert(0, '/opt/wedding')
from app.settings import Settings
from app.drive import Drive
try:
    settings = Settings()
    adapter = Drive(settings)
    adapter.api._http.http.timeout = 20
    folder = adapter.api.files().get(fileId=settings.drive_root, fields='mimeType,trashed,capabilities(canAddChildren)').execute()
    assert folder.get('mimeType') == 'application/vnd.google-apps.folder'
    assert not folder.get('trashed')
    assert folder.get('capabilities', {}).get('canAddChildren') is True
except Exception as exc:
    print('Drive connection check failed: ' + type(exc).__name__)
    sys.exit(1)
print('Drive authorization works; the configured album folder is writable. No files uploaded or shared.')
'''


def main():
    if os.geteuid() != 0:
        raise SystemExit('Run with sudo using /opt/wedding/.venv/bin/python.')
    source = Path(__file__).resolve().parent
    app = Path('/opt/wedding')
    for unit in ('wedding-api.service', 'wedding-worker.service'):
        state = subprocess.check_output(['systemctl', 'show', unit, '-p', 'ActiveState', '--value'], text=True).strip()
        if state not in ('inactive', 'failed'):
            raise SystemExit('Stop the wedding API and worker before credential installation.')
    config = dotenv_values(app / '.env')
    if config.get('DEV_MODE', 'false').lower() != 'false' or config.get('UPLOADS_ENABLED', 'false').lower() != 'false':
        raise SystemExit('Server must remain in production mode with uploads disabled.')
    # Read once into memory; do not execute code from a user-writable credential file.
    token_bytes = (source / 'google-token.json').read_bytes()
    token = json.loads(token_bytes)
    folder = (source / 'drive-root-id.txt').read_text().strip()
    if token.get('scopes') != ['https://www.googleapis.com/auth/drive.file'] or not token.get('refresh_token'):
        raise SystemExit('Expected an offline token with only drive.file permission.')
    if token.get('token_uri') != 'https://oauth2.googleapis.com/token':
        raise SystemExit('Unexpected Google token endpoint.')
    if not re.fullmatch(r'[A-Za-z0-9_-]+', folder):
        raise SystemExit('Invalid album folder identifier.')
    backup = Path(tempfile.mkdtemp(prefix='wedding-drive.', dir='/var/backups'))
    shutil.copy2(app / '.env', backup / '.env')
    target = app / 'secrets/google-token.json'
    existing = target.exists()
    if existing:
        shutil.copy2(target, backup / 'google-token.json')
    try:
        target.parent.mkdir(mode=0o700, exist_ok=True)
        # umask 077 ensures the file is private from its creation.
        with tempfile.NamedTemporaryFile(dir=app, prefix='.drive-token-', delete=False) as stream:
            stream.write(token_bytes)
            temporary = Path(stream.name)
        os.replace(temporary, target)
        target.chmod(0o600)
        shutil.chown(target.parent, user='wedding', group='wedding')
        shutil.chown(target, user='wedding', group='wedding')
        set_key(str(app / '.env'), 'GOOGLE_TOKEN_FILE', 'secrets/google-token.json')
        set_key(str(app / '.env'), 'DRIVE_ROOT_ID', folder)
        (app / '.env').chmod(0o600)
        shutil.chown(app / '.env', user='wedding', group='wedding')
        # Only reads folder metadata; authenticates as the production service user.
        subprocess.run(['timeout', '--kill-after=5s', '90s', 'runuser', '-u', 'wedding', '--',
                        str(app / '.venv/bin/python'), '-I', '-B', '-'],
                       input=CHECK, text=True, cwd=app, check=True)
    except Exception:
        shutil.copy2(backup / '.env', app / '.env')
        shutil.chown(app / '.env', user='wedding', group='wedding')
        if existing:
            shutil.copy2(backup / 'google-token.json', target)
            shutil.chown(target, user='wedding', group='wedding')
        else:
            target.unlink(missing_ok=True)
        raise SystemExit('Drive setup failed; previous credentials/settings restored. Services remain stopped.')
    # The server no longer needs these extra staging copies after a successful check.
    (source / 'google-token.json').unlink()
    (source / 'drive-root-id.txt').unlink()
    print('Drive credentials installed privately. Other configuration preserved; services remain stopped.')
    print('Previous configuration backup: ' + str(backup))


if __name__ == '__main__':
    os.umask(0o077)
    main()
