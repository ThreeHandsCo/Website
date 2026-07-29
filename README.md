# Three Hands website

This private repository contains the static site served at
[`threehands.dev`](https://threehands.dev/).

Pushing `main` deploys the site automatically through the host's `git-sync`
container, normally within 30 seconds.

The complete architecture, bootstrap procedure, operations, rollback, and
disaster-recovery instructions are in
[`.deployment/README.md`](.deployment/README.md). Secret-free copies of the
host configuration are stored beside that runbook.

## Important implementation details

- The custom domain is a GitHub Pages wrapper containing a full-screen iframe;
  it is not a reverse proxy.
- The iframe must retain its `allow="autoplay; fullscreen; picture-in-picture"`
  policy for the site's muted video playback.
- `index.html` deliberately limits HLS trailer resolution and buffering. The
  limits reduced 15-second mobile trailer traffic from about 68.6 MB to about
  5 MB.
- Steam and itch.io embeds use controlled lazy loading and load-event fades.
  Replacing `data-src` with `src` on every embed will make them all contend for
  bandwidth again.
- Deployment material lives under `.deployment/`. Nginx rejects dot-paths, so
  these files are not served by the Funnel origin.
