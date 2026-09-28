"""The worker can submit a file but cannot start services or choose commands."""
import os
import socket
import time
from pathlib import Path
from .validation_protocol import MIMES,send_request,receive_json

def request_validation(path,socket_path,operation='validate',extension=None):
    ext=extension or Path(path).suffix.lower()
    if ext not in MIMES:raise ValueError('unsupported_extension')
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
    try:
        for attempt in range(3):
            try:
                with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as sock:
                    sock.settimeout(40)
                    sock.connect(str(socket_path))
                    send_request(sock,fd,ext,operation)
                    result=receive_json(sock,4096)
                break
            except (ConnectionResetError,BrokenPipeError,ValueError) as exc:
                # Accept=yes can briefly reject a new connection while the previous
                # instance exits. Never classify a missing response as bad media.
                if attempt==2:raise RuntimeError('validator_unavailable') from exc
                time.sleep(0.1*(attempt+1))
        if not isinstance(result,dict):raise RuntimeError('invalid_validator_response')
        if operation=='isolation-check':return result
        if result=={'mime':MIMES[ext]}:return result
        if result in ({'error':'invalid_media'},{'error':'validator_unavailable'}):return result
        raise RuntimeError('invalid_validator_response')
    finally:os.close(fd)
