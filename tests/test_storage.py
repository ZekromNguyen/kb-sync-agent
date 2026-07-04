"""Tests for src.storage: hashing, classification, manifest/store round-trips."""
from src import storage


def test_compute_hash_stable_and_sensitive():
    # Leading/trailing whitespace is stripped before hashing -> stable.
    assert storage.compute_hash("hello\n") == storage.compute_hash("  hello  \n")
    assert storage.compute_hash("hello") != storage.compute_hash("goodbye")


def test_classify_added_updated_skipped():
    manifest = {1: {"content_hash": "h"}}
    assert storage.classify(manifest, 2, "h") == "added"
    assert storage.classify(manifest, 1, "h2") == "updated"
    assert storage.classify(manifest, 1, "h") == "skipped"
    assert storage.classify({}, 99, "x") == "added"


def test_manifest_roundtrip(tmp_path):
    p = str(tmp_path / "manifest.json")
    m = {}
    storage.upsert_entry(
        m,
        article_id=10,
        slug="how-to-x",
        source_url="https://support.optisigns.com/x",
        updated_at="2026-01-01",
        content_hash="c",
        local_path="data/markdown/how-to-x.md",
        status="added",
    )
    storage.save_manifest(m, p)
    loaded = storage.load_manifest(p)
    assert 10 in loaded
    assert loaded[10]["slug"] == "how-to-x"
    assert loaded[10]["status"] == "added"
    assert loaded[10]["content_hash"] == "c"


def test_upload_status_transitions():
    m = {5: {"slug": "s"}}
    storage.mark_uploaded(m, 5, operation_name="op/1", document_name="doc/1")
    assert m[5]["upload_status"] == "uploaded"
    assert m[5]["document_name"] == "doc/1"
    assert m[5]["operation_name"] == "op/1"
    storage.mark_upload_failed(m, 5, "boom")
    assert m[5]["upload_status"] == "failed"
    assert m[5]["upload_error"] == "boom"


def test_store_roundtrip(tmp_path):
    p = str(tmp_path / "store.json")
    assert storage.load_store(p) is None
    storage.save_store({"name": "fileSearchStores/x-123", "display_name": "optibot-support-kb"}, p)
    s = storage.load_store(p)
    assert s["name"] == "fileSearchStores/x-123"
    assert s["display_name"] == "optibot-support-kb"


def test_upsert_preserves_upload_info_on_skip():
    m = {1: {"upload_status": "uploaded", "document_name": "doc/x", "uploaded_at": "T"}}
    # A skipped re-run should carry over upload bookkeeping.
    storage.upsert_entry(
        m, article_id=1, slug="s", source_url="u", updated_at="2026",
        content_hash="h", local_path="p", status="skipped",
    )
    assert m[1]["upload_status"] == "uploaded"
    assert m[1]["document_name"] == "doc/x"
    # A delta change (added/updated) wipes upload bookkeeping, since the file
    # must be re-uploaded.
    storage.upsert_entry(
        m, article_id=1, slug="s", source_url="u", updated_at="2026",
        content_hash="h2", local_path="p", status="updated",
    )
    assert "upload_status" not in m[1]


def test_load_manifest_missing_file(tmp_path):
    # Missing path returns an empty dict (no crash), enabling a clean first run.
    assert storage.load_manifest(str(tmp_path / "nope.json")) == {}
