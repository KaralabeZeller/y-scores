"""Bounded, unprivileged health probes. No network identities or raw errors."""
from pathlib import Path
import shutil
import math
import time


def age_ms(timestamp, now):
    return min(86400000, max(0, int((now-timestamp)*1000))) if timestamp else None


class Health:
    def __init__(self, state_path, root=Path('/'), clock=time.monotonic):
        self.path=state_path; self.root=root; self.clock=clock; self.started=clock()
    def read(self, path, limit=4096):
        with (self.root/path.lstrip('/')).open('rb') as source:
            return source.read(limit).decode('ascii').strip('\x00\n ')
    def sample(self):
        result={'applicationUptimeSeconds':int(self.clock()-self.started)}
        probes=(('systemUptimeSeconds',lambda:int(float(self.read('/proc/uptime').split()[0]))),
                ('cpuTemperatureCelsius',lambda:float(self.read('/sys/class/thermal/thermal_zone0/temp'))/1000),
                ('memoryAvailableBytes',lambda:int(next(line.split()[1] for line in self.read('/proc/meminfo').splitlines() if line.startswith('MemAvailable:')))*1024),
                ('storageAvailableBytes',lambda:shutil.disk_usage(self.path).free))
        for name, probe in probes:
            try:
                value=probe()
                if not math.isfinite(value) or value<0 and name!='cpuTemperatureCelsius': raise ValueError()
                if name=='cpuTemperatureCelsius' and not -40<=value<=150: raise ValueError()
                if name.endswith('UptimeSeconds') and value>315360000: raise ValueError()
                result[name]=value
            except (OSError,ValueError,UnicodeError,OverflowError,StopIteration,IndexError): result[name]=None
        # Firmware throttling cannot be inferred from temperature alone.
        result['throttled']=None
        return result
    def model(self):
        try: return self.read('/proc/device-tree/model',120)
        except (OSError,UnicodeError): return None
    def network(self):
        try:
            interfaces=list((self.root/'sys/class/net').iterdir())[:16]
            active=[item for item in interfaces if item.name!='lo' and (item/'operstate').read_text().strip()=='up']
            if any(not (item/'wireless').exists() for item in active): return 'ETHERNET'
            if active: return 'WIFI'
            return 'DISCONNECTED'
        except OSError: return 'UNKNOWN'
