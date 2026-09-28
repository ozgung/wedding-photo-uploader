"""Single durable worker; no Celery, Redis, transcoding, or public file serving."""
import fcntl
import hashlib
import json
import logging
import os
import subprocess
import sys
import time
from . import db
from .drive import Drive
from .settings import Settings

log=logging.getLogger('wedding.worker')

def validator_command(s,path,name):
    if s.dev:
        return [sys.executable,'-m','app.validate',str(path),name]
    raise RuntimeError('Production validation requires the systemd socket service')

def run_validation(s,path,name):
    if s.dev:
        return subprocess.run(validator_command(s,path,name),capture_output=True,timeout=25,
                              env={**os.environ,'OMP_NUM_THREADS':'1'})
    from pathlib import Path
    from .validation_client import request_validation
    result=request_validation(path,s.validator_socket,extension=Path(name).suffix.lower())
    return subprocess.CompletedProcess([],0 if 'mime' in result else 1,json.dumps(result).encode(),b'')

def cleanup(s,now=None):
    now=now or time.time()
    with db.connection(s,True) as c:
        # Abandoned browser uploads expire after 24 hours; accepted queued files do not.
        stale=c.execute("SELECT id FROM uploads WHERE state='receiving' AND updated<?",(now-86400,)).fetchall()
        for row in stale:c.execute("UPDATE uploads SET state='expired',error='expired',updated=? WHERE id=?",(now,row['id']))
        dead=c.execute("SELECT id FROM uploads WHERE state IN ('done','cancelled','rejected','expired')").fetchall()
        c.execute('DELETE FROM rate_limits WHERE window<?',(int(now)//60-1440,))
    for row in dead:(s.data_dir/'media'/row['id']).unlink(missing_ok=True)

def process_one(s,drive_factory=Drive):
    now=time.time()
    with db.connection(s,True) as c:
        c.execute("INSERT INTO health VALUES('heartbeat',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(str(now),))
        row=c.execute("SELECT * FROM uploads WHERE state IN ('queued','retry') AND retry_at<=? ORDER BY created LIMIT 1",(now,)).fetchone()
        if not row:return False
        c.execute("UPDATE uploads SET state='processing',updated=?,attempts=attempts+1 WHERE id=?",(now,row['id']))
    uid=row['id'];path=s.data_dir/'media'/uid
    try:
        if not row['mime']:
            result=run_validation(s,path,row['name'])
            if result.returncode:
                # A failed validator service is not a bad photo.
                try:parser_error=json.loads(result.stdout).get('error')=='invalid_media'
                except (ValueError,AttributeError):parser_error=False
                if not parser_error:raise RuntimeError('validator_unavailable')
                with db.connection(s,True) as c:c.execute("UPDATE uploads SET state='rejected',error='invalid_media',updated=? WHERE id=?",(time.time(),uid))
                path.unlink(missing_ok=True)
                return True
            mime=json.loads(result.stdout)['mime']
            with open(path,'rb') as f:checksum=hashlib.file_digest(f,'md5').hexdigest()
            with db.connection(s,True) as c:c.execute('UPDATE uploads SET mime=?,checksum=? WHERE id=?',(mime,checksum,uid))
        with db.connection(s) as c:row=c.execute('SELECT * FROM uploads WHERE id=?',(uid,)).fetchone()
        fid=drive_factory(s).upload(row,path)
        with db.connection(s,True) as c:
            c.execute("UPDATE uploads SET state='done',drive_id=?,error='',updated=? WHERE id=?",(fid,time.time(),uid))
            c.execute("INSERT INTO health VALUES('last_success',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(str(time.time()),))
        path.unlink(missing_ok=True)
        log.info('Drive saved upload %s',uid)
    except (subprocess.TimeoutExpired,KeyError,ValueError) as exc:
        # Parser timeouts / ambiguous Drive integrity need owner attention, never success.
        with db.connection(s,True) as c:c.execute("UPDATE uploads SET state='held',error='review_needed',updated=? WHERE id=?",(time.time(),uid))
        log.error('Upload %s held: %s',uid,type(exc).__name__)
    except Exception as exc:
        attempts=row['attempts']+1
        with db.connection(s,True) as c:
            c.execute("UPDATE uploads SET state='retry',error='drive_delayed',retry_at=?,updated=? WHERE id=?",
                      (time.time()+min(3600,15*2**min(attempts,8)),time.time(),uid))
            c.execute("INSERT INTO health VALUES('last_error',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(type(exc).__name__,))
        # Never log exception text: HTTP exceptions can contain credentials or resumable URLs.
        log.warning('Drive delayed for %s: %s',uid,type(exc).__name__)
    return True

def main():
    os.umask(0o077);s=Settings();db.init(s)
    lock=open(s.data_dir/'worker.lock','w')
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    with db.connection(s,True) as c:c.execute("UPDATE uploads SET state='retry',retry_at=0 WHERE state='processing'")
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(message)s')
    cleaned=0
    while True:
        if time.time()-cleaned>600:cleanup(s);cleaned=time.time()
        if not process_one(s):time.sleep(2)

if __name__=='__main__':main()
