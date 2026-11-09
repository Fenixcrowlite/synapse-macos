#!/usr/bin/env bash
# Manual one-command public publish, run ONLY as the GitHub owner on your own Mac.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
if ! command -v gh >/dev/null; then
  echo 'GitHub CLI is required: brew install gh' >&2
  exit 2
fi
if ! gh auth status >/dev/null 2>&1; then
  echo 'Sign into YOUR GitHub account first: gh auth login' >&2
  exit 2
fi
user="$(gh api user --jq '.login')"
if [[ "$user" != 'Fenixcrowlite' ]]; then
  echo "Authenticated as $user, expected Fenixcrowlite. Switch accounts before publishing." >&2
  exit 2
fi
if [[ ! -d .git ]]; then git init -b main; fi
# Only publish reviewed source; never add all local files, credentials or secrets.
git add .gitignore .github/workflows/ci.yml README.md LICENSE bootstrap.sh publish-public.sh client.json.example synapse_mac tests
if ! git diff --cached --quiet; then
  git commit -m 'feat: macOS Synapse agent with main and backup HTTPS channels'
fi
if gh repo view 'Fenixcrowlite/synapse-macos' >/dev/null 2>&1; then
  echo 'Repository already exists; inspecting visibility.'
  visibility="$(gh repo view 'Fenixcrowlite/synapse-macos' --json visibility --jq '.visibility')"
  if [[ "$visibility" != 'PUBLIC' ]]; then
    echo 'Existing repository is NOT PUBLIC. Refusing to change visibility automatically.' >&2
    exit 3
  fi
  if ! git remote get-url origin >/dev/null 2>&1; then
    git remote add origin 'https://github.com/Fenixcrowlite/synapse-macos.git'
  fi
  git push -u origin HEAD:main
else
  gh repo create Fenixcrowlite/synapse-macos --public --source . --remote origin --push
fi
printf 'Published: https://github.com/Fenixcrowlite/synapse-macos\n'
