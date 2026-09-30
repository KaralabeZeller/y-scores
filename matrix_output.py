"""Map a logical 192x64 scoreboard to parallel ports or one three-panel chain."""
import os
import numpy as np

TOPOLOGIES = ('parallel', 'chain')


def dimensions(topology):
    if topology not in TOPOLOGIES:
        raise ValueError('Topology must be parallel or chain')
    return (64, 192, 6) if topology == 'parallel' else (192, 64, 2)


def create_matrix(topology, pinout):
    # Lazy imports allow desktop mapping tests without GPIO or Pi dependencies.
    import adafruit_blinka_raspberry_pi5_piomatter as p
    from adafruit_blinka_raspberry_pi5_piomatter.pixelmappers import simple_multilane_mapper
    planes = int(os.environ.get('Y_SCORES_COLOR_PLANES', '10'))
    temporal = int(os.environ.get('Y_SCORES_TEMPORAL_PLANES', '2'))
    if not 1 <= planes <= 10 or not 1 <= temporal <= min(4, planes):
        raise ValueError('Color planes must be 1-10; temporal planes must be 1-4 and no greater than color planes')
    width, height, lanes = dimensions(topology)
    geometry = p.Geometry(width=width, height=height, n_addr_lines=5,
                          n_planes=planes, n_temporal_planes=temporal, n_lanes=lanes,
                          map=simple_multilane_mapper(width, height, 5, lanes))
    framebuffer = np.zeros((height, width, 3), dtype=np.uint8)
    matrix = p.PioMatter(colorspace=p.Colorspace.RGB888Packed,
                        pinout=getattr(p.Pinout, pinout),
                        framebuffer=framebuffer, geometry=geometry)
    return matrix, framebuffer


def fill_framebuffer(framebuffer, image, brightness, topology, order, rotations):
    """Order/rotation refer to each port (parallel) or position along the chain."""
    width, height, _ = dimensions(topology)
    if image.size != (192, 64) or framebuffer.shape != (height, width, 3):
        raise ValueError('Unexpected scoreboard/framebuffer dimensions')
    if sorted(order) != [0, 1, 2] or len(rotations) != 3 or any(
            r not in (0, 90, 180, 270) for r in rotations):
        raise ValueError('Invalid panel mapping')
    if not np.isfinite(brightness) or not 0 < brightness <= 1:
        raise ValueError('Brightness must be greater than zero and at most one')
    for position, (tile, rotation) in enumerate(zip(order, rotations)):
        panel = image.crop((tile * 64, 0, tile * 64 + 64, 64)).rotate(rotation)
        pixels = np.rint(np.asarray(panel).astype(np.float32) * brightness).astype(np.uint8)
        if topology == 'parallel':
            framebuffer[position * 64:(position + 1) * 64, :, :] = pixels
        else:
            framebuffer[:, position * 64:(position + 1) * 64, :] = pixels
