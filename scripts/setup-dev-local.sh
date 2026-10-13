#!/usr/bin/env bash
# Set up the local TypeWhisper-Dev build on a Mac, or bring it up to date.
#
# Run it from the dev-build-signing branch:
#
#   git fetch origin
#   git switch dev-build-signing
#   ./scripts/setup-dev-local.sh
#
# It updates the local branches to the fork, turns off automatic updates for the
# dev build (they replace it with the official release), offers to remove the
# official app, then builds, signs, installs and opens TypeWhisper-Dev.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
remote="${TYPEWHISPER_DEV_REMOTE:-origin}"
branches=(paste-keystroke-override dev-build-signing)
dev_bundle_id="com.typewhisper.mac.dev"
official_app="/Applications/TypeWhisper.app"
installed_app="$HOME/Applications/TypeWhisper-Dev.app"

log() {
  printf '[typewhisper-dev-setup] %s\n' "$*"
}

confirm() {
  local answer
  [[ -t 0 ]] || return 1
  read -r -p "$1 [y/N] " answer
  [[ "$answer" == [yY]* ]]
}

# Point each local branch at the fork. A branch with commits the fork does not
# have is kept as <branch>-backup-<timestamp> before it moves, so nothing is lost.
update_branches() {
  local current branch local_rev remote_rev backup
  current="$(git -C "$repo_root" branch --show-current)"

  if [[ -n "$(git -C "$repo_root" status --porcelain --untracked-files=no)" ]]; then
    log "error: the working tree has uncommitted changes; commit or stash them first"
    exit 1
  fi

  git -C "$repo_root" fetch "$remote"

  for branch in "${branches[@]}"; do
    remote_rev="$(git -C "$repo_root" rev-parse --verify --quiet "$remote/$branch" || true)"
    if [[ -z "$remote_rev" ]]; then
      log "error: $remote/$branch not found"
      exit 1
    fi

    local_rev="$(git -C "$repo_root" rev-parse --verify --quiet "refs/heads/$branch" || true)"
    if [[ "$local_rev" == "$remote_rev" ]]; then
      log "$branch is up to date"
      continue
    fi
    if [[ -n "$local_rev" ]] \
       && ! git -C "$repo_root" merge-base --is-ancestor "$local_rev" "$remote_rev"; then
      backup="$branch-backup-$(date +%Y%m%d-%H%M%S)"
      git -C "$repo_root" branch "$backup" "$local_rev"
      log "$branch has commits that $remote/$branch does not; kept them as $backup"
    fi

    if [[ "$branch" == "$current" ]]; then
      git -C "$repo_root" reset --hard "$remote_rev"
    else
      git -C "$repo_root" branch -f "$branch" "$remote_rev"
    fi
    log "$branch -> $remote/$branch ($(git -C "$repo_root" rev-parse --short "$remote_rev"))"
  done

  if [[ "$current" != "dev-build-signing" ]]; then
    git -C "$repo_root" switch dev-build-signing
  fi
}

check_signing_identity() {
  if security find-identity -v -p codesigning 2>/dev/null | grep -q '"Apple Development: '; then
    log "found an Apple Development signing identity"
    return
  fi
  log "warning: no Apple Development signing identity in this keychain"
  log "  Create one in Xcode > Settings > Accounts > Manage Certificates."
  log "  Without it the build is signed ad-hoc, and Accessibility must be granted again after every build."
  if ! confirm "Continue with ad-hoc signing?"; then
    exit 1
  fi
}

disable_automatic_updates() {
  defaults write "$dev_bundle_id" SUEnableAutomaticChecks -bool false
  defaults write "$dev_bundle_id" SUAutomaticallyUpdate -bool false
  log "turned off automatic updates for $dev_bundle_id"
}

offer_to_remove_official_app() {
  [[ -d "$official_app" ]] || return 0
  log "the official app is installed at $official_app"
  if ! confirm "Quit it and move it to the Trash?"; then
    log "kept $official_app; quit it before using TypeWhisper-Dev"
    return 0
  fi
  osascript -e 'tell application id "com.typewhisper.mac" to quit' >/dev/null 2>&1 || true
  for _ in {1..20}; do
    pgrep -f "$official_app/Contents/MacOS/" >/dev/null || break
    sleep 0.25
  done
  trash "$official_app"
  log "moved $official_app to the Trash"
}

update_branches
check_signing_identity
disable_automatic_updates
offer_to_remove_official_app

"$repo_root/scripts/build-dev-local.sh"
open "$installed_app"

cat <<EOF

[typewhisper-dev-setup] TypeWhisper-Dev is running. If this is the first setup on this Mac:
  1. Grant Microphone and Accessibility in System Settings > Privacy & Security.
  2. Choose a speech engine and model.
  3. Turn on the API server in Settings > Advanced.
  4. To bring over the official app's terms and corrections, run:
       $repo_root/scripts/copy-dictionary-to-dev.py --dry-run
     then again without --dry-run.
EOF
