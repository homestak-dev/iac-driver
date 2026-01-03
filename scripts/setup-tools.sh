#!/bin/bash
# Setup or update tool repositories
# Usage: setup-tools.sh [base_dir]
#
# Clones ansible, tofu, and packer repos if they don't exist,
# or pulls latest changes if they do.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_DIR="${1:-$(dirname "$(dirname "$SCRIPT_DIR")")}"
GITHUB_USER="john-derose"

declare -A REPOS=(
  [ansible]="https://github.com/$GITHUB_USER/ansible.git"
  [tofu]="https://github.com/$GITHUB_USER/tofu.git"
  [packer]="https://github.com/$GITHUB_USER/packer.git"
)

echo "Setting up tool repositories in: $BASE_DIR"

for repo in "${!REPOS[@]}"; do
  target="$BASE_DIR/$repo"
  if [[ -d "$target/.git" ]]; then
    echo "Updating $repo..."
    git -C "$target" pull --ff-only
  else
    echo "Cloning $repo..."
    git clone "${REPOS[$repo]}" "$target"
  fi
done

echo "Done. Tool repositories are ready."
