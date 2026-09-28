"""One socket-activated job per isolated systemd service instance."""
import os
import socket
import subprocess
import sys
from pathlib import Path
# -I excludes the working directory and PYTHONPATH; only root-owned code is used.
if __package__ in (None,''):
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.validation_protocol import receive_request,send_json
from app.validate import validate,ValidatorUnavailable

def isolation_check(fd):
    status={}
    for line in Path('/proc/self/status').read_text().splitlines():
        key,_,value=line.partition(':');status[key]=value.strip()
    def inaccessible(path):
        try:
            with open(path,'rb'):pass
            return False
        except OSError:return True
    def socket_denied(family):
        try:
            with socket.socket(family,socket.SOCK_STREAM):pass
            return False
        except OSError:return True
    try:os.write(fd,b'x');readonly=False
    except OSError:readonly=True
    # A successful write to this path would be a containment failure.
    try:
        with open('/app/.isolation-write-probe','xb'):pass
        code_readonly=False
        Path('/app/.isolation-write-probe').unlink()
    except OSError:code_readonly=True
    checks={'nonroot':os.geteuid()!=0,'no_capabilities':int(status['CapEff'],16)==0,
        'no_new_privileges':status.get('NoNewPrivs')=='1',
        'ipv4_socket_denied':socket_denied(socket.AF_INET),
        'ipv6_socket_denied':socket_denied(socket.AF_INET6),
        'credentials_hidden':inaccessible('/opt/wedding/.env') and inaccessible('/opt/wedding/secrets/google-token.json'),
        'database_hidden':inaccessible('/opt/wedding/data/wedding.sqlite3'),
        'other_upload_hidden':inaccessible('/opt/wedding/data/media/isolation-sentinel'),
        'host_root_hidden':inaccessible('/proc/1/root/opt/wedding/data/media/isolation-sentinel'),
        'host_sentinel_hidden':inaccessible('/var/lib/wedding-validator/host-only-sentinel'),
        'proc_host_sentinel_hidden':inaccessible('/proc/1/root/var/lib/wedding-validator/host-only-sentinel'),
        'input_readonly':readonly,'code_readonly':code_readonly,
        'no_credential_environment':not any(k.startswith(('GOOGLE_','TURNSTILE_','INVITE_')) for k in os.environ)}
    return {'checks':checks,'passed':all(checks.values())}

def handle(sock):
    fd=None
    try:
        fd,ext,operation=receive_request(sock)
        if operation=='isolation-check':result=isolation_check(fd)
        else:result={'mime':validate(Path(f'/proc/self/fd/{fd}'),'input'+ext,input_fd=fd)}
    except (ValidatorUnavailable,FileNotFoundError,PermissionError,ImportError,MemoryError,subprocess.TimeoutExpired):result={'error':'validator_unavailable'}
    except (ValueError, OSError):
        # Malformed protocol/files are rejected; socket service configuration is separate.
        result={'error':'invalid_media'}
    except Exception:
        # A decoder can raise format-specific exceptions; avoid revealing file details.
        result={'error':'invalid_media'}
    finally:
        if fd is not None:os.close(fd)
    send_json(sock,result,4096)

if __name__=='__main__':
    with socket.socket(fileno=0) as connection:
        connection.settimeout(20)
        handle(connection)
