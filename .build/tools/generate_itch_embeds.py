#!/usr/bin/env python3
"""Generate same-origin itch cards and real HDR color swatches.

The thumbnail files are inputs. This script never rewrites them. It creates
small PQ/BT.2020 AVIF swatches for the generated accent colors so the static
cards can use an actual HDR image instead of relying on CSS out-of-range
colors, which browsers may clamp.
"""

from __future__ import annotations

import argparse
import html
import json
import math
from pathlib import Path
import re
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = ROOT / ".build" / "itch-games.json"
COLORS_PATH = ROOT / "assets" / "itch-embed-colors.js"
THUMBNAIL_DIR = ROOT / "assets" / "itch-thumbnails"
EMBED_DIR = ROOT / "assets" / "itch-embeds"
HDR_DIR = ROOT / "assets" / "itch-hdr"

REFERENCE_WHITE_NITS = 203.0
HDR_STOPS = 1
HDR_SCALE = 2**HDR_STOPS

# Linear sRGB -> XYZ D65, then XYZ -> Rec.2020.
SRGB_TO_XYZ = (
    (0.4123907993, 0.3575843394, 0.1804807884),
    (0.2126390059, 0.7151686788, 0.0721923154),
    (0.0193308187, 0.1191947798, 0.9505321522),
)
XYZ_TO_REC2020 = (
    (1.7166512, -0.3556708, -0.2533663),
    (-0.6666844, 1.6164812, 0.0157685),
    (0.0176399, -0.0427706, 0.9421031),
)


def parse_colors() -> dict[str, dict[str, str]]:
    text = COLORS_PATH.read_text(encoding="utf-8")
    match = re.search(
        r"Object\.freeze\((\{.*?\})\);", text, flags=re.DOTALL
    )
    if not match:
        raise RuntimeError(f"Could not parse {COLORS_PATH}")
    return json.loads(match.group(1))


def srgb_to_linear(channel: int) -> float:
    value = channel / 255.0
    if value <= 0.04045:
        return value / 12.92
    return ((value + 0.055) / 1.055) ** 2.4


def hex_to_linear_rec2020(value: str) -> tuple[float, float, float]:
    rgb = tuple(srgb_to_linear(int(value[offset : offset + 2], 16)) for offset in (0, 2, 4))
    xyz = tuple(sum(row[index] * rgb[index] for index in range(3)) for row in SRGB_TO_XYZ)
    rec2020 = tuple(
        max(0.0, sum(row[index] * xyz[index] for index in range(3)))
        for row in XYZ_TO_REC2020
    )
    return rec2020


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


def hdr_pq_channels(value: str) -> tuple[int, int, int]:
    channels = hex_to_linear_rec2020(value)
    # Treat SDR diffuse white as 203 nits, then add exactly two stops in
    # linear light. Each channel is encoded independently as PQ BT.2020.
    return tuple(
        round(pq_encode(channel * REFERENCE_WHITE_NITS * HDR_SCALE) * 65535)
        for channel in channels
    )


def write_ppm(path: Path, channels: tuple[int, int, int]) -> None:
    size = 16
    data = bytearray(f"P6\n{size} {size}\n65535\n".encode("ascii"))
    for _ in range(size * size):
        for channel in channels:
            data.extend(channel.to_bytes(2, "big"))
    path.write_bytes(data)


def make_hdr_swatch(color: str, output: Path, temporary: Path) -> None:
    ppm = temporary / f"{color}.ppm"
    write_ppm(ppm, hdr_pq_channels(color))
    command = [
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
    ]
    subprocess.run(command, check=True)


def relative_luminance(value: str) -> float:
    channels = tuple(srgb_to_linear(int(value[offset : offset + 2], 16)) for offset in (0, 2, 4))
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def readable_foreground(value: str) -> str:
    # The threshold is where black and white have equal WCAG contrast.
    return "000000" if relative_luminance(value) >= 0.179 else "FFFFFF"


def create_embed(game: dict[str, str], color: dict[str, str]) -> str:
    game_id = game["id"]
    fg = color["fg"]
    link = color["link"]
    button_text = readable_foreground(link)
    title = html.escape(game["title"])
    page_url = html.escape(game["page_url"], quote=True)
    fg_swatch = f"/assets/itch-hdr/{fg}.avif"
    link_swatch = f"/assets/itch-hdr/{link}.avif"
    thumbnail = f"/assets/itch-thumbnails/{game_id}.jpg"
    return f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link href="https://fonts.googleapis.com/css2?family=Press+Start+2P&display=swap" rel="stylesheet">
<style>
html {{ height:100%; background:#000; dynamic-range-limit:no-limit; }}
body {{ margin:0; padding:0; height:100%; background:#000; color:#{fg}; font-family:'Press Start 2P',cursive; dynamic-range-limit:no-limit; }}
a {{ text-decoration:none; }}
.embed {{ display:grid; grid-template-columns:175px minmax(0,1fr); gap:18px; width:100%; height:100%; min-height:175px; box-sizing:border-box; align-items:start; padding:14px; background:#000; overflow:hidden; }}
.thumb-frame {{ position:relative; width:175px; height:138px; box-sizing:border-box; background:#{fg}; overflow:hidden; }}
.thumb {{ position:absolute; inset:2px; background:#111 url('{thumbnail}') center/cover no-repeat; }}
.meta {{ min-width:0; min-height:0; width:100%; height:138px; align-self:start; display:flex; flex-direction:column; gap:10px; overflow:hidden; }}
.title {{ flex:1 1 auto; min-width:0; min-height:0; overflow:hidden; overflow-wrap:anywhere; white-space:normal; font-size:14px; line-height:1.15; font-weight:700; letter-spacing:.5px; color:#{fg}; }}
.link-frame {{ flex:0 0 auto; width:max-content; max-width:100%; box-sizing:border-box; margin-top:auto; padding:2px; background:#{link}; }}
.link {{ display:block; box-sizing:border-box; max-width:100%; padding:10px 14px; background:#{link}; color:#{button_text}; border:0; font-size:10px; line-height:1.4; letter-spacing:1px; text-align:center; text-transform:uppercase; white-space:normal; }}
.link:hover {{ filter:brightness(1.15); }}
/* Actual PQ/BT.2020 HDR swatches. Browsers that do not support HDR AVIF
   keep the generated SDR colors above; HDR-capable outputs tone-map this
   image correctly instead of clamping CSS color() values. */
@supports (background-image:url('{fg_swatch}')) {{
  .thumb-frame {{ background-image:url('{fg_swatch}'); background-size:cover; background-repeat:no-repeat; background-position:center; }}
}}
@supports (background-image:url('{link_swatch}')) {{
  .link-frame, .link {{ background-image:url('{link_swatch}'); background-size:cover; background-repeat:no-repeat; background-position:center; }}
}}
@supports ((-webkit-background-clip:text) or (background-clip:text)) and (background-image:url('{fg_swatch}')) {{
  .title {{ color:transparent; -webkit-text-fill-color:transparent; background-image:url('{fg_swatch}'); background-repeat:repeat; background-size:auto 100%; -webkit-background-clip:text; background-clip:text; }}
}}
@media (max-width:600px) {{
  .embed {{ grid-template-columns:38% minmax(0,1fr); gap:12px; min-height:100%; padding:12px; }}
  .thumb-frame {{ width:100%; height:auto; aspect-ratio:1.27; }}
  .link-frame {{ width:100%; }}
  .link {{ padding:10px 8px; font-size:10px; letter-spacing:.5px; }}
}}
</style>
</head>
<body>
<div class="embed">
  <div class="thumb-frame"><div class="thumb"></div></div>
  <div class="meta">
    <div class="title">{title}</div>
    <div class="link-frame"><a class="link" href="{page_url}" target="_blank" rel="noopener">VIEW ON ITCH.IO</a></div>
  </div>
</div>
<script>
(() => {{
  const embed = document.querySelector('.embed');
  const thumbFrame = document.querySelector('.thumb-frame');
  const meta = document.querySelector('.meta');
  const title = document.querySelector('.title');
  if (!embed || !thumbFrame || !meta || !title) return;

  const MIN_FONT_SIZE = 7;
  const MAX_FONT_SIZE = 48;

  function titleFits(size) {{
    title.style.fontSize = `${{size}}px`;
    return title.scrollWidth <= title.clientWidth + 1
      && title.scrollHeight <= title.clientHeight + 1;
  }}

  function fitTitle() {{
    if (!title.clientWidth || !title.clientHeight) return;
    let low = MIN_FONT_SIZE;
    let high = MAX_FONT_SIZE;
    let best = MIN_FONT_SIZE;
    while (high - low > 0.1) {{
      const middle = (low + high) / 2;
      if (titleFits(middle)) {{
        best = middle;
        low = middle;
      }} else {{
        high = middle;
      }}
    }}
    title.style.fontSize = `${{best}}px`;
  }}

  function syncEmbedLayout() {{
    const height = thumbFrame.getBoundingClientRect().height;
    if (height > 0) meta.style.height = `${{height}}px`;
    fitTitle();
  }}

  if ('ResizeObserver' in window) {{
    const observer = new ResizeObserver(syncEmbedLayout);
    observer.observe(embed);
    observer.observe(thumbFrame);
  }}
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(syncEmbedLayout);
  window.addEventListener('load', syncEmbedLayout, {{ once: true }});
  syncEmbedLayout();
}})();
</script>
</body>
</html>
'''


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    games = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))["games"]
    colors = parse_colors()
    EMBED_DIR.mkdir(parents=True, exist_ok=True)
    HDR_DIR.mkdir(parents=True, exist_ok=True)
    unique_colors: set[str] = set()
    for game in games:
        game_id = game["id"]
        if not (THUMBNAIL_DIR / f"{game_id}.jpg").is_file():
            raise RuntimeError(f"Missing thumbnail for {game_id}")
        color = colors[game_id]
        unique_colors.add(color["fg"])
        unique_colors.add(color["link"])
        if not args.check:
            (EMBED_DIR / f"{game_id}.html").write_text(
                create_embed(game, color), encoding="utf-8"
            )
    if not args.check:
        with tempfile.TemporaryDirectory() as temporary:
            temporary_path = Path(temporary)
            for color in sorted(unique_colors):
                make_hdr_swatch(color, HDR_DIR / f"{color}.avif", temporary_path)
    expected = [
        EMBED_DIR / f"{game['id']}.html" for game in games
    ] + [HDR_DIR / f"{color}.avif" for color in unique_colors]
    missing = [str(path.relative_to(ROOT)) for path in expected if not path.is_file()]
    if missing:
        raise SystemExit("Missing generated files: " + ", ".join(missing))
    print(f"validated {len(games)} embeds and {len(unique_colors)} HDR swatches")


if __name__ == "__main__":
    main()
