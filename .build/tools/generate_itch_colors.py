#!/usr/bin/env python3
"""Generate the public itch embed color configuration from game thumbnails."""

from __future__ import annotations

import argparse
import colorsys
import difflib
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any
from urllib.parse import urljoin
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = ROOT / ".build" / "itch-games.json"
OUTPUT_PATH = ROOT / "assets" / "itch-embed-colors.js"
HTML_PATH = ROOT / "index.html"
HEX_COLOR = re.compile(r"^[0-9A-F]{6}$")
HISTOGRAM_COLOR = re.compile(
    r"^\s*(?P<count>\d+):\s*\(\s*(?P<red>\d+),\s*(?P<green>\d+),\s*(?P<blue>\d+)(?:,\s*\d+)?\)"
)
USER_AGENT = "Mozilla/5.0 (compatible; ThreeHandsItchColorGenerator/1.0; +https://threehands.dev/)"
REQUEST_INTERVAL_SECONDS = 1.0
last_request_at = 0.0


class OpenGraphParser(HTMLParser):
    """Extract only the image URL needed from a public itch game page."""

    def __init__(self) -> None:
        super().__init__()
        self.image_url: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "meta" or self.image_url:
            return

        attributes = {name.lower(): value for name, value in attrs}
        if attributes.get("property", "").lower() == "og:image":
            self.image_url = attributes.get("content")


def fail(message: str) -> None:
    raise RuntimeError(message)


def fetch(url: str) -> bytes:
    global last_request_at

    for attempt in range(3):
        delay = REQUEST_INTERVAL_SECONDS - (time.monotonic() - last_request_at)
        if delay > 0:
            time.sleep(delay)

        request = Request(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
            },
        )
        try:
            with urlopen(request, timeout=30) as response:
                last_request_at = time.monotonic()
                content_length = response.headers.get("Content-Length")
                if content_length and int(content_length) > 20_000_000:
                    fail(f"refusing oversized response from {url}")

                body = response.read(20_000_001)
                if len(body) > 20_000_000:
                    fail(f"refusing oversized response from {url}")
                return body
        except HTTPError as error:
            last_request_at = time.monotonic()
            if error.code == 429 and attempt < 2:
                retry_after = error.headers.get("Retry-After")
                delay = int(retry_after) if retry_after and retry_after.isdigit() else 5 * (attempt + 1)
                time.sleep(delay)
                continue
            fail(f"could not fetch {url}: HTTP {error.code}")
        except URLError as error:
            last_request_at = time.monotonic()
            if attempt < 2:
                time.sleep(5 * (attempt + 1))
                continue
            fail(f"could not fetch {url}: {error.reason}")
    raise AssertionError("unreachable")


def discover_thumbnail_url(page_url: str) -> str:
    parser = OpenGraphParser()
    parser.feed(fetch(page_url).decode("utf-8", errors="replace"))
    if not parser.image_url:
        fail(f"no og:image found on {page_url}")
    return urljoin(page_url, parser.image_url)


def image_converter() -> str:
    for command in ("convert", "magick"):
        if shutil.which(command):
            return command
    fail("ImageMagick is required: install a package that provides convert or magick")
    raise AssertionError("unreachable")


def histogram(image: bytes) -> list[tuple[int, int, int, int]]:
    """Return deterministic quantized RGB histogram entries as count, R, G, B."""

    with tempfile.NamedTemporaryFile(suffix=".img") as source:
        source.write(image)
        source.flush()
        command = [
            image_converter(),
            source.name,
            "-auto-orient",
            "-alpha",
            "remove",
            "-background",
            "#000000",
            "-flatten",
            "-resize",
            "200x200>",
            "-colorspace",
            "sRGB",
            "-dither",
            "None",
            "-colors",
            "32",
            "-depth",
            "8",
            "-format",
            "%c",
            "histogram:info:-",
        ]
        completed = subprocess.run(command, check=False, capture_output=True, text=True, timeout=30)

    if completed.returncode:
        fail(f"ImageMagick could not inspect thumbnail: {completed.stderr.strip()}")

    colors: list[tuple[int, int, int, int]] = []
    for line in completed.stdout.splitlines():
        match = HISTOGRAM_COLOR.match(line)
        if match:
            colors.append(
                (
                    int(match.group("count")),
                    int(match.group("red")),
                    int(match.group("green")),
                    int(match.group("blue")),
                )
            )

    if not colors:
        fail("ImageMagick returned no RGB histogram colors")
    return colors


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(value, high))


def thumbnail_accent(image: bytes) -> str:
    """Choose a vivid, readable thumbnail-derived accent for a black embed."""

    colors = histogram(image)
    total = sum(count for count, *_ in colors)
    hls_colors = []

    for count, red, green, blue in colors:
        hue, lightness, saturation = colorsys.rgb_to_hls(red / 255, green / 255, blue / 255)
        hls_colors.append((count, red, green, blue, hue, lightness, saturation))

    near_white = sum(
        count
        for count, _, _, _, _, lightness, saturation in hls_colors
        if lightness >= 0.93 and saturation <= 0.16
    )
    if near_white / total >= 0.50:
        return "FFFFFF"

    candidates = [
        entry
        for entry in hls_colors
        if 0.15 <= entry[5] <= 0.93 and entry[6] >= 0.15
    ]

    if candidates:
        # Area favors actual cover identity; chroma favors a readable accent.
        selected = max(
            candidates,
            key=lambda entry: (
                (entry[0] / total) * (entry[6] ** 1.5),
                entry[0],
                entry[1],
                entry[2],
                entry[3],
            ),
        )
    else:
        visible = [entry for entry in hls_colors if 0.15 <= entry[5] <= 0.98]
        selected = max(visible or hls_colors, key=lambda entry: (entry[5], entry[0], entry[1], entry[2], entry[3]))

    _, _, _, _, hue, lightness, saturation = selected
    # Darkened 25% (strong) per request: keep HDR boost, lower base color brightness.
    normalized_lightness = clamp(lightness, 0.37, 0.55)
    normalized_saturation = clamp(saturation, 0.55, 0.88)
    red, green, blue = colorsys.hls_to_rgb(hue, normalized_lightness, normalized_saturation)
    return f"{round(red * 255):02X}{round(green * 255):02X}{round(blue * 255):02X}"


def require_color(value: Any, context: str) -> str:
    if not isinstance(value, str) or not HEX_COLOR.fullmatch(value.upper()):
        fail(f"{context} must be a six-digit uppercase-compatible hex color")
    return value.upper()


def load_manifest() -> dict[str, Any]:
    try:
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        fail(f"cannot load {MANIFEST_PATH.relative_to(ROOT)}: {error}")

    if manifest.get("schema_version") != 1 or not isinstance(manifest.get("games"), list):
        fail("unsupported itch color manifest")
    return manifest


def baseline_for(game: dict[str, Any], game_id: str) -> dict[str, str]:
    baseline = game.get("visual_baseline")
    if not isinstance(baseline, dict):
        fail(f"game {game_id} has no thumbnail profile; run --refresh --write")

    thumbnail_hash = baseline.get("thumbnail_sha256")
    if not isinstance(thumbnail_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", thumbnail_hash):
        fail(f"game {game_id} visual_baseline.thumbnail_sha256 must be a SHA-256 hash")

    return {
        "thumbnail_sha256": thumbnail_hash,
        "accent": require_color(baseline.get("accent"), f"game {game_id} visual_baseline.accent"),
        "source": baseline.get("source", "approved-thumbnail-baseline"),
    }


def resolve_game(game: dict[str, Any], refresh: bool) -> tuple[dict[str, str], bool]:
    game_id = game.get("id")
    page_url = game.get("page_url")
    if not isinstance(game_id, str) or not game_id.isdigit():
        fail("each game requires a numeric string id")
    if not isinstance(page_url, str) or not page_url.startswith("https://"):
        fail(f"game {game_id} requires an HTTPS page_url")

    baseline = baseline_for(game, game_id) if not refresh else game.get("visual_baseline")
    if not refresh:
        return (
            {
                "id": game_id,
                "fg": baseline["accent"],
                "link": baseline["accent"],
                "source": baseline["source"],
                "thumbnail_sha256": baseline["thumbnail_sha256"],
            },
            False,
        )

    cover_url = discover_thumbnail_url(page_url)
    cover = fetch(cover_url)
    cover_hash = hashlib.sha256(cover).hexdigest()
    current = baseline_for(game, game_id) if isinstance(baseline, dict) else None

    if current and current["thumbnail_sha256"] == cover_hash:
        accent = current["accent"]
        source = current["source"]
        changed = False
    else:
        accent = thumbnail_accent(cover)
        source = "derived-thumbnail"
        game["visual_baseline"] = {
            "thumbnail_sha256": cover_hash,
            "accent": accent,
            "source": source,
        }
        changed = True

    return (
        {
            "id": game_id,
            "fg": accent,
            "link": accent,
            "source": source,
            "thumbnail": cover_url,
            "thumbnail_sha256": cover_hash,
        },
        changed,
    )


def render_javascript(games: list[dict[str, str]]) -> str:
    public_games = {
        game["id"]: {"fg": game["fg"], "link": game["link"]}
        for game in games
    }
    payload = json.dumps(public_games, indent=2, sort_keys=True)
    return (
        "/* Generated by .build/tools/generate_itch_colors.py. Do not edit manually. */\n"
        "window.THREEHANDS_ITCH_EMBED_COLORS = Object.freeze(" + payload + ");\n"
    )


def validate_html(game_ids: list[str]) -> None:
    html = HTML_PATH.read_text(encoding="utf-8")
    itch_iframes = re.findall(r'<iframe\b[^>]*\bdata-itch-id="\d+"[^>]*>', html)
    found_ids = re.findall(r'\bdata-itch-id="(\d+)"', "\n".join(itch_iframes))

    if found_ids != game_ids:
        fail(f"index.html itch game IDs are {found_ids}, expected {game_ids}")
    for iframe in itch_iframes:
        if re.search(r'\s(?:src|data-src)\s*=', iframe):
            fail("index.html must hydrate itch embed URLs from generated config")
        if "fg_color=" in iframe or "link_color=" in iframe:
            fail("index.html still contains manually entered itch color query parameters")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="verify the generated browser config (default)")
    parser.add_argument("--write", action="store_true", help="write the generated browser config")
    parser.add_argument("--refresh", action="store_true", help="refresh public thumbnail profiles before writing")
    parser.add_argument("--report", action="store_true", help="print resolved thumbnail metadata")
    arguments = parser.parse_args()

    if arguments.refresh and not arguments.write:
        fail("--refresh changes thumbnail profiles; use it with --write")

    manifest = load_manifest()
    resolved = [resolve_game(game, arguments.refresh) for game in manifest["games"]]
    games = [game for game, _ in resolved]
    profile_changed = any(changed for _, changed in resolved)
    game_ids = [game["id"] for game in games]
    if len(game_ids) != len(set(game_ids)):
        fail("itch game IDs must be unique")

    validate_html(game_ids)
    expected = render_javascript(games)

    if arguments.report:
        print(json.dumps(games, indent=2))

    if arguments.write:
        if arguments.refresh and profile_changed:
            MANIFEST_PATH.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT_PATH.write_text(expected, encoding="utf-8")
        print(f"wrote {OUTPUT_PATH.relative_to(ROOT)}")
        return 0

    actual = OUTPUT_PATH.read_text(encoding="utf-8") if OUTPUT_PATH.exists() else ""
    if actual != expected:
        difference = "".join(
            difflib.unified_diff(
                actual.splitlines(keepends=True),
                expected.splitlines(keepends=True),
                fromfile=str(OUTPUT_PATH.relative_to(ROOT)),
                tofile="expected",
            )
        )
        fail(f"generated itch embed config is stale; run --write\n{difference}")

    print(f"itch colors verified for {len(games)} thumbnails")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1)
