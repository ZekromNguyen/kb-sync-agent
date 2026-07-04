# kb-sync-agent

Daily OptiSigns support KB sync job: scrape public Zendesk articles, normalize
them to Markdown, detect deltas, and upload only changed files to a Google AI
File Search store for an OptiBot-style assistant.

## Setup

```bash
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.sample .env               # set GOOGLE_API_KEY
```

Important env vars:

```bash
GOOGLE_API_KEY=...
GOOGLE_FILE_SEARCH_STORE_DISPLAY_NAME=optibot-support-kb
GEMINI_MODEL=gemini-3-flash-preview
ARTICLE_LIMIT=30
UPLOAD_ENABLED=true
```

## Run Locally

```bash
python main.py
python -m src.assistant "How do I add a YouTube video?"
```

Safe-mode smoke run without Google upload:

```bash
python main.py --safe-mode --limit 5
```

Docker one-shot job:

```bash
docker build -t kb-sync-agent .
docker run --rm --env-file .env kb-sync-agent
```

The container runs `python main.py` once and exits. Secrets are read from env;
no keys are committed.

## What It Does

- Scrapes published articles from `support.optisigns.com` through the Zendesk
  Help Center API.
- Converts each article to clean Markdown in `data/markdown/<slug>.md`.
- Preserves headings, links, lists, code blocks, images, front matter, and a
  trailing `Article URL:` citation line.
- Removes help-center chrome such as nav, footer, scripts, votes, comments,
  related widgets, and forms.
- Tracks article `content_hash` in `data/state/articles_manifest.json`.
- Uploads only `added` and `updated` articles to Google AI File Search.

## Google AI / RAG

Provider: Google AI / Gemini File Search.

Store display name: `optibot-support-kb`.

Assistant system prompt:

```text
You are OptiBot, the customer-support bot for OptiSigns.com.
- Tone: helpful, factual, concise.
- Only answer using the uploaded docs.
- Max 5 bullet points; else link to the doc.
- Cite up to 3 "Article URL:" lines per reply.
```

Chunking strategy: Google File Search whitespace chunking with
`max_tokens_per_chunk=512` and `max_overlap_tokens=64`. The API does not return
exact chunk counts, so `logs/last_run.json` reports an estimated count.

Latest verified local run:

```json
{
  "scraped": 30,
  "generated": 30,
  "added": 0,
  "updated": 0,
  "skipped": 30,
  "uploaded": 4,
  "failed": 0,
  "estimated_chunk_count": 143
}
```

## Daily Job

Deployment target: GitHub Actions scheduled workflow.

- Schedule: `0 2 * * *` (02:00 UTC daily)
- Manual trigger: `workflow_dispatch`
- Workflow: `.github/workflows/daily-ingest.yml`
- Required secret: `GOOGLE_API_KEY`
- Optional public artifact secrets: `GIST_TOKEN`, `GIST_ID`

Logs and artifacts:

- GitHub Actions run: https://github.com/ZekromNguyen/kb-sync-agent/actions/runs/28701740169/job/85120809537
- Public Gist artifact: https://gist.github.com/ZekromNguyen/420e8039f32aea2a46bce6783374ca61

Each run uploads a `last-run` artifact containing:

```text
logs/last_run.json
data/state/articles_manifest.json
data/state/store.json
```

## Screenshot

Assistant sanity check for: `How do I add a YouTube video?`

![Assistant answer screenshot](screenshots/youtube-answer.png)

## Tests

```bash
pip install -r requirements-dev.txt
pytest -q
python -m compileall main.py src
```

Last local result: `27 passed`.
