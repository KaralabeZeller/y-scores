import socket
import struct
import types
import unittest
from unittest.mock import patch
from local_access import lan_addresses,admin_access


class AccessTests(unittest.TestCase):
    def test_only_connected_physical_ipv4_addresses_are_advertised(self):
        interfaces=[(1,'lo'),(2,'eth0'),(3,'wlan0'),(4,'eth1'),(5,'docker0')]
        addresses={'eth0':'192.168.1.25','wlan0':'192.168.1.25'}
        def ioctl(fd,action,request):
            name=request.split(b'\0')[0].decode()
            if action==0x8913:return b'\0'*16+struct.pack('H',1 if name=='eth1' else 0x41)+b'\0'*22
            return b'\0'*20+socket.inet_aton(addresses[name])+b'\0'*16
        def physical(path):return path.parent.name in ('eth0','eth1','wlan0')
        with patch.dict('sys.modules',{'fcntl':types.SimpleNamespace(ioctl=ioctl)}),patch('local_access.socket.if_nameindex',return_value=interfaces),patch('local_access.socket.socket'),patch('local_access.Path.exists',physical):
            self.assertEqual(lan_addresses(),['192.168.1.25'])
    def test_missing_network_does_not_break_setup(self):
        with patch.dict('sys.modules',{'fcntl':types.SimpleNamespace()}),patch('local_access.socket.if_nameindex',side_effect=OSError('Unavailable')):
            self.assertEqual(lan_addresses(),[])
    def test_numeric_addresses_use_actual_port_and_keep_hostname_optional(self):
        with patch('local_access.lan_addresses',return_value=['192.168.1.25','192.168.4.1']),patch('local_access.socket.gethostname',return_value='board'):
            result=admin_access('0.0.0.0',8081)
        self.assertEqual(result['urls'],['http://192.168.1.25:8081/','http://192.168.4.1:8081/'])
        self.assertEqual(result['hostnameUrl'],'http://board.local:8081/')
    def test_loopback_only_server_does_not_advertise_lan_access(self):
        with patch('local_access.lan_addresses') as addresses:
            result=admin_access('127.0.0.1',18080)
        addresses.assert_not_called()
        self.assertEqual(result,dict(urls=['http://127.0.0.1:18080/'],hostnameUrl=None))

if __name__=='__main__':unittest.main()
