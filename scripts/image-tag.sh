#!/usr/bin/env bash
# Tag for an image built from a folder of this repository: the short hash of the last commit that
# changed it. With uncommitted changes it gets "-dirty-" plus a hash of the folder's content, so the
# tag is the same every time it is computed for the same files (build and deploy must agree) and an
# untested build never takes a committed tag.
#   scripts/image-tag.sh rag
set -euo pipefail
dir="${1:?usage: image-tag.sh <folder>}"
tag=$(git log -1 --format=%h -- "$dir")
if [[ -n "$(git status --porcelain -- "$dir")" ]]; then
  content=$(git ls-files --cached --others --exclude-standard -- "$dir" | sort | xargs shasum | shasum | cut -c1-8)
  tag="${tag}-dirty-${content}"
fi
echo "$tag"
