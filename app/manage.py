"""Owner-only local commands; no public administration routes."""
import argparse
import json
import os
import secrets
import shutil
import time
from pathlib import Path
from dotenv import set_key
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from . import db
from .drive import SCOPES
from .main import digest
from .settings import Settings

def main():
    p=argparse.ArgumentParser();sub=p.add_subparsers(dest='command',required=True)
    sub.add_parser('invite').add_argument('--rotate',action='store_true')
    auth=sub.add_parser('authorize');auth.add_argument('--client',type=Path,default=Path('secrets/google-client.json'))
    sub.add_parser('status')
    retry=sub.add_parser('retry-held');retry.add_argument('upload_id')
    args=p.parse_args();os.umask(0o077);s=Settings();db.init(s)
    if args.command=='invite':
        path=Path('secrets/invitation-url.txt');path.parent.mkdir(exist_ok=True,mode=0o700)
        if path.exists() and not args.rotate:p.error('Invitation exists. Use --rotate to revoke existing sessions and replace the printed QR.')
        token=secrets.token_urlsafe(24);path.write_text(s.origin+'/#invite='+token+'\n');path.chmod(0o600)
        set_key('.env','INVITE_HASH',digest(token));Path('.env').chmod(0o600)
        print('Invitation saved to secrets/invitation-url.txt; .env updated. Restart API and regenerate final QR cards.')
    elif args.command=='authorize':
        if s.google_token.exists():p.error('Token already exists. Move it aside explicitly before reconnecting.')
        flow=InstalledAppFlow.from_client_secrets_file(str(args.client),SCOPES)
        credentials=flow.run_local_server(port=0,access_type='offline',prompt='consent')
        api=build('drive','v3',credentials=credentials,cache_discovery=False)
        root=api.files().create(body={'name':s.drive_folder_name,'mimeType':'application/vnd.google-apps.folder'},fields='id').execute()['id']
        s.google_token.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        s.google_token.write_text(credentials.to_json());s.google_token.chmod(0o600)
        set_key('.env','DRIVE_ROOT_ID',root);Path('.env').chmod(0o600)
        print('Owner authorization saved and private event folder created. .env updated; no sharing permissions changed.')
    elif args.command=='status':
        with db.connection(s) as c:
            counts={r[0]:r[1] for r in c.execute('SELECT state,COUNT(*) FROM uploads GROUP BY state')}
            health={r[0]:r[1] for r in c.execute('SELECT key,value FROM health')}
            oldest=c.execute("SELECT MIN(created) FROM uploads WHERE state IN ('queued','retry','held')").fetchone()[0]
        print(json.dumps({'configured':s.ready,'enabled':s.enabled,'closes':s.close_at,'states':counts,
            'free_gb':round(shutil.disk_usage(s.data_dir).free/1e9,2),'oldest_pending_seconds':round(time.time()-oldest) if oldest else 0,'worker':health},indent=2))
    elif args.command=='retry-held':
        with db.connection(s,True) as c:
            r=c.execute("UPDATE uploads SET state='retry',retry_at=0,error='' WHERE id=? AND state='held'",(args.upload_id,))
            print(f'{r.rowcount} upload(s) queued for retry. Resolve the underlying issue first.')

if __name__=='__main__':main()
