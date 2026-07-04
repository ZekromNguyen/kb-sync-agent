"""OptiBot Mini-Clone daily job.

Pipeline: scrape Zendesk -> clean to Markdown -> detect deltas -> upload
changed files to Google AI File Search -> write logs/last_run.json -> exit.

Safe mode: without GOOGLE_API_KEY (or with UPLOAD_ENABLED=false) the job still
scrapes, cleans, classifies, and writes Markdown + manifest + logs; it just
skips the upload step. This is the no-secret smoke-test path.
"""
from __future__ import annotations

import os
import traceback

from src.config import load_config
from src.logger import setup_logging, write_last_run
from src.scraper import scrape_articles
from src.cleaner import build_markdown, render_body, slugify, estimate_chunk_count
from src import storage
from src import uploader_google

MD_DIR = os.path.join("data", "markdown")


def run() -> dict:
    cfg = load_config()
    log = setup_logging(cfg.log_level)
    log.info("starting run upload_enabled=%s article_limit=%s can_upload=%s",
             cfg.upload_enabled, cfg.article_limit, cfg.can_upload)

    manifest = storage.load_manifest()
    # Map slug -> article_id so each article keeps a stable filename across
    # runs; only genuine duplicate slugs get an -<article_id> suffix (below).
    slug_to_id = {e.get("slug"): aid for aid, e in manifest.items()}

    scraped = generated = 0
    added = updated = skipped = 0
    to_upload: list[int] = []          # delta set: added + updated only
    retry_failed: list[int] = []       # separate: skipped articles whose prior
                                       # upload never succeeded (transient API
                                       # errors must not permanently orphan docs)
    estimated_total_chunks = 0

    try:
        for article in scrape_articles(
            cfg.support_base_url,
            cfg.article_limit,
            sample_article_ids=cfg.sample_article_ids,
        ):
            scraped += 1
            try:
                # Body WITHOUT front matter -> hash over content only, so the
                # front-matter content_hash field equals the manifest value.
                body = render_body(article, cfg.support_base_url)
            except Exception as exc:  # noqa: BLE001
                log.error("clean failed aid=%s title=%r err=%s", article.id, article.title, exc)
                continue

            content_hash = storage.compute_hash(body)
            md = build_markdown(article, content_hash=content_hash, body=body)

            slug = slugify(article.title) or f"article-{article.id}"
            owner = slug_to_id.get(slug)
            if owner is not None and owner != article.id:
                slug = f"{slug}-{article.id}"
            slug_to_id[slug] = article.id

            status = storage.classify(manifest, article.id, content_hash)
            local_path = storage.write_markdown(md, slug, MD_DIR)
            generated += 1

            storage.upsert_entry(
                manifest,
                article_id=article.id,
                slug=slug,
                source_url=article.url,
                updated_at=article.updated_at,
                content_hash=content_hash,
                local_path=local_path,
                status=status,
            )

            if status == "added":
                added += 1
                to_upload.append(article.id)
            elif status == "updated":
                updated += 1
                to_upload.append(article.id)
            else:
                skipped += 1
                # Strict delta contract: unchanged content is NOT re-uploaded.
                # Exception: a prior upload never succeeded (failed/never
                # tried). Retry it so a transient API error doesn't leave a
                # doc permanently missing from the store.
                entry = manifest.get(article.id, {})
                if entry.get("upload_status") != "uploaded":
                    retry_failed.append(article.id)

            estimated_total_chunks += estimate_chunk_count(md, cfg.max_tokens_per_chunk)
            log.info("aid=%s status=%s slug=%s", article.id, status, slug)
    except Exception as exc:  # noqa: BLE001
        log.error("scrape loop failed: %s", exc)
        raise

    # Persist after scrape/clean so the no-secret path still writes a manifest.
    storage.save_manifest(manifest)

    # Combine the delta set (added + updated) with any skipped articles that
    # never successfully uploaded. De-dup by article_id preserving order.
    upload_set: list[int] = []
    seen_ids: set[int] = set()
    for aid in [*to_upload, *retry_failed]:
        if aid not in seen_ids:
            seen_ids.add(aid)
            upload_set.append(aid)

    store_name: str | None = None
    store_created = False
    uploaded = failed = 0
    failures: list = []

    if cfg.can_upload and upload_set:
        log.info("uploading deltas=%d retries=%d total=%d",
                 len(to_upload), len(retry_failed), len(upload_set))
        try:
            client = uploader_google.create_client(cfg.google_api_key)
            # Prefer an explicit env pin, else reuse the persisted store name
            # from data/state/store.json (so deployed reruns stay idempotent).
            persisted_store = storage.load_store() or {}
            stored_name = cfg.store_name or persisted_store.get("name")
            store = uploader_google.get_or_create_store(
                client,
                display_name=cfg.store_display_name,
                stored_name=stored_name,
            )
            store_name = store["name"]
            store_created = store["created"]
            log.info("store name=%s created=%s", store_name, store_created)
            storage.save_store({"name": store_name, "display_name": cfg.store_display_name})

            outcome = uploader_google.upload_changed(
                client,
                store_name=store_name,
                manifest=manifest,
                article_ids=upload_set,
                max_tokens_per_chunk=cfg.max_tokens_per_chunk,
                max_overlap_tokens=cfg.max_overlap_tokens,
                logger=log,
            )
            uploaded = outcome.uploaded
            failed = outcome.failed
            failures = outcome.failures
            storage.save_manifest(manifest)
        except Exception as exc:  # noqa: BLE001
            failed = len(upload_set)
            failures.append({"error": str(exc)})
            log.error("upload step failed: %s\n%s", exc, traceback.format_exc())
    elif not cfg.can_upload:
        log.info("upload skipped (UPLOAD_ENABLED=false or no GOOGLE_API_KEY)")
    elif not upload_set:
        log.info("upload skipped (no deltas and no pending retries)")

    run_summary = {
        "scraped": scraped,
        "generated": generated,
        "added": added,
        "updated": updated,
        "skipped": skipped,
        "uploaded": uploaded,
        "failed": failed,
        "store_name": store_name or cfg.store_name,
        "store_created": store_created,
        "estimated_chunk_count": estimated_total_chunks,
        "failures": failures,
        "upload_enabled": cfg.can_upload,
    }
    path = write_last_run("logs", run_summary)
    log.info("run complete -> %s", path)
    log.info("counts scraped=%d generated=%d added=%d updated=%d skipped=%d "
             "uploaded=%d failed=%d est_chunks=%d",
             scraped, generated, added, updated, skipped, uploaded, failed,
             estimated_total_chunks)
    return run_summary


def main() -> int:
    try:
        run()
        return 0
    except Exception as exc:  # noqa: BLE001
        setup_logging().error("FATAL: %s\n%s", exc, traceback.format_exc())
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
