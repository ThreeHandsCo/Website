#!/usr/bin/env python3
"""Regenerate assets/hdr-white.avif.

This is the HDR white used for the Steam and One-offs buttons (the unselected
rim and the selected fill). It is a solid white PQ/BT.2020 AVIF.

203 nits is SDR diffuse white. The asset encodes HDR_WHITE_NITS; lower the
value to reduce HDR intensity, raise it to increase it. The original asset
was 2x SDR white (406 nits).
"""
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "assets" / "hdr-white.avif"
SIZE = 64

REFERENCE_WHITE_NITS = 203.0
HDR_WHITE_NITS = 320.0  # slightly reduced from 406 nits (2x SDR white)


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


def main() -> None:
    code = round(pq_encode(HDR_WHITE_NITS) * 65535)
    with tempfile.TemporaryDirectory() as temporary:
        ppm = Path(temporary) / "hdr-white.ppm"
        data = bytearray(f"P6\n{SIZE} {SIZE}\n65535\n".encode("ascii"))
        for _ in range(SIZE * SIZE):
            for _ in range(3):
                data.extend(code.to_bytes(2, "big"))
        ppm.write_bytes(data)
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
                str(OUTPUT),
            ],
            check=True,
        )
    print(f"wrote {OUTPUT} at {HDR_WHITE_NITS:.0f} nits (SDR white {REFERENCE_WHITE_NITS:.0f} nits)")


if __name__ == "__main__":
    main()
