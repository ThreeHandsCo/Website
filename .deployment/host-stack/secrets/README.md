# Host-only secrets

The active stack expects these files in this directory:

- `github_deploy_key` — dedicated read-only SSH private key for
  `ThreeHandsCo/Website`, mode 600
- `github_known_hosts` — verified GitHub SSH host key, mode 644

Never copy either live file into the repository. The parent `.gitignore`
excludes everything in this directory except this README.

The Tailscale auth key is not a file. Supply it through the shell environment
only during first enrollment, unset it immediately, and revoke it afterward.
