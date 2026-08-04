# Itch embed color generation

The itch.io embed text colors are generated at development time. They are not
maintained in `index.html`.

```sh
python3 .build/tools/generate_itch_colors.py --check
python3 .build/tools/generate_itch_colors.py --write
python3 .build/tools/generate_itch_colors.py --refresh --write
```

The generator fetches each game's public Open Graph cover image, fingerprints
it with SHA-256, and derives an accent color for a new or changed thumbnail.

The initial games include a `visual_baseline` tied to the exact hash of their
current thumbnail. That preserves the approved appearance exactly while the
underlying thumbnail is unchanged. This is intentional: several established
colors are editorial, high-contrast versions of the cover palette rather than
literal image pixels. When a thumbnail changes, the generator uses its
deterministic thumbnail algorithm instead of silently reusing the old baseline.

`--check` is offline: it validates the committed thumbnail profile, generated
JavaScript, and that `index.html` contains no manually entered itch `fg_color`
or `link_color` query values. `--write` regenerates the public config from that
profile. `--refresh --write` is the intentional networked update operation: it
re-discovers every public cover image, creates a deterministic derived profile
when a thumbnail changed, and regenerates the public config. This split keeps
routine checks reliable if itch.io rate-limits requests.

The source manifest and generator live in this dot-directory. Nginx blocks
dot-paths, so they are not served publicly.
