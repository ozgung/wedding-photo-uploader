"""Administrative invitation setup; does not import the app or create its database."""
import hashlib
import os
import secrets
import sys
from pathlib import Path
from urllib.parse import urlsplit, parse_qs
from dotenv import dotenv_values, set_key


def ensure_invitation(app, owner=None):
    env = app / '.env'
    config = dotenv_values(env)
    origin = config.get('PUBLIC_ORIGIN')
    parts = urlsplit(origin or '')
    if parts.scheme != 'https' or not parts.hostname or parts.path or parts.query or parts.fragment:
        raise ValueError('PUBLIC_ORIGIN must be a production HTTPS origin such as https://photos.example.com')
    path = app / 'secrets/invitation-url.txt'
    created = False
    if path.exists():
        url = urlsplit(path.read_text().strip())
        token = parse_qs(url.fragment).get('invite', [''])[0]
        if f'{url.scheme}://{url.netloc}' != origin or url.path != '/' or len(token) != 32:
            raise ValueError('Existing invitation needs review; refusing to replace it')
    else:
        token = secrets.token_urlsafe(24)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as stream:
            stream.write(origin + '/#invite=' + token + '\n')
        created = True
    try:
        expected = hashlib.sha256(token.encode()).hexdigest()
        existing = config.get('INVITE_HASH')
        if existing and existing != expected:
            raise ValueError('Invitation hash mismatch; refusing to reactivate or rotate an invitation')
        if existing != expected:
            set_key(str(env), 'INVITE_HASH', expected)
        env.chmod(0o600)
        path.chmod(0o600)
        if owner is not None:
            os.chown(env, *owner)
            os.chown(path, *owner)
    except Exception:
        if created:
            path.unlink(missing_ok=True)
        raise
    return created


if __name__ == '__main__':
    if os.geteuid() != 0:
        raise SystemExit('This server setup helper must run as root.')
    import pwd
    account = pwd.getpwnam('wedding')
    os.umask(0o077)
    ensure_invitation(Path('/opt/wedding'), (account.pw_uid, account.pw_gid))
    print('Private invitation and hash configured; no database created by the administrator.')
