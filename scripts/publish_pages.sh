#!/usr/bin/env bash
# Publish the site/ folder to the gh-pages branch, which GitHub Pages serves
# at https://knaito.xyz. Used by the server's daily job and by the GitHub
# workflow (after code changes).
#
# Each publish replaces gh-pages with a single fresh commit, so the branch
# never grows. It uses the repository's own git credentials (the deploy key on
# the server, GITHUB_TOKEN in GitHub Actions); no extra token is needed.
set -euo pipefail
cd "$(dirname "$0")/.."

tmp=$(mktemp -d)
index=$(mktemp)
trap 'rm -rf "$tmp" "$index"' EXIT

cp -r site/. "$tmp/"
echo "knaito.xyz" > "$tmp/CNAME"    # custom domain for GitHub Pages
touch "$tmp/.nojekyll"              # serve files as they are

export GIT_INDEX_FILE="$index"
git --work-tree="$tmp" add -A
tree=$(git write-tree)
commit=$(GIT_AUTHOR_NAME="ews-news-bot" GIT_AUTHOR_EMAIL="41898282+github-actions[bot]@users.noreply.github.com" \
         GIT_COMMITTER_NAME="ews-news-bot" GIT_COMMITTER_EMAIL="41898282+github-actions[bot]@users.noreply.github.com" \
         git commit-tree "$tree" -m "Publish site $(date -u +%Y-%m-%dT%H:%MZ)")
git push --force --quiet origin "$commit:refs/heads/gh-pages"
echo "published site to gh-pages ($commit)"
