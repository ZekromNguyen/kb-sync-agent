"""Manifest state: track articles across runs to detect deltas.

`data/state/articles_manifest.json` maps article_id -> entry. An entry holds
the content hash, local Markdown path, upload status, and Google operation /
document info from the last successful upload.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone

MANIFEST_PATH = os.path.join("data", "state", "articles_manifest.json")
STORE_PATH = os.path.join("data", "state", "store.json")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_manifest(path: str = MANIFEST_PATH) -> dict[int, dict]:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}
    # Keys are strings in JSON; normalise to int for lookups.
    return {int(k): v for k, v in data.items()}


def save_manifest(manifest: dict[int, dict], path: str = MANIFEST_PATH) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    payload = {str(k): v for k, v in sorted(manifest.items())}
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True)


def compute_hash(markdown_text: str) -> str:
    """Stable sha256 of normalized Markdown (ignoring leading/trailing ws)."""
    norm = markdown_text.strip().encode("utf-8")
    return hashlib.sha256(norm).hexdigest()


def classify(manifest: dict[int, dict], article_id: int, new_hash: str) -> str:
    """Return 'added', 'updated', or 'skipped' for an article."""
    entry = manifest.get(article_id)
    if entry is None:
        return "added"
    if entry.get("content_hash") != new_hash:
        return "updated"
    return "skipped"


def write_markdown(content: str, slug: str, md_dir: str) -> str:
    os.makedirs(md_dir, exist_ok=True)
    path = os.path.join(md_dir, f"{slug}.md")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)
    return path


def upsert_entry(
    manifest: dict[int, dict],
    *,
    article_id: int,
    slug: str,
    source_url: str,
    updated_at: str,
    content_hash: str,
    local_path: str,
    status: str,
) -> None:
    """Create/refresh the manifest entry after Markdown is written.

    Preserves leave-of-run info (upload_status, document_name, operation_name,
    uploaded_at) from a prior entry so it survives into skipped runs. When an
    article is added/updated, the caller re-sets upload_status after upload.
    """
    existing = manifest.get(article_id, {})
    entry = {
        "article_id": article_id,
        "slug": slug,
        "source_url": source_url,
        "updated_at": updated_at,
        "content_hash": content_hash,
        "local_path": local_path,
        "status": status,
        "last_seen_at": _now(),
    }
    # Carry over upload bookkeeping so the uploader can delete a superseded
    # document on `updated` (Google has no in-place replace) and so `skipped`
    # runs keep the doc reference + status intact. For a real `added` there
    # is no prior entry, so nothing is carried over.
    if status in ("updated", "skipped"):
        for key in ("upload_status", "document_name", "operation_name", "uploaded_at"):
            if key in existing:
                entry[key] = existing[key]
    manifest[article_id] = entry


def mark_uploaded(
    manifest: dict[int, dict],
    article_id: int,
    *,
    operation_name: str | None,
    document_name: str | None,
) -> None:
    entry = manifest.get(article_id)
    if not entry:
        return
    # If a previous version of this article was already uploaded, keep its
    # document name so the uploader can delete the stale document after a
    # successful replace-upload (prevents orphaned, still-retrievable chunks).
    if entry.get("document_name") and entry["document_name"] != document_name:
        entry["previous_document_name"] = entry["document_name"]
    entry["upload_status"] = "uploaded"
    entry["operation_name"] = operation_name
    entry["document_name"] = document_name
    entry["uploaded_at"] = _now()


def mark_document_replaced(manifest: dict[int, dict], article_id: int) -> None:
    """Clear the stale-document pointer after the old doc is deleted."""
    entry = manifest.get(article_id)
    if not entry:
        return
    entry.pop("previous_document_name", None)


def mark_upload_failed(manifest: dict[int, dict], article_id: int, error: str) -> None:
    entry = manifest.get(article_id)
    if not entry:
        return
    entry["upload_status"] = "failed"
    entry["upload_error"] = (error or "")[:500]


# ---- File Search store persistence ------------------------------------------
def load_store(path: str = STORE_PATH) -> dict | None:
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None


def save_store(store: dict, path: str = STORE_PATH) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(store, fh, indent=2, sort_keys=True)
