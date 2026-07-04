#!/usr/bin/env bash
# Smoke test: compile, unit tests, safe-mode run, and citation check.
# No-secret path — never touches the Google API.
#
# Usage: bash scripts/smoke.sh
set -euo pipefail

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo"

# Prefer a local venv if present, else fall back to `python`.
if [ -x "venv/Scripts/python.exe" ]; then
  py="venv/Scripts/python.exe"
elif [ -x "venv/bin/python" ]; then
  py="venv/bin/python"
else
  py="python"
fi

run() { "$@"; }
step() { echo; echo "=== $1 ==="; shift; run "$@"; }

# Clean run outputs so the safe-mode run actually exercises scrape->clean.
rm -rf data logs

step "1/4 compileall" "$py" -m compileall -q main.py src
step "2/4 pytest"     "$py" -m pytest -q -p no:cacheprovider --basetemp=.pytest_tmp
step "3/4 safe-mode run (--limit 3)" "$py" main.py --safe-mode --limit 3

step "4/4 citation + front-matter check" "$py" - <<'PY'
import os, sys
md_dir = 'data/markdown'
files = sorted(f for f in os.listdir(md_dir) if f.endswith('.md'))
assert files, 'no markdown files generated'
missing_url, missing_fm = [], []
required = ['title:', 'source_url:', 'article_id:', 'updated_at:', 'content_hash:']
for f in files:
    text = open(os.path.join(md_dir, f), encoding='utf-8').read()
    if 'Article URL:' not in text:
        missing_url.append(f)
    head = text.split('---', 2)
    fm = head[1] if len(head) >= 3 else ''
    if not all(k in fm for k in required):
        missing_fm.append(f)
if missing_url or missing_fm:
    print('citation missing:', missing_url)
    print('front matter missing fields:', missing_fm)
    sys.exit(1)
print(f'ok: {len(files)} files, all have Article URL + front matter')
PY

echo; echo "SMOKE OK"
