# CrystalX CoreView LCD for Windows
# Copyright (C) 2026 Abdul Moez (https://github.com/Abdul-Moez)
# SPDX-License-Identifier: GPL-3.0-or-later -- see the LICENSE file.
"""Draw assets/crystalx-lcd.ico: a tall case screen showing a clock and stats.

Run it again after changing the design; the .ico is committed so builds do
not need to regenerate it.
"""
import os

from PIL import Image, ImageDraw

SIZE = 256
HERE = os.path.dirname(os.path.abspath(__file__))


def draw():
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    # The case screen: a tall rounded panel with a purple-to-cyan glow.
    x0, y0, x1, y1 = 72, 12, 184, 244
    glow = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    g = ImageDraw.Draw(glow)
    for y in range(y0, y1):
        t = (y - y0) / (y1 - y0)
        g.line([(x0, y), (x1, y)], fill=(int(122 - 76 * t), int(92 + 105 * t), 255, 255))
    mask = Image.new("L", (SIZE, SIZE), 0)
    ImageDraw.Draw(mask).rounded_rectangle((x0, y0, x1, y1), 22, fill=255)
    img.paste(glow, (0, 0), mask)
    d.rounded_rectangle((x0, y0, x1, y1), 22, outline=(20, 22, 32, 255), width=8)
    # Clock bar near the top, three stat bars near the bottom.
    d.rounded_rectangle((90, 40, 166, 72), 10, fill=(255, 255, 255, 235))
    for i, width in enumerate((58, 44, 66)):
        top = 168 + i * 22
        d.rounded_rectangle((92, top, 92 + width, top + 12), 6, fill=(20, 22, 32, 210))
    return img


if __name__ == "__main__":
    path = os.path.join(HERE, "crystalx-lcd.ico")
    draw().save(path, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64),
                             (128, 128), (256, 256)])
    print("wrote", path)
