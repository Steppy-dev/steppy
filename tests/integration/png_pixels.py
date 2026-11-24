"""Read the pixels of a screenshot, with the standard library only.

Playwright writes 8-bit RGB or RGBA PNGs without interlacing, which is all this
decodes. Pillow is not a dependency of Steppy or of its tests.
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

RGB = tuple[int, int, int]


class Png:
    """``png.pixel(x, y)`` is the ``(r, g, b)`` at that position."""

    def __init__(self, source: "bytes | Path"):
        data = source if isinstance(source, bytes) else Path(source).read_bytes()
        if data[:8] != b"\x89PNG\r\n\x1a\n":
            raise ValueError("not a PNG file")
        position, compressed = 8, b""
        while position < len(data):
            size, kind = struct.unpack(">I4s", data[position : position + 8])
            chunk = data[position + 8 : position + 8 + size]
            position += 12 + size
            if kind == b"IHDR":
                self.width, self.height, depth, colour, _, _, interlace = struct.unpack(">IIBBBBB", chunk)
            elif kind == b"IDAT":
                compressed += chunk
        channels = {2: 3, 6: 4}.get(colour)
        if depth != 8 or interlace or channels is None:
            raise ValueError(f"unsupported PNG (depth {depth}, colour type {colour}, interlace {interlace})")
        self._channels = channels
        self._rows = self._unfilter(zlib.decompress(compressed), self.width * channels, channels)

    def _unfilter(self, raw: bytes, stride: int, step: int) -> list[bytearray]:
        rows: list[bytearray] = []
        previous = bytearray(stride)
        position = 0
        for _ in range(self.height):
            kind = raw[position]
            line = bytearray(raw[position + 1 : position + 1 + stride])
            position += 1 + stride
            for i in range(stride):
                left = line[i - step] if i >= step else 0
                up = previous[i]
                corner = previous[i - step] if i >= step else 0
                if kind == 1:
                    add = left
                elif kind == 2:
                    add = up
                elif kind == 3:
                    add = (left + up) >> 1
                elif kind == 4:
                    to_left, to_up, to_corner = abs(up - corner), abs(left - corner), abs(left + up - 2 * corner)
                    if to_left <= to_up and to_left <= to_corner:
                        add = left
                    else:
                        add = up if to_up <= to_corner else corner
                else:
                    add = 0
                line[i] = (line[i] + add) & 0xFF
            rows.append(line)
            previous = line
        return rows

    def pixel(self, x: int, y: int) -> RGB:
        start = x * self._channels
        red, green, blue = self._rows[y][start : start + 3]
        return red, green, blue

    def colours(self, x: int, y: int, width: int, height: int) -> set[RGB]:
        """Every colour in a box, its one-pixel edge left out (anti-aliasing)."""
        return {
            self.pixel(column, row) for row in range(y + 1, y + height - 1) for column in range(x + 1, x + width - 1)
        }
