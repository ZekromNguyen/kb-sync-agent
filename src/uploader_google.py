"""Google AI File Search ingestion: get-or-create store, upload deltas.

Uses the `google-genai` SDK (Client.file_search_stores). Uploads are
long-running operations that must be polled via client.operations.get.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Iterable

from google import genai
from google.genai import types as gtypes

from . import storage

# Poll the upload LRO at most ~5 min (60 * 5s) before giving up.
POLL_MAX_ATTEMPTS = 60
POLL_INTERVAL = 5.0


def create_client(api_key: str) -> "genai.Client":
    """Build an authenticated google-genai Client (thin wrapper for callers)."""
    return genai.Client(api_key=api_key)


def get_or_create_store(
    client: "genai.Client",
    *,
    display_name: str,
    stored_name: str | None,
) -> dict:
    """Find a store by display_name (idempotent) or create it on first run.

    Returns a small dict: {name, display_name, created} where `created` is
    True only if this call created the store fresh.
    """
    # Prefer an exact resource name we already persisted.
    if stored_name:
        try:
            store = client.file_search_stores.get(name=stored_name)
            return {"name": store.name, "display_name": store.display_name, "created": False}
        except Exception:
            # Store was deleted out-of-band; fall through to creation path.
            pass

    # Otherwise list stores and match by display_name. Result order is by
    # create_time ascending, so iterate the whole (paginated) pager.
    for store in client.file_search_stores.list():
        if (store.display_name or "") == display_name and not display_name.endswith("deleted"):
            return {"name": store.name, "display_name": store.display_name, "created": False}

    # None found -> create.
    store = client.file_search_stores.create(
        config=gtypes.CreateFileSearchStoreConfig(
            display_name=display_name,
        )
    )
    return {"name": store.name, "display_name": store.display_name, "created": True}


def _metadata_for(article_id: int, slug: str, source_url: str, content_hash: str, updated_at: str):
    return [
        gtypes.CustomMetadata(key="article_id", string_value=str(article_id)),
        gtypes.CustomMetadata(key="slug", string_value=slug),
        gtypes.CustomMetadata(key="source_url", string_value=source_url),
        gtypes.CustomMetadata(key="content_hash", string_value=content_hash),
        gtypes.CustomMetadata(key="updated_at", string_value=updated_at),
    ]


def _chunking_config(max_tokens: int, overlap: int):
    return gtypes.ChunkingConfig(
        white_space_config=gtypes.WhiteSpaceConfig(
            max_tokens_per_chunk=max_tokens,
            max_overlap_tokens=overlap,
        )
    )


def upload_one(
    client: "genai.Client",
    *,
    store_name: str,
    local_path: str,
    display_name: str,
    article_id: int,
    slug: str,
    source_url: str,
    content_hash: str,
    updated_at: str,
    max_tokens_per_chunk: int,
    max_overlap_tokens: int,
) -> dict:
    """Upload a single Markdown file and block until the LRO completes.

    Returns {operation_name, document_name} on success; raises on failure.
    """
    op = client.file_search_stores.upload_to_file_search_store(
        file_search_store_name=store_name,
        file=local_path,
        config=gtypes.UploadToFileSearchStoreConfig(
            display_name=display_name,
            mime_type="text/markdown",
            custom_metadata=_metadata_for(article_id, slug, source_url, content_hash, updated_at),
            chunking_config=_chunking_config(max_tokens_per_chunk, max_overlap_tokens),
        ),
    )

    attempts = 0
    while not getattr(op, "done", False) and attempts < POLL_MAX_ATTEMPTS:
        time.sleep(POLL_INTERVAL)
        attempts += 1
        op = client.operations.get(op)

    if not getattr(op, "done", False):
        raise TimeoutError(f"upload operation did not finish in ~{POLL_MAX_ATTEMPTS*POLL_INTERVAL:.0f}s: {op.name}")
    if getattr(op, "error", None):
        raise RuntimeError(f"upload failed: {op.error}")

    document_name = None
    if getattr(op, "response", None) is not None:
        document_name = getattr(op.response, "document_name", None)
    return {"operation_name": getattr(op, "name", None), "document_name": document_name}


@dataclass
class UploadOutcome:
    uploaded: int
    failed: int
    failures: list  # [{article_id, slug, error}]


def upload_changed(
    client: "genai.Client",
    *,
    store_name: str,
    manifest: dict[int, dict],
    article_ids: Iterable[int],
    max_tokens_per_chunk: int,
    max_overlap_tokens: int,
    logger,
) -> UploadOutcome:
    """Upload every article in `article_ids` (the added+updated set).

    For `updated` articles a new document is uploaded first; on success the
    previous document is deleted so stale chunks are no longer retrievable
    (Google File Search has no in-place replace). Added articles just upload.
    """
    outcome = UploadOutcome(uploaded=0, failed=0, failures=[])
    for aid in article_ids:
        entry = manifest.get(aid)
        if not entry:
            continue
        try:
            res = upload_one(
                client,
                store_name=store_name,
                local_path=entry["local_path"],
                display_name=f"{entry['slug']}.md",
                article_id=aid,
                slug=entry["slug"],
                source_url=entry["source_url"],
                content_hash=entry["content_hash"],
                updated_at=entry["updated_at"],
                max_tokens_per_chunk=max_tokens_per_chunk,
                max_overlap_tokens= max_overlap_tokens,
            )
            storage.mark_uploaded(
                manifest,
                aid,
                operation_name=res["operation_name"],
                document_name=res["document_name"],
            )
            # Replace path: delete the now-superseded document so its stale
            # chunks can't be retrieved alongside the fresh upload. Google
            # File Search has no in-place replace, and a non-empty document
            # (one with chunks) only deletes with force=true.
            stale = entry.get("previous_document_name")
            if stale:
                try:
                    client.file_search_stores.documents.delete(
                        name=stale,
                        config=gtypes.DeleteDocumentConfig(force=True),
                    )
                    storage.mark_document_replaced(manifest, aid)
                    logger.info("replaced aid=%s deleted stale doc=%s", aid, stale)
                except Exception as exc:  # noqa: BLE001 - don't fail the upload
                    logger.warning(
                        "upload ok but stale-doc delete failed aid=%s doc=%s err=%s",
                        aid, stale, exc,
                    )
            outcome.uploaded += 1
            logger.info("uploaded aid=%s slug=%s doc=%s", aid, entry["slug"], res["document_name"])
        except Exception as exc:  # noqa: BLE001 - log + continue per article
            outcome.failed += 1
            outcome.failures.append({"article_id": aid, "slug": entry.get("slug"), "error": str(exc)})
            storage.mark_upload_failed(manifest, aid, str(exc))
            logger.error("upload failed aid=%s slug=%s err=%s", aid, entry.get("slug"), exc)
    return outcome
