"""Local admin discovery, independent of DNS and the optional root network helper."""
import ipaddress
from pathlib import Path
import socket
import struct


def lan_addresses():
    try:
        import fcntl
    except ImportError:
        return []
    result=set()
    try:
        interfaces=socket.if_nameindex()[:32]
        with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as probe:
            for _,name in interfaces:
                if not (Path('/sys/class/net')/name/'device').exists():continue
                try:
                    request=struct.pack('256s',name.encode('ascii')[:15])
                    flags=struct.unpack_from('H',fcntl.ioctl(probe.fileno(),0x8913,request),16)[0]
                    if flags&0x41!=0x41:continue  # Interface up, with a working link.
                    raw=fcntl.ioctl(probe.fileno(),0x8915,request)
                    address=ipaddress.IPv4Address(raw[20:24])
                    if not address.is_loopback and not address.is_unspecified and not address.is_multicast:
                        result.add(str(address))
                except (OSError,ValueError,UnicodeError,struct.error):continue
    except OSError:
        pass
    return sorted(result,key=ipaddress.IPv4Address)


def admin_access(bind,port):
    address=ipaddress.IPv4Address(bind)
    addresses=lan_addresses() if address.is_unspecified else [str(address)]
    return dict(urls=[f'http://{ip}:{port}/' for ip in addresses],
                hostnameUrl=f'http://{socket.gethostname()}.local:{port}/' if address.is_unspecified else None)
