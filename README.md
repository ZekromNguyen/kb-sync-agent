# kb-mini-agent

A backend-only **support-knowledge-base ingestion job**: a daily run that
scrapes a Zendesk Help Center, converts articles to clean Markdown, detects
added / updated / skipped articles, and uploads **only the delta** to a Google
AI File Search store. A small local query helper answers grounded questions
against that store for testing. No chat UI - backend job only.

> Repo name is intentionally cryptic and brand-neutral.

## Setup

```bash
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.sample .env               # then add GOOGLE_API_KEY
```

## Run locally

```bash
# Full pipeline: scrape -> clean -> delta -> upload -> logs
python main.py

# Local sanity query against the uploaded store
python -m src.assistant "How do I add a YouTube video?"
```

Safe mode (no secrets) - still scrapes, cleans, classifies, writes Markdown +
manifest + logs, skips upload:

```bash
python main.py --safe-mode             # CLI flag, no env edits
python main.py --safe-mode --limit 5    # demo with just 5 articles
UPLOAD_ENABLED=false python main.py     # same thing via env
```

### Smoke check

A no-secret one-shot that compiles, runs unit tests, does a 3-article safe-mode
run, and verifies every generated Markdown file has front matter + an
`Article URL:` citation line:

```bash
bash scripts/smoke.sh        # macOS / Linux / Git Bash
pwsh scripts/smoke.ps1       # Windows PowerShell
```

### Evaluation against the live store

5 hand-picked canonical questions (see `eval/questions.yaml`), scored on
groundedness (cites an expected article) + topic coverage. Calls the Google
API - run after `python main.py` has populated the store:

```bash
pip install -r requirements-dev.txt   # adds pyyaml for the eval harness
python -m src.evaluate                # PASS/FAIL summary
python -m src.evaluate --raw          # also print each answer
python -m src.evaluate --limit 2
```

Artifacts (git-ignored, regenerated per run):

| Path | What |
|---|---|
| `data/markdown/<slug>.md` | cleaned article Markdown (front matter + `Article URL:`) |
| `data/state/articles_manifest.json` | per-article state: id, slug, hash, paths, upload status, Google op/doc IDs |
| `data/state/store.json` | persisted File Search store resource name (`fileSearchStores/...`) |
| `logs/last_run.json` | structured run summary (counts, store name, est. chunk count) |

## Docker

```bash
docker build -t kb-mini-agent .
docker run --env-file .env kb-mini-agent   # runs once, exits 0
```

## Tests

```bash
pip install -r requirements-dev.txt
pytest                      # focused unit tests (no live Google calls)
python -m compileall main.py src
```

## Scraping approach

- **Source:** Zendesk Help Center public API,
  `https://support.optisigns.com/api/v2/help_center/en-us/articles.json`.
- **Recency vs. coverage:** the list endpoint returns articles newest-first, so
  a small `ARTICLE_LIMIT` only covers recent docs. A configurable
  `SAMPLE_ARTICLE_IDS` (default: the dedicated YouTube-app article) is fetched
  via the single-article detail endpoint *first*, ahead of the listing, so the
  corpus always covers the canonical sample question regardless of recency.
- **Blank-body backfill:** the list endpoint occasionally returns a
  metadata-only record with an empty `body`. Any such record is transparently
  refetched via the single-article detail endpoint, so no Markdown file is ever
  blank.
- **Pagination:** follows Zendesk's `next_page` URL until `ARTICLE_LIMIT`
  published (non-draft) articles are collected.
- **Cleaning:** BeautifulSoup drops nav/footer/scripts/forms + Zendesk
  comment/vote/related widgets, resolves relative links/images against the
  help-center origin, then `markdownify` converts to Markdown. Each file gets
  YAML front matter (`title`, `source_url`, `article_id`, `updated_at`,
  `content_hash`) and a trailing `Article URL: ...` citation line.
- **Retries:** `tenacity` exponential backoff on connection/5xx errors.

## Chunking + upload strategy

- **Provider chunking.** Each upload sends a `chunking_config` with a
  `white_space_config` of `max_tokens_per_chunk=512`, `max_overlap_tokens=64`.
  Google AI File Search caps tokens-per-chunk at 512; 512 maximizes context per
  chunk while a 12.5% overlap (64 tokens) preserves context across boundaries.
- **Delta-only upload.** A sha256 over the rendered article body plus the
  `Article URL:` citation line (front matter excluded, since it embeds the hash
  itself) classifies each article as `added` / `updated` / `skipped` vs
  `articles_manifest.json`. Only `added` + `updated` are uploaded.
- **Retry of prior failures.** A `skipped` article whose previous upload never
  succeeded (failed / never tried) is retried, so a transient API error doesn't
  permanently orphan a doc. A successfully uploaded `skipped` article is not
  re-uploaded - the strict delta contract holds.
- **Updated-article replacement.** Google File Search has no in-place document
  replace. On an `updated` article the job uploads a fresh document first, then
  deletes the superseded one (`documents.delete` with `force=true` so its
  chunks go too). The old doc name is carried through `updated` via the
  manifest's `previous_document_name` field; if the delete fails the new
  upload still succeeds (a warning is logged) - stale chunks are then
  retrievable until the next run retries the delete.
- **Metadata.** Each uploaded doc carries `article_id`, `slug`, `source_url`,
  `content_hash`, `updated_at`.
- **Store reuse.** The job creates a File Search store (display name
  `optibot-support-kb`) on first run, persists its real resource name
  (`fileSearchStores/...`) to `data/state/store.json`, and reuses it on later
  runs.
- **Chunk count.** The API does not return an exact chunk count, so
  `logs/last_run.json` reports an **estimated** chunk count
  (`~ chars/4 / max_tokens_per_chunk`). Documented as estimated.

## Deployment (GitHub Actions - daily schedule)

Target: a **scheduled GitHub Actions** workflow, `.github/workflows/daily-ingest.yml`,
on `cron: "0 2 * * *"` (02:00 UTC daily) with a `workflow_dispatch` trigger for
manual runs.

Required repo **Actions secret**: `GOOGLE_API_KEY`. Optionally set
`GOOGLE_FILE_SEARCH_STORE_NAME` to pin a specific File Search store. Other env
vars are defaulted inside the workflow (article limit, model, chunk sizes,
upload enabled, log level).

State continuity: the workflow caches `data/state` and `logs` across runs
(per-run cache key with a prefix `restore-keys`), so each scheduled run sees
the previous run's manifest and store name - without it, a fresh state would
re-classify every article as `added` and re-upload all docs every run. Each run
also uploads `logs/last_run.json`, the manifest, and `store.json` as a
`last-run` workflow artifact (saved even on failure).

> Never commit real secrets. The secret is referenced via
> `${{ secrets.GOOGLE_API_KEY }}` only.

## Daily job logs

- **Daily job logs:** https://github.com/ZekromNguyen/kb-sync-agent/actions/runs/28701059139/job/85119025489
- **Last run artifact:** open the workflow run, download the `last-run`
  artifact, and inspect `logs/last_run.json`, `articles_manifest.json`, and
  `store.json` (`scraped`, `generated`, `added`, `updated`, `skipped`,
  `uploaded`, `failed`, `store_name`, `estimated_chunk_count`).
- **Optional public Gist artifact:** set repository secrets `GIST_TOKEN` and
  `GIST_ID`; each workflow run will publish sanitized `last-run.json`,
  `articles-manifest.json`, and `store.json` to that Gist.

## Assistant screenshot

- **Screenshot: TODO** - Run `python -m src.assistant "How do I add a YouTube
  video?"` (or the equivalent in AI Studio Playground) and capture the grounded
  answer + `Article URL:` citations. Placeholder until captured.

## Assistant behavior

The query helper (and any AI Studio assistant configured for the screenshot)
uses this required system instruction verbatim:

```
You are OptiBot, the customer-support bot for OptiSigns.com.
- Tone: helpful, factual, concise.
- Only answer using the uploaded docs.
- Max 5 bullet points; else link to the doc.
- Cite up to 3 "Article URL:" lines per reply.
```

If the answer is not in the uploaded docs, the assistant says so rather than
guessing. No login / billing / account automation is implemented.

## Notes

- Python 3.11. Google AI / Gemini. Store display name `optibot-support-kb`.
- No secrets are committed; `.env` is git-ignored.
