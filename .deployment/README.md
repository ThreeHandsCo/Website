# Three Hands website deployment and recovery runbook

Last verified: 2026-07-29

This is the canonical, secret-safe description of how `threehands.dev` is
hosted. It is intended to be sufficient to operate the current installation or
rebuild it on a replacement Linux host months later.

No credential values belong in this repository. The files under
[`host-stack/`](host-stack/) are reproducible templates; the active host copies
currently live at `/home/baba/threehands-website`.

## 1. What exists

| Component | Location | Visibility | Purpose |
| --- | --- | --- | --- |
| Website source | `ThreeHandsCo/Website` | Private GitHub repository | Static HTML, media, and this runbook |
| Mask | `ThreeHandsCo/Website-Mask` | Public GitHub repository | GitHub Pages custom-domain TLS and full-screen iframe |
| Host stack | `/home/baba/threehands-website` | Deployment host only | Compose, Nginx, Tailscale, and deploy-key files |
| Public URL | `https://threehands.dev` | Public | Address visitors use |
| Funnel origin | `https://threehands-website.tail220731.ts.net` | Public through Funnel | Actual website origin and health endpoint |

Current repositories:

- `https://github.com/ThreeHandsCo/Website` (private)
- `https://github.com/ThreeHandsCo/Website-Mask` (public)

Current container names:

- `threehands-site-sync`
- `threehands-tailscale`
- `threehands-website`

Current named volumes:

- `threehands-website_site-data` — disposable synchronized source cache
- `threehands-website_tailscale-state` — persistent and sensitive node identity

## 2. Architecture

### Content deployment path

```text
developer pushes main
        |
        v
private ThreeHandsCo/Website repository
        |
        | read-only SSH deploy key; poll every 30 seconds
        v
git-sync container
        |
        | atomic /data/root/current symlink in site-data volume
        v
Nginx container serving /data/root/current
```

`git-sync` uses a shallow checkout and atomically moves its `current` symlink
after a successful fetch. Nginx therefore sees either the previous complete
revision or the next complete revision, not a partially copied site.

### Visitor traffic path

```text
browser
  |
  | HTTPS for threehands.dev
  v
GitHub Pages (Website-Mask)
  |
  | full-viewport cross-origin iframe
  v
https://threehands-website.tail220731.ts.net
  |
  | Tailscale Funnel TLS on 443
  v
Tailscale sidecar network namespace
  |
  | proxy to http://127.0.0.1:80
  v
Nginx -> synchronized static files
```

The mask is **not** an HTTP reverse proxy. GitHub Pages serves a small HTML
document from `threehands.dev`; that document embeds the Funnel origin in an
iframe. Consequently:

- the address bar remains `threehands.dev`;
- the actual application has the Funnel origin's web origin;
- browser developer tools show a nested, cross-origin frame;
- SEO and cross-origin browser behavior differ from direct hosting;
- the direct Funnel URL is the correct place to isolate origin failures.

This arrangement exists because Tailscale Funnel issues certificates for its
`*.ts.net` hostname, not arbitrary custom domains. GitHub Pages supplies the
custom-domain certificate for `threehands.dev`.

## 3. Security boundaries and intentional choices

- No Compose service publishes a host port.
- Nginx shares the Tailscale service's network namespace through
  `network_mode: service:tailscale`.
- Tailscale uses `/dev/net/tun` and kernel networking; the host's own Tailscale
  client is not part of this deployment.
- The GitHub deploy key is read-only and dedicated to `ThreeHandsCo/Website`.
  A Personal Access Token is neither required nor desired.
- Nginx returns 404 for any path containing a dot-prefixed segment. This keeps
  `.git` and `.deployment` inaccessible from the origin.
- `site-data` contains a checkout and can be recreated. `tailscale-state`
  contains node identity and must be treated as a secret.
- Never commit, paste into an issue/chat, or back up in plaintext:
  `TS_AUTHKEY`, GitHub PATs, deploy private keys, or Tailscale state.
- Revoke a Tailscale enrollment key immediately after successful enrollment.
- Avoid `docker compose down -v`: it deletes the persisted Tailscale identity
  and forces re-enrollment.

## 4. Canonical host files

The templates in this directory mirror the active files:

```text
.deployment/host-stack/
├── compose.yaml
├── config/
│   ├── nginx.conf
│   └── serve.json
└── secrets/
    └── README.md
```

Active host layout:

```text
/home/baba/threehands-website/
├── compose.yaml
├── README.md
├── config/
│   ├── nginx.conf
│   └── serve.json
└── secrets/
    ├── github_deploy_key       # never commit
    └── github_known_hosts
```

When changing infrastructure, update both the canonical template and the live
copy, validate them, and commit the template. Do not put live credentials under
`.deployment` even though `.gitignore` excludes their expected paths.

## 5. Fresh-host prerequisites

The replacement host needs:

1. Linux with Docker Engine and the Docker Compose plugin.
2. `/dev/net/tun` available to containers.
3. Access to the `ThreeHandsCo` GitHub organization.
4. Access to the correct Tailscale tailnet and permission to create a Funnel
   node.
5. Tailscale Funnel enabled by tailnet policy for the enrolling user or tag.
6. Outbound HTTPS and SSH access to GitHub, Tailscale, and container registries.
7. The public DNS and GitHub Pages settings described in section 8.

The current images are:

- `registry.k8s.io/git-sync/git-sync:v4.7.1`
- `nginx:1.28-alpine`
- `tailscale/tailscale:latest`

Content deployment is automatic, but container-image updates are a separate
host concern. The current host has Watchtower, which is not defined by this
Compose project. A rebuilt host must not assume Watchtower exists.

Tailscale Funnel authorization is controlled by tailnet policy. A typical
policy fragment is shown below; merge the appropriate target into the existing
policy rather than replacing the whole policy:

```json
{
  "nodeAttrs": [
    {
      "target": ["autogroup:member"],
      "attr": ["funnel"]
    }
  ]
}
```

Use a narrower tag such as `tag:web` when the tailnet's ownership and ACL model
supports it. See `https://tailscale.com/kb/1223/funnel` for current policy
syntax.

## 6. Bootstrap or rebuild the Docker origin

### 6.1 Prepare the stack directory

From a trusted checkout of the private website repository:

```sh
install -d -m 700 /home/baba/threehands-website/secrets
install -d -m 755 /home/baba/threehands-website/config
install -m 644 .deployment/host-stack/compose.yaml /home/baba/threehands-website/compose.yaml
install -m 644 .deployment/host-stack/config/nginx.conf /home/baba/threehands-website/config/nginx.conf
install -m 644 .deployment/host-stack/config/serve.json /home/baba/threehands-website/config/serve.json
```

Use a different absolute stack path if required; Compose's `./config` and
`./secrets` mounts are relative to the directory containing `compose.yaml`.

### 6.2 Create the read-only GitHub deploy key

Generate a dedicated key on the deployment host:

```sh
cd /home/baba/threehands-website
ssh-keygen -t ed25519 -N '' -C 'threehands-website-deploy' -f secrets/github_deploy_key
chmod 600 secrets/github_deploy_key
```

Add `secrets/github_deploy_key.pub` to:

`ThreeHandsCo/Website` -> **Settings** -> **Deploy keys** -> **Add deploy key**

Use the title `threehands-website-deploy` and leave write access disabled. The
current repository has exactly this read-only deploy key.

Create and verify the known-hosts file:

```sh
ssh-keyscan -t ed25519 github.com > secrets/github_known_hosts
ssh-keygen -lf secrets/github_known_hosts
chmod 644 secrets/github_known_hosts
```

Compare the printed fingerprint with GitHub's published SSH host fingerprints
before proceeding:

`https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/githubs-ssh-key-fingerprints`

Never substitute a PAT into Compose. If the deploy key is lost, generate a new
one rather than trying to recover the old private key.

### 6.3 Enroll the Tailscale sidecar

Create a short-lived, one-off, **non-ephemeral** Tailscale auth key. If tags are
used, ensure tailnet policy permits that tag to use Funnel. Do not save the key
in `.env`, Compose, shell history, or this repository.

Enter it without displaying it, start the stack, and then remove it from the
shell environment:

```sh
cd /home/baba/threehands-website
read -rsp 'Tailscale auth key: ' TS_AUTHKEY
export TS_AUTHKEY
docker compose up -d
unset TS_AUTHKEY
docker compose up -d --force-recreate tailscale website
```

`TS_AUTH_ONCE=true` and the `tailscale-state` volume mean the key is needed only
for the first enrollment. Wait for the first `docker compose up` to enroll the
node and persist healthy state before running the force-recreate command. That
second command recreates the two network-coupled containers with an empty
`TS_AUTHKEY`, removing the enrollment key from Docker's stored container
environment while retaining the node identity in the named volume.

Revoke the auth key in the Tailscale admin console as soon as the recreated
stack is healthy. Confirm whether node-key expiry is disabled or record its
renewal policy.

The Compose hostname is `threehands-website`. Changing it can change the
`*.ts.net` certificate hostname and therefore requires updating the mask.

### 6.4 Validate the origin

```sh
cd /home/baba/threehands-website
docker compose config --quiet
docker compose ps
docker compose logs --tail=100 site-sync tailscale website
docker exec threehands-tailscale tailscale funnel status
docker exec threehands-site-sync git -C /data/root/current rev-parse HEAD
curl -fsS https://threehands-website.tail220731.ts.net/healthz
```

Expected state:

- `threehands-tailscale` is healthy;
- `threehands-website` is healthy;
- Funnel status maps `/` to `http://127.0.0.1:80`;
- `/healthz` prints `ok`;
- the synchronized commit equals `main` in `ThreeHandsCo/Website`.

The Nginx container starts only after the Tailscale health check succeeds. The
`site-sync` container keeps retrying indefinitely if GitHub is temporarily
unavailable.

## 7. How the origin configuration works

### `site-sync`

- Polls `git@github.com:ThreeHandsCo/Website.git`, branch `main`.
- Poll interval is 30 seconds.
- Uses depth 1 and no submodules.
- Writes to the `site-data` named volume.
- Publishes the complete checkout through `/data/root/current`.
- Retries indefinitely instead of taking down the last successful version.

### `website`

- Serves `/data/root/current` using Nginx.
- Exposes no host port.
- Provides `/healthz`.
- Rejects dot-paths and unknown files.
- Shares the Tailscale container's network namespace.

### `tailscale`

- Terminates Funnel HTTPS on port 443.
- Loads `config/serve.json` through `TS_SERVE_CONFIG`.
- Proxies `/` to Nginx at `http://127.0.0.1:80`.
- Persists identity in `tailscale-state`.
- Uses its own isolated node rather than the host's Tailscale identity.

## 8. GitHub Pages mask and DNS

The public `ThreeHandsCo/Website-Mask` repository contains:

- `index.html` — full-screen iframe pointing at the Funnel origin;
- `CNAME` — exactly `threehands.dev`;
- `README.md` — mask-specific operations.

Verified GitHub Pages settings:

- Source: branch `main`, path `/`
- Custom domain: `threehands.dev`
- Enforce HTTPS: enabled
- Build status: built

Verified DNS records for the apex:

```text
threehands.dev A     185.199.108.153
threehands.dev A     185.199.109.153
threehands.dev A     185.199.110.153
threehands.dev A     185.199.111.153
threehands.dev AAAA  2606:50c0:8000::153
threehands.dev AAAA  2606:50c0:8001::153
threehands.dev AAAA  2606:50c0:8002::153
threehands.dev AAAA  2606:50c0:8003::153
www.threehands.dev CNAME threehandsco.github.io
```

If the Funnel hostname changes, update all origin references in the mask's
`index.html` (iframe, favicon, and noscript link), verify the direct origin, and
then push the mask repository.

Do not remove the iframe's following permission policy:

```html
allow="autoplay; fullscreen; picture-in-picture"
```

Without `autoplay`, muted media inside the cross-origin application frame may
behave differently or fail to start in some browsers.

Validate both layers independently:

```sh
curl -fsS https://threehands-website.tail220731.ts.net/healthz
curl -fsSI https://threehands.dev/
```

Then use a real browser at mobile and desktop widths. A successful HTTP request
to the mask does not prove its cross-origin iframe loaded.

## 9. Normal deployment and revision verification

Normal content release:

```sh
git push origin main
```

Within approximately 30 seconds, `git-sync` should report the new remote hash
and move the `current` symlink. No Compose restart is needed for static content.

Verify the exact deployed revision:

```sh
docker logs --since 5m threehands-site-sync
docker exec threehands-site-sync git -C /data/root/current rev-parse HEAD
git ls-remote https://github.com/ThreeHandsCo/Website.git refs/heads/main
```

The first two commands work on the deployment host. The last command requires
GitHub authorization because the source repository is private.

After every user-visible release, verify:

1. `https://threehands-website.tail220731.ts.net/healthz`
2. The direct origin in a browser
3. `https://threehands.dev` in a browser
4. Mobile scrolling
5. Muted trailer autoplay without controls
6. Steam and itch.io placeholder-to-iframe fades

Do not use `networkidle` as the browser completion condition: the page contains
looping HLS/video media and intentionally maintains network activity. Use
`DOMContentLoaded`, fixed observation windows, and explicit element states.

## 10. Rollback

The preferred rollback is a new Git revert commit, preserving history:

```sh
git revert <bad-commit>
git push origin main
```

Watch `threehands-site-sync` logs and verify the deployed revision afterward.
Do not rewrite or force-push `main` merely to roll back static content.

If GitHub becomes unavailable, Nginx continues serving the last successful
checkout. Do not delete `site-data` during an outage.

For an infrastructure-template regression, restore the previous active
`compose.yaml` or config file from Git, run `docker compose config --quiet`, and
then run:

```sh
docker compose up -d
```

## 11. Credential rotation and identity recovery

### GitHub deploy key

1. Generate a replacement key at a temporary path.
2. Add the replacement public key to the private repository as read-only.
3. Replace `secrets/github_deploy_key` and set mode 600.
4. Restart only `site-sync`.
5. Confirm it fetches successfully.
6. Remove the old deploy key from GitHub.
7. Delete the obsolete private key securely.

```sh
docker compose restart site-sync
docker compose logs --tail=100 site-sync
```

### Tailscale enrollment key

Enrollment auth keys should be revoked immediately after use. Revoking the
enrollment key does not disconnect an already enrolled node because the node's
identity is persisted separately.

### Lost or corrupt Tailscale state

If `threehands-website_tailscale-state` is gone or unusable:

1. Stop the stack.
2. Remove the stale node from the Tailscale admin console if appropriate.
3. Remove only the broken Tailscale volume after confirming its name.
4. Create a new one-off, non-ephemeral auth key.
5. Start the stack with `TS_AUTHKEY` as described in section 6.3.
6. Revoke the auth key.
7. Verify the new Funnel hostname before touching the mask.

Do not blindly run `docker compose down -v`; it removes every project volume.

## 12. Backup and disaster recovery

What must be durable:

- private `Website` Git repository, including `.deployment`;
- public `Website-Mask` Git repository;
- DNS registrar access;
- GitHub organization access;
- Tailscale tailnet access.

What can be regenerated:

- `site-data` from the private Git repository;
- the read-only GitHub deploy key;
- the Tailscale node and `tailscale-state` through re-enrollment;
- all three containers from their images.

Backing up `tailscale-state` is optional. If backed up, encrypt it and restrict
access because it contains node identity. It is often safer to re-enroll a new
node during disaster recovery.

Recovery order:

1. Restore access to GitHub, Tailscale, and DNS.
2. Provision Docker and `/dev/net/tun` on a Linux host.
3. Copy the canonical host templates.
4. Create and register a new read-only deploy key.
5. Enroll a new Tailscale sidecar and verify its direct Funnel URL.
6. Start all services and verify the synchronized revision.
7. Update `Website-Mask` only if the Funnel hostname changed.
8. Verify `threehands.dev` in real mobile and desktop browsers.

## 13. Troubleshooting decision tree

### `threehands.dev` is down or blank

1. Open the Funnel origin directly.
2. If the origin works, inspect `Website-Mask`, GitHub Pages status, DNS, and the
   browser console for iframe errors.
3. If the origin fails, check Compose health and Funnel status.

```sh
docker compose ps
docker exec threehands-tailscale tailscale funnel status
docker compose logs --tail=200 tailscale website
```

### Origin works but content is stale

```sh
docker compose logs --tail=200 site-sync
docker exec threehands-site-sync git -C /data/root/current rev-parse HEAD
```

Check the deploy key, known-hosts file, GitHub availability, and the private
repository's `main` hash. Do not restart Nginx unless its health check fails;
static content updates do not require it.

### `site-sync` cannot authenticate

- Confirm the deploy key still exists in the GitHub repository settings.
- Confirm it is read-only.
- Confirm the mounted private key is mode 600 and not a `.pub` file.
- Confirm `github_known_hosts` matches GitHub's published fingerprint.
- Rotate the deploy key if its provenance is uncertain.

### Tailscale is unhealthy

- Confirm `/dev/net/tun` exists.
- Confirm `NET_ADMIN` and `NET_RAW` capabilities remain in Compose.
- Inspect Tailscale logs.
- Confirm the node still exists and is authorized in the tailnet.
- Confirm tailnet policy permits Funnel.

### Mask loads but video does not autoplay

- Confirm the mask iframe still has `allow="autoplay; ..."`.
- Confirm the videos remain `muted` and `playsinline`.
- Confirm the application is not restoring controls unless `play()` rejects.
- Test the direct Funnel origin to separate iframe-policy behavior from the app.

### Steam or itch.io cards pop in or remain on placeholders

- Confirm the embed shell receives `embed-ready` after the iframe `load` event.
- Confirm nearby deferred iframes receive `src` from `data-src`.
- Confirm third-party Steam/itch requests are not blocked.
- Do not replace all `data-src` attributes with eager `src` attributes.

## 14. Known limitations and future simplification

- The deployment host and its internet connection are a single point of
  failure.
- The GitHub Pages mask adds an extra document and cross-origin iframe.
- The mask is less ideal for SEO, analytics, accessibility debugging, and web
  platform integration than serving the application directly on its custom
  domain.
- Funnel and GitHub Pages are two separate availability dependencies.

A future migration to a conventional public reverse proxy, CDN, or static host
that can terminate TLS directly for `threehands.dev` would remove the mask
layer. Do not point `threehands.dev` directly at the Funnel host and expect a
valid custom-domain certificate without first changing the hosting/TLS design.
