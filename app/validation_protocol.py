"""Bounded socket protocol: one read-only descriptor, never paths or commands."""
import array
import fcntl
import json
import os
import socket
import stat
import struct

MIMES={'.jpg':'image/jpeg','.jpeg':'image/jpeg','.png':'image/png','.webp':'image/webp',
       '.heic':'image/heic','.heif':'image/heif','.mp4':'video/mp4','.m4v':'video/mp4','.mov':'video/quicktime'}

def read_exact(sock,count):
    data=bytearray()
    while len(data)<count:
        part=sock.recv(count-len(data))
        if not part:raise ValueError('truncated_message')
        data.extend(part)
    return bytes(data)

def send_json(sock,value,limit):
    raw=json.dumps(value,separators=(',',':')).encode()
    if len(raw)>limit:raise ValueError('message_too_large')
    sock.sendall(struct.pack('!I',len(raw))+raw)

def receive_json(sock,limit):
    size=struct.unpack('!I',read_exact(sock,4))[0]
    if not 0<size<=limit:raise ValueError('message_too_large')
    return json.loads(read_exact(sock,size))

def send_request(sock,fd,extension,operation='validate'):
    rights=array.array('i',[fd])
    if sock.sendmsg([b'F'],[(socket.SOL_SOCKET,socket.SCM_RIGHTS,rights)])!=1:
        raise OSError('descriptor_send_failed')
    send_json(sock,{'extension':extension,'operation':operation},256)

def receive_request(sock):
    descriptors=[]
    try:
        data,ancillary,flags,_=sock.recvmsg(1,socket.CMSG_SPACE(8*array.array('i').itemsize))
        for level,kind,payload in ancillary:
            if level==socket.SOL_SOCKET and kind==socket.SCM_RIGHTS:
                values=array.array('i');values.frombytes(payload[:len(payload)//values.itemsize*values.itemsize])
                descriptors.extend(values)
        if data!=b'F' or flags & socket.MSG_CTRUNC or len(descriptors)!=1:
            raise ValueError('expected_one_descriptor')
        fd=descriptors[0]
        os.set_inheritable(fd,False)
        metadata=receive_json(sock,256)
        if not isinstance(metadata,dict) or set(metadata)!={'extension','operation'}:
            raise ValueError('invalid_request')
        ext=metadata['extension'];operation=metadata['operation']
        if not isinstance(ext,str) or ext not in MIMES or operation not in ('validate','isolation-check'):
            raise ValueError('invalid_request')
        info=os.fstat(fd)
        limit=250_000_000 if ext in ('.mp4','.mov','.m4v') else 30_000_000
        if not stat.S_ISREG(info.st_mode) or not 0<info.st_size<=limit:
            raise ValueError('invalid_descriptor')
        if fcntl.fcntl(fd,fcntl.F_GETFL) & os.O_ACCMODE != os.O_RDONLY:
            raise ValueError('descriptor_must_be_read_only')
        os.lseek(fd,0,os.SEEK_SET)
        descriptors.clear()
        return fd,ext,operation
    finally:
        for fd in descriptors:os.close(fd)
