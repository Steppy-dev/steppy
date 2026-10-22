"""Create minimal dummy screenshots for the fixture app."""

import json
import struct
import zlib
from pathlib import Path


def minimal_png(width: int = 4, height: int = 4) -> bytes:
    """Generate a minimal valid PNG file."""
    sig = b"\x89PNG\r\n\x1a\n"

    def chunk(ctype, data):
        c = ctype + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)

    ihdr = chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
    raw_rows = b""
    for _ in range(height):
        raw_rows += b"\x00" + b"\xcc\xcc\xcc" * width
    idat = chunk(b"IDAT", zlib.compress(raw_rows))
    iend = chunk(b"IEND", b"")
    return sig + ihdr + idat + iend


def main():
    base = Path(__file__).parent / "output" / "screenshots"
    sidecar_data = {
        "viewport": {"width": 1280, "height": 720},
        "target": {
            "kind": "single",
            "bbox": {"x": 100, "y": 200, "width": 50, "height": 30},
        },
    }

    png_data = minimal_png()

    for slug in ["onboarding", "invoices"]:
        d = base / slug
        d.mkdir(parents=True, exist_ok=True)
        for i in range(1, 3):
            (d / f"step-{i:02d}.png").write_bytes(png_data)
            (d / f"step-{i:02d}.json").write_text(json.dumps(sidecar_data))

    print("Dummy screenshots created.")


if __name__ == "__main__":
    main()
