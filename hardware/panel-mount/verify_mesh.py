"""Check exported STL dimensions and closed, consistently oriented manifold edges."""
from pathlib import Path
from collections import Counter
import struct
import sys

folder = Path(sys.argv[1])
parts = [('Pi5-corner-holder.stl', (84.5,69.5,8.4))] if '--pi5' in sys.argv else [('seam-link-print-4.stl', (28,12,2.4)), ('end-foot-print-2.stl',(100,48,14))]
for name, expected in parts:
    raw = (folder/name).read_bytes()
    count = struct.unpack_from('<I',raw,80)[0]
    assert len(raw) == 84 + 50*count
    triangles = [[tuple(round(a,5) for a in struct.unpack_from('<3f',raw,84+i*50+12+j*12)) for j in range(3)] for i in range(count)]
    vertices = [v for triangle in triangles for v in triangle]
    bounds = [max(v[a] for v in vertices)-min(v[a] for v in vertices) for a in range(3)]
    edges = Counter(tuple(sorted((triangle[i],triangle[(i+1)%3]))) for triangle in triangles for i in range(3))
    directed = Counter((triangle[i],triangle[(i+1)%3]) for triangle in triangles for i in range(3))
    assert all(n==2 for n in edges.values()), 'Mesh has open/non-manifold edges'
    assert all(directed[(a,b)]==directed[(b,a)] for a,b in directed), 'Inconsistent winding'
    assert all(abs(a-b)<0.01 for a,b in zip(bounds,expected)), (name,bounds)
    print(name, 'PASS', count, 'triangles, dimensions mm:', bounds)
