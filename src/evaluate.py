"""Evaluate the grounded assistant against a small set of canonical questions.

Each question is scored PASS/FAIL on two criteria:
  1. Groundedness — at least one cited `Article URL:` maps to an article in
     `expected_article_ids`.
  2. Topic coverage — the answer text (case-insensitive) mentions every
     keyword in `expected_topics`.

Run (calls the Google API):
    python -m src.evaluate
    python -m src.evaluate --raw    # also print each answer verbatim
    python -m src.evaluate --limit 3

Requires a populated data/state/articles_manifest.json + a configured File
Search store (run `python main.py` first).
"""
from __future__ import annotations

import argparse
import sys

try:
    import yaml  # type: ignore
except ImportError:  # pragma: no cover - dev-only; fall back to a tiny parser
    yaml = None

from . import assistant, storage

EVAL_FILE = "eval/questions.yaml"


def _load_questions(path: str = EVAL_FILE) -> list[dict]:
    if yaml is None:
        raise SystemExit("PyYAML is required: pip install pyyaml")
    with open(path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    return data.get("questions", [])


def _article_url_to_id(url: str, id_to_url: dict[int, str]) -> int | None:
    for aid, u in id_to_url.items():
        if u == url:
            return aid
    return None


def _urls_to_ids(urls: list[str], manifest: dict[int, dict]) -> set[int]:
    id_to_url = {int(k): v.get("source_url", "") for k, v in manifest.items()}
    ids: set[int] = set()
    for u in urls:
        aid = _article_url_to_id(u, id_to_url)
        if aid is not None:
            ids.add(aid)
    return ids


def _score_answer(text: str, citations: list[str], item: dict, manifest: dict[int, dict]) -> bool:
    topics = [t.lower() for t in item.get("expected_topics", [])]
    expected_ids = set(item.get("expected_article_ids", []))
    text_lower = (text or "").lower()
    topic_ok = all(t in text_lower for t in topics)
    cited_ids = _urls_to_ids(citations, manifest)
    grounded_ok = bool(cited_ids & expected_ids) if expected_ids else True
    return topic_ok and grounded_ok


def evaluate(limit: int | None = None, raw: bool = False) -> tuple[int, int]:
    questions = _load_questions()
    if limit:
        questions = questions[:limit]
    manifest = storage.load_manifest()
    if not manifest:
        raise SystemExit("manifest is empty — run `python main.py` first to populate it")

    passed = 0
    print(f"Evaluating {len(questions)} questions...\n")
    for i, item in enumerate(questions, 1):
        q = item["question"]
        try:
            text, citations = assistant.answer(q)
        except Exception as exc:  # noqa: BLE001
            print(f"[{i}/{len(questions)}] FAIL  Q: {q}")
            print(f"        error: {exc}\n")
            continue
        ok = _score_answer(text, citations, item, manifest)
        marker = "PASS" if ok else "FAIL"
        if ok:
            passed += 1
        print(f"[{i}/{len(questions)}] {marker}  Q: {q}")
        print(f"        topics grounded={'yes' if ok else 'no'}  citations={citations}")
        if raw:
            print("        --- answer ---")
            print(text)
            print("        --- end ---")
        print()
    print(f"RESULT {passed}/{len(questions)} passed")
    return passed, len(questions)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="src.evaluate", description="Evaluate the grounded assistant.")
    p.add_argument("--limit", type=int, default=None, help="evaluate only the first N questions")
    p.add_argument("--raw", action="store_true", help="print each answer verbatim")
    args = p.parse_args(argv)
    try:
        passed, total = evaluate(limit=args.limit, raw=args.raw)
    except SystemExit as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
