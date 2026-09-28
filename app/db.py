import sqlite3
from contextlib import contextmanager

SCHEMA = '''
CREATE TABLE IF NOT EXISTS sessions (
 id TEXT PRIMARY KEY, token_hash TEXT UNIQUE NOT NULL, csrf TEXT NOT NULL,
 invite_hash TEXT NOT NULL, created REAL NOT NULL, expires REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS uploads (
 id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id),
 client_id TEXT NOT NULL, name TEXT NOT NULL, guest TEXT NOT NULL,
 size INTEGER NOT NULL, offset INTEGER NOT NULL DEFAULT 0,
 state TEXT NOT NULL DEFAULT 'receiving', error TEXT NOT NULL DEFAULT '',
 created REAL NOT NULL, updated REAL NOT NULL, retry_at REAL NOT NULL DEFAULT 0,
 attempts INTEGER NOT NULL DEFAULT 0, drive_id TEXT, mime TEXT, checksum TEXT,
 UNIQUE(session_id, client_id)
);
CREATE TABLE IF NOT EXISTS folders (key TEXT PRIMARY KEY, drive_id TEXT NOT NULL, ready INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS rate_limits (key TEXT NOT NULL, window INTEGER NOT NULL, count INTEGER NOT NULL, PRIMARY KEY(key,window));
CREATE INDEX IF NOT EXISTS upload_queue ON uploads(state, retry_at, created);
CREATE TABLE IF NOT EXISTS health (key TEXT PRIMARY KEY, value TEXT NOT NULL);
'''
def connect(settings):
    c = sqlite3.connect(settings.data_dir / 'wedding.sqlite3', timeout=10, isolation_level=None)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA foreign_keys=ON')
    c.execute('PRAGMA busy_timeout=10000')
    return c

def init(settings):
    settings.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    (settings.data_dir / 'media').mkdir(exist_ok=True, mode=0o700)
    with connection(settings) as c:
        c.execute('PRAGMA journal_mode=WAL')
        c.executescript(SCHEMA)
    (settings.data_dir / 'wedding.sqlite3').chmod(0o600)

@contextmanager
def connection(settings, write=False):
    c = connect(settings)
    try:
        if write:c.execute('BEGIN IMMEDIATE')
        yield c
        if write:c.commit()
    except BaseException:
        if write:c.rollback()
        raise
    finally:c.close()
