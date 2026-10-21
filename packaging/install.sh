#!/usr/bin/env bash
# Install the checked-out revision as a release, point `current` at it and restart the user service.
# Usage: packaging/install.sh            (from a clean checkout of the revision to install)
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
sha="$(git -C "$root" rev-parse --short=12 HEAD)"
lib="$HOME/.local/lib/fleet"
mkdir -p "$lib/releases" "$HOME/.config/systemd/user"
git -C "$root" archive --format=tar --prefix="$sha/" HEAD | tar -x -C "$lib/releases"
ln -sfn "$lib/releases/$sha" "$lib/current"
install -m 0644 "$lib/current/packaging/fleet.service" "$HOME/.config/systemd/user/fleet.service"
test -f "$HOME/.config/fleet/config.toml" || { echo "missing ~/.config/fleet/config.toml (see config.example.toml)" >&2; exit 1; }
systemctl --user daemon-reload
systemctl --user enable --now fleet.service
systemctl --user restart fleet.service
echo "installed $sha"
