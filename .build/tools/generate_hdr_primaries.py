#!/usr/bin/env python3
"""Regenerate assets/hdr-red.avif, hdr-green.avif, hdr-blue.avif.

Solid PQ/BT.2020 AVIF primaries used to fill the cursor-trail ghosts.
Each primary is encoded two stops above SDR white (812 nits): clearly HDR
against the black page without blooming at full ceiling brightness.

203 nits is SDR diffuse white. Two stops above it is 4x (812 nits).
"""
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SIZE = 32

REFERENCE_WHITE_NITS = 203.0
TRAIL_PRIMARY_NITS = 812.0  # two stops above SDR white


def pq_encode(nits: float) -> float:
    """Encode absolute luminance in nits to normalized SMPTE ST 2084."""
    m1 = 2610 / 16384
    m2 = 2523 / 32
    c1 = 3424 / 4096
    c2 = 2413 / 128
    c3 = 2392 / 128
    normalized = max(0.0, min(nits / 10000.0, 1.0))
    powered = normalized**m1
    return ((c1 + c2 * powered) / (1 + c3 * powered)) ** m2


def write_avif(ppm: Path, output: Path) -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "image2",
            "-i",
            str(ppm),
            "-frames:v",
            "1",
            "-c:v",
            "libaom-av1",
            "-still-picture",
            "1",
            "-crf",
            "0",
            "-cpu-used",
            "8",
            "-pix_fmt",
            "yuv444p10le",
            "-color_range",
            "pc",
            "-colorspace",
            "bt2020_ncl",
            "-color_primaries",
            "bt2020",
            "-color_trc",
            "smpte2084",
            str(output),
        ],
        check=True,
    )


def main() -> None:
    code = round(pq_encode(TRAIL_PRIMARY_NITS) * 65535)
    primaries = {
        "red": (code, 0, 0),
        "green": (0, code, 0),
        "blue": (0, 0, code),
    }
    for name, (r, g, b) in primaries.items():
        output = ROOT / "assets" / f"hdr-{name}.avif"
        with tempfile.TemporaryDirectory() as temporary:
            ppm = Path(temporary) / f"hdr-{name}.ppm"
            data = bytearray(f"P6\n{SIZE} {SIZE}\n65535\n".encode("ascii"))
            for _ in range(SIZE * SIZE):
                data.extend(r.to_bytes(2, "big"))
                data.extend(g.to_bytes(2, "big"))
                data.extend(b.to_bytes(2, "big"))
            ppm.write_bytes(data)
            write_avif(ppm, output)
        print(
            f"wrote {output.name} at {TRAIL_PRIMARY_NITS:.0f} nits "
            f"(SDR white {REFERENCE_WHITE_NITS:.0f} nits)"
        )


if __name__ == "__main__":
    main()
