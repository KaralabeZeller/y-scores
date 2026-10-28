"""Finite, stateless diagnostics in logical panel order (left to right)."""
import colorsys
import math

from PIL import Image, ImageDraw, ImageFont


STEPS = [(f'panel{i + 1}', 3) for i in range(3)] + [
    (name, 3) for name in ('red', 'green', 'blue', 'cyan', 'magenta', 'yellow', 'white', 'black')
] + [('hues', 8), ('grayscale', 5), ('checkerboard', 6), ('lines', 8)]
DURATION = sum(duration for _, duration in STEPS)
COLORS = dict(red=(255, 0, 0), green=(0, 255, 0), blue=(0, 0, 255),
              cyan=(0, 255, 255), magenta=(255, 0, 255), yellow=(255, 255, 0),
              white=(255, 255, 255), black=(0, 0, 0))


def status(elapsed):
    if elapsed is None or elapsed >= DURATION:
        return dict(active=False, phase=None, remaining=0)
    position = max(0, elapsed)
    for name, duration in STEPS:
        if position < duration:
            return dict(active=True, phase=name, remaining=math.ceil(DURATION - elapsed))
        position -= duration


def render(elapsed):
    phase = status(elapsed)['phase']
    image = Image.new('RGB', (192, 64))
    draw = ImageDraw.Draw(image)
    if phase and phase.startswith('panel'):
        panel = int(phase[-1]) - 1
        x = panel * 64
        draw.rectangle((x, 0, x + 63, 63), outline='white')
        draw.polygon([(x + 32, 3), (x + 27, 9), (x + 37, 9)], fill='white')
        draw.text((x + 3, 12), 'TOP', font=ImageFont.load_default(), fill='white')
        # Scale the bundled Pillow bitmap font for a clear panel number.
        number = Image.new('RGB', (10, 12))
        ImageDraw.Draw(number).text((1, 0), str(panel + 1), font=ImageFont.load_default(), fill='white')
        image.paste(number.resize((30, 36), Image.Resampling.NEAREST), (x + 17, 23))
    elif phase in COLORS:
        draw.rectangle((0, 0, 191, 63), fill=COLORS[phase])
    elif phase in ('hues', 'grayscale'):
        for x in range(192):
            # Identical gradients on every panel expose differences between panels.
            value = (x % 64) / 63
            color = (tuple(round(c * 255) for c in colorsys.hsv_to_rgb((value + elapsed / 8) % 1, 1, 1))
                     if phase == 'hues' else (round(value * 255),) * 3)
            draw.line((x, 0, x, 63), fill=color)
    elif phase == 'checkerboard':
        for y in range(64):
            for x in range(192):
                if (x + y + int(elapsed)) % 2 == 0:
                    draw.point((x, y), fill='white')
    elif phase == 'lines':
        x = int((elapsed - 52) * 24) % 192
        y = int((elapsed - 52) * 8) % 64
        draw.line((x, 0, x, 63), fill='white')
        draw.line((0, y, 191, y), fill='white')
    return image
