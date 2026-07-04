# Agent Guide: Support KB Mini Clone

This repo is for the Support KB Mini-Clone take-home test. The goal is to ship a small, reviewable backend job in about 10 focused hours: scrape a vendor's support articles, normalize them to Markdown, upload changed documents to Google AI File Search via API, and run the whole pipeline as a daily scheduled job.

## Non-Negotiables

- Do not name the GitHub repo `optisigns`, `optibot`, `optisigns-bot`, or any obvious searchable variant.
- Do not hard-code secrets. Read the Google API key from `.env` as `GOOGLE_API_KEY`.
- Use Python 3.11.
- Use Google AI / Gemini as the AI provider.
- Preferred knowledge base display name: `optibot-support-kb` (an existing store name carried over from prior runs; do not rename without reason).
- Preferred assistant/chat configuration name: `Support KB Mini Clone`.
- Daily schedule: once per day at `02:00 UTC`.
- API upload is mandatory. Do not use UI drag-and-drop for ingestion.
- Docker must run the job once, then exit with code 0.
- Do not fabricate deployment URLs, log links, assistant screenshots, vector store IDs, or successful runs.

## Required Workflow

Every agent should follow this loop for meaningful work:

1. Spec
   - Read this file, the current `README.md`, and relevant code before editing.
   - Restate the concrete acceptance criteria for the current task.
   - Identify missing credentials or external dependencies early.
   - If Google AI API behavior is uncertain, verify against official Google AI docs before implementing.

2. Plan
   - Make a short implementation plan before changing files.
   - Prefer the smallest design that satisfies the grading rubric.
   - Choose existing repo patterns over new abstractions.
   - Keep the pipeline idempotent: a second run should skip unchanged articles.

3. Implement
   - Keep code readable for a 1-hour review.
   - Use `.env.sample` for configuration examples.
   - Store generated Markdown in `data/markdown/`.
   - Store article state in `data/state/articles_manifest.json` or SQLite.
   - Write last-run output to `logs/last_run.json`.
   - Log structured counts: scraped, generated, added, updated, skipped, uploaded, failed.
   - Add retries/backoff for network calls.
   - Comments should explain only non-obvious decisions.

4. Test
   - Run fast local checks before handing off.
   - At minimum, run Python syntax/import checks and the pipeline in a safe mode if credentials are missing.
   - With `GOOGLE_API_KEY`, verify that changed Markdown files upload via API.
   - Run the job twice: first run should upload deltas, second run should skip unchanged articles.
   - Test the sample question: `How do I add a YouTube video?`

5. Review
   - Inspect the diff before finalizing.
   - Confirm no secrets were written to files.
   - Confirm `README.md` is concise and includes setup, local run, Docker run, deployment schedule, logs placeholder/link, screenshot placeholder, scraping approach, and chunking/upload strategy.
   - Confirm the assistant answer style is grounded, concise, and cited.

## Target Project Structure

```text
.
|-- main.py
|-- requirements.txt
|-- Dockerfile
|-- .env.sample
|-- README.md
|-- AGENTS.md
|-- src/
|   |-- config.py
|   |-- scraper.py
|   |-- cleaner.py
|   |-- storage.py
|   |-- uploader_google.py
|   |-- assistant.py
|   `-- logger.py
|-- data/
|   |-- markdown/
|   `-- state/
`-- logs/
```

Use this structure unless the existing repo already has a better established pattern.

## Functional Spec

### Scrape and Clean

- Pull at least 30 articles from `https://support.optisigns.com`.
- Prefer Zendesk Help Center API if available, for example article listing endpoints under `/api/v2/help_center/.../articles.json`.
- Convert each article body to clean Markdown.
- Save each file as `data/markdown/<slug>.md`.
- Preserve headings, useful links, code blocks, lists, tables when reasonable, and article URLs.
- Remove navigation, duplicated UI text, ads, footers, and unrelated page chrome.
- Include metadata at the top of each Markdown file:

```yaml
---
title: "Article title"
source_url: "https://support.optisigns.com/..."
article_id: "123456"
updated_at: "2026-01-01T00:00:00Z"
content_hash: "sha256..."
---
```

- Include this plain citation line in every Markdown file:

```text
Article URL: https://support.optisigns.com/...
```

### Delta Detection

- Compute a stable hash from normalized Markdown content plus relevant metadata.
- Track article ID, slug, source URL, updated timestamp, content hash, local path, upload status, and Google File Search document/operation identifiers.
- On each run, classify articles as:
  - `added`: not present in manifest.
  - `updated`: present but hash changed.
  - `skipped`: present and hash unchanged.
- Upload only `added` and `updated` articles.

### Google AI Upload

- Use Google AI File Search, not OpenAI.
- Create or reuse a File Search store with display name `optibot-support-kb`.
- Persist the returned resource name such as `fileSearchStores/...` because Google may generate a unique name from the display name.
- Upload Markdown files via API to the File Search store.
- Use custom metadata when possible: `article_id`, `slug`, `source_url`, `content_hash`, `updated_at`.
- Use provider chunking unless an explicit chunking config is implemented. If chunk counts are not returned by the API, log an estimated chunk count based on the configured local chunking rule and document that in `README.md`.
- Recommended docs to verify during implementation:
  - https://ai.google.dev/gemini-api/docs/file-search
  - https://ai.google.dev/api/file-search/file-search-stores

### Assistant Behavior

Use this required system instruction exactly when configuring the assistant/chat behavior:

```text
You are OptiBot, the customer-support bot for OptiSigns.com.
• Tone: helpful, factual, concise.
• Only answer using the uploaded docs.
• Max 5 bullet points; else link to the doc.
• Cite up to 3 "Article URL:" lines per reply.
```

For Google AI, this likely means using a model call with `system_instruction` plus the File Search tool pointing at the File Search store. If an AI Studio "assistant" is configured manually for the screenshot, use the same instruction text and same uploaded knowledge base.

Expected answer style:

- For how-to questions, start with the OptiSigns portal path when the docs support it.
- Give 1 to 5 short bullets or short paragraphs.
- Mention important caveats, such as full YouTube URLs vs share links when relevant.
- End with up to 3 `Article URL:` citations from the uploaded Markdown files.
- For vague troubleshooting, ask clarifying questions before giving detailed steps.
- If the answer is not in the uploaded docs, say it was not found in the uploaded docs. Do not guess.
- Do not implement private billing/account access, OptiSigns login automation, device control, payment operations, or account-specific tools.

Sample target answer shape for `How do I add a YouTube video?`:

```text
Files/Assets -> + Create -> Apps -> YouTube.

- Paste the full YouTube video URL, name the asset, and save it.
- For Shorts, convert the URL from /shorts/ to /embed/ if the support doc says to do so.
- Assign the saved asset to a screen, playlist, or schedule.

Article URL: https://support.optisigns.com/...
```

## Runtime Contract

`python main.py` should:

1. Load config from environment.
2. Scrape article metadata and bodies.
3. Convert bodies to Markdown.
4. Save Markdown files.
5. Compare against the manifest.
6. Upload only changed files to Google AI File Search if `GOOGLE_API_KEY` is present and uploads are enabled.
7. Write `logs/last_run.json`.
8. Exit cleanly.

`docker run --env-file .env <image>` should run the same job once and exit 0.

## Environment Variables

Use these names unless there is a strong reason not to:

```text
GOOGLE_API_KEY=
GOOGLE_FILE_SEARCH_STORE_DISPLAY_NAME=optibot-support-kb
GOOGLE_FILE_SEARCH_STORE_NAME=
GEMINI_MODEL=gemini-2.5-flash
SUPPORT_BASE_URL=https://support.optisigns.com
ARTICLE_LIMIT=30
UPLOAD_ENABLED=true
LOG_LEVEL=INFO
```

`GOOGLE_FILE_SEARCH_STORE_NAME` is optional on the first run. After a store is created, persist the returned `fileSearchStores/...` resource name in state and mention it in logs.

## Deployment Notes

- Add deployment docs for at least one target: Render, Railway, Fly.io, or DigitalOcean.
- Render cron syntax for the desired schedule is `0 2 * * *`.
- The deployed job must run once per day, re-scrape, detect deltas, upload only changed articles, log counts, and exit.
- If deployment is not completed, leave a clear placeholder such as `Daily job logs: TODO after deployment`; do not invent a link.

## Acceptance Checklist

- At least 30 Markdown support articles exist under `data/markdown/`.
- Markdown files contain metadata and an `Article URL:` line.
- `python main.py` runs once and exits cleanly.
- A second run skips unchanged articles.
- Changed files upload through Google AI API.
- Logs include added, updated, skipped, uploaded, failed, and chunk counts or estimated chunk counts.
- `.env.sample` exists and contains no real key.
- `Dockerfile` works with `docker run --env-file .env <image>`.
- `README.md` is short and has all required setup/deployment/screenshot/log sections.
- Screenshot shows the assistant answering `How do I add a YouTube video?` with citations.
- No private OptiSigns login, billing access, or account automation is implemented.

## Review Priorities

When reviewing code in this repo, focus on:

- Scrape quality and Markdown cleanliness.
- Whether upload is actually API-based.
- Idempotency and delta behavior.
- Secret handling.
- Clear logs and manifest state.
- Small, understandable code.
- README accuracy.
