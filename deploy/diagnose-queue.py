"""Read-only queue diagnostics; omit names, credentials and Drive IDs."""
import json
import sqlite3
import time
from pathlib import Path

path = Path('/opt/wedding/data/wedding.sqlite3')
connection = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
connection.row_factory = sqlite3.Row
now = time.time()
print('Queue summary:')
for row in connection.execute('SELECT state, COUNT(*) AS count FROM uploads GROUP BY state'):
    print(json.dumps(dict(row)))
print('Recent uploads (no filenames or guest names):')
for row in connection.execute('SELECT state,error,size,offset,attempts,mime,checksum,drive_id,retry_at,updated,id FROM uploads ORDER BY created DESC LIMIT 10'):
    print(json.dumps({
        'state':row['state'], 'error':row['error'], 'bytes':row['size'],
        'received_bytes':row['offset'], 'attempts':row['attempts'],
        'validated':bool(row['mime']), 'checksum_present':bool(row['checksum']),
        'drive_id_allocated':bool(row['drive_id']),
        'retry_in_seconds':max(0,round(row['retry_at']-now)),
        'last_update_seconds_ago':round(now-row['updated']),
        'local_file_present':(path.parent/'media'/row['id']).is_file(),
    }))
print('Worker health:')
for row in connection.execute('SELECT key,value FROM health'):
    key,value=row['key'],row['value']
    if key in ('heartbeat','last_success'):
        print(key+'_seconds_ago',round(now-float(value)))
    elif key=='last_error':
        print(key, value if value.isidentifier() else '[redacted]')
