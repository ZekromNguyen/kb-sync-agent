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
    # An `added` entry has no prior info to carry over.
    m2 = {}
    storage.upsert_entry(
        m2, article_id=2, slug="s", source_url="u", updated_at="2026",
        content_hash="h", local_path="p", status="added",
    )
    assert "upload_status" not in m2[2]


def test_upsert_preserves_doc_name_on_updated_for_replace():
    """An `updated` article must keep its old document_name so the uploader can
    delete the now-superseded document (Google has no in-place replace)."""
    m = {1: {"document_name": "doc/old", "upload_status": "uploaded", "uploaded_at": "T"}}
    storage.upsert_entry(
        m, article_id=1, slug="s", source_url="u", updated_at="2026",
        content_hash="h2", local_path="p", status="updated",
    )
    assert m[1]["document_name"] == "doc/old"  # carried over for the delete path
    # upload_status is carried over too; the uploader rewrites it after upload.
    assert m[1]["upload_status"] == "uploaded"


def test_mark_uploaded_tracks_stale_document_for_replace():
    """A second upload with a new doc name should record the old one as stale,
    so the uploader can delete it (Google has no in-place replace)."""
    m = {1: {"upload_status": "uploaded", "document_name": "doc/old"}}
    storage.mark_uploaded(m, 1, operation_name="op/2", document_name="doc/new")
    assert m[1]["document_name"] == "doc/new"
    assert m[1]["previous_document_name"] == "doc/old"
    # clearing the stale pointer after the delete
    storage.mark_document_replaced(m, 1)
    assert "previous_document_name" not in m[1]
    assert m[1]["document_name"] == "doc/new"  # current doc unaffected


def test_load_manifest_missing_file(tmp_path):
    # Missing path returns an empty dict (no crash), enabling a clean first run.
    assert storage.load_manifest(str(tmp_path / "nope.json")) == {}
