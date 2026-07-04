#!/usr/bin/env pwsh
<#
.SYNOPSIS
  Smoke test: compile, unit tests, safe-mode run, and citation check.
.DESCRIPTION
  Runs the no-secret pipeline path end-to-end without touching the Google API:
    1. compileall on main.py + src
    2. pytest unit tests (no live Google calls)
    3. safe-mode scrape -> clean -> delta over 3 articles (no uploads)
    4. citation check: every Markdown file under data/markdown ends with an
       "Article URL:" line, and YAML front matter has the required fields.
  Exits non-zero if any step fails. Uses a local venv if present.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$repo = $PSScriptRoot | Split-Path -Parent
Set-Location $repo

# Pick a Python interpreter: prefer a local venv, else fall back to `python`.
$py = Join-Path $repo "venv\Scripts\python.exe"
if (-not (Test-Path $py)) { $py = "python" }

function Step($label, $script) {
    Write-Host "`n=== $label ===" -ForegroundColor Cyan
    & $script
    if ($LASTEXITCODE -ne 0) { Write-Host "FAIL: $label" -ForegroundColor Red; exit $LASTEXITCODE }
}

# Clean the run outputs so the safe-mode run actually exercises scrape->clean.
Remove-Item -Recurse -Force "data", "logs" -ErrorAction SilentlyContinue

Step "1/4 compileall" { & $py -m compileall -q main.py src }
Step "2/4 pytest"     { & $py -m pytest -q -p no:cacheprovider --basetemp=.pytest_tmp }
Step "3/4 safe-mode run (--limit 3)" { & $py main.py --safe-mode --limit 3 }

# 4. Citation + front-matter check over the generated Markdown files.
Step "4/4 citation + front-matter check" {
    & $py -c @"
import os, re, sys
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
"@
}

Write-Host "`nSMOKE OK" -ForegroundColor Green
