"""Merge only wedding web settings from a private root-owned staging snapshot."""
import json
import re
import sys
from pathlib import Path
from dotenv import dotenv_values, set_key
import shutil

source = Path(sys.argv[1])
domain = sys.argv[2]
if not re.fullmatch(r'([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}', domain):
    raise SystemExit('Invalid domain')
keys = json.loads((source / 'turnstile.json').read_text())
for name in ('TURNSTILE_SITEKEY', 'TURNSTILE_SECRET'):
    if not isinstance(keys.get(name), str) or not re.fullmatch(r'[A-Za-z0-9_-]{10,200}', keys[name]):
        raise SystemExit('Missing or invalid Turnstile key: ' + name)
    if keys[name].startswith(('1x', '2x', '3x')):
        raise SystemExit('Use production Turnstile keys, not documented test keys.')
env = Path('/opt/wedding/.env')
config = dotenv_values(env)
if config.get('UPLOADS_ENABLED', 'false').lower() != 'false':
    raise SystemExit('Uploads must be disabled before setup.')
if not config.get('DRIVE_ROOT_ID') or not Path('/opt/wedding/secrets/google-token.json').is_file():
    raise SystemExit('Complete Drive credential installation first.')
for name, value in {**keys, 'PUBLIC_ORIGIN': 'https://' + domain, 'DEV_MODE': 'false', 'UPLOADS_ENABLED': 'false'}.items():
    if name not in ('TURNSTILE_SITEKEY','TURNSTILE_SECRET','PUBLIC_ORIGIN','DEV_MODE','UPLOADS_ENABLED'):
        raise SystemExit('Unexpected configuration key')
    set_key(str(env), name, value)
env.chmod(0o600)
shutil.chown(env, user='wedding', group='wedding')
print('Turnstile and production origin configured; uploads remain disabled.')
