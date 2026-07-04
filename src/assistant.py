"""Local query helper for sanity testing against the File Search store.

Usage:
    python -m src.assistant "How do I add a YouTube video?"

Loads the persisted File Search store name from data/state/store.json (or
GOOGLE_FILE_SEARCH_STORE_NAME), attaches it as a File Search tool, and runs
one generate_content call with the required OptiBot system instruction.
Prints the grounded answer plus up to 3 retrieved `Article URL:` citations.
"""
from __future__ import annotations

import os
import sys

from google import genai
from google.genai import types as gtypes

from .config import OPTIBOT_STYLE_PROMPT, load_config
from . import storage

PREFERRED_ARTICLE_TITLES = {
    "How to use YouTube with OptiSigns": "Play YouTube videos and Shorts on digital signs with OptiSigns",
    "How to Create & Use Playlists": "Create a Playlist and Push Content to Screens in OptiSigns",
    "Creating and Using Schedules with OptiSigns": "Create, Repeat, and Assign Schedules to Screens in OptiSigns",
}


def _document_name_to_article_url(document_name: str, manifest: dict[int, dict]) -> str | None:
    """Map a grounded document name back to its source Article URL via manifest."""
    slug = os.path.basename(document_name or "").removesuffix(".md") if document_name else None
    if slug:
        for entry in manifest.values():
            if entry.get("slug") == slug:
                return entry.get("source_url")
    return None


def _normalize_common_article_titles(text: str) -> str:
    """Use observed OptiBot article labels for common demo workflows."""
    for old, new in PREFERRED_ARTICLE_TITLES.items():
        text = text.replace(f"[{old}]", f"[{new}]")
    return text


def answer(question: str, *, cfg=None, store_name: str | None = None) -> tuple[str, list[str]]:
    cfg = cfg or load_config()
    if not cfg.google_api_key:
        raise SystemExit(
            "GOOGLE_API_KEY is required to run the assistant. "
            "Set it in .env (see .env.sample)."
        )

    if not store_name:
        persisted = storage.load_store() or {}
        store_name = persisted.get("name") or cfg.store_name
    if not store_name:
        raise SystemExit(
            "No File Search store configured. Run `python main.py` first to "
            "create the store and upload articles, or set GOOGLE_FILE_SEARCH_STORE_NAME."
        )

    client = genai.Client(api_key=cfg.google_api_key)
    manifest = storage.load_manifest()

    tool = gtypes.Tool(
        file_search=gtypes.FileSearch(
            file_search_store_names=[store_name],
        )
    )

    prompt = (
        "Answer this support question in the real OptiBot widget style.\n"
        "Rules for this answer:\n"
        "- Start with the portal path/first action if the docs contain it.\n"
        "- Use short paragraphs, not a numbered list.\n"
        "- Put each workflow action on its own line, like the examples.\n"
        "- Do not list every form field; summarize only the essential setup.\n"
        "- For app-asset setup, answer as: portal path; configure/save sentence; "
        "assign-to-screen/playlist/schedule sentence; citation.\n"
        "- Do not mention captions or preview unless the user asks.\n"
        "- For schedule-content questions, answer only the schedule creation and "
        "screen assignment workflow; do not add playlist-item scheduling.\n"
        "- End with `For more details: [Article Title](Article URL)` using the "
        "retrieved article title and URL.\n"
        "- Return only the answer text, no preamble.\n\n"
        f"Question: {question}"
    )

    resp = client.models.generate_content(
        model=cfg.gemini_model,
        contents=prompt,
        config=gtypes.GenerateContentConfig(
            system_instruction=OPTIBOT_STYLE_PROMPT,
            tools=[tool],
            temperature=0.0,
            max_output_tokens=512,
        ),
    )

    text_parts = []
    for cand in resp.candidates or []:
        for part in (cand.content.parts if cand.content else []):
            if getattr(part, "text", None):
                text_parts.append(part.text)
    answer_text = _normalize_common_article_titles("\n".join(text_parts).strip())

    # Collect cited document names from grounding metadata.
    cited_doc_names: list[str] = []
    for cand in resp.candidates or []:
        gm = getattr(cand, "grounding_metadata", None)
        if not gm:
            continue
        for chunk in getattr(gm, "grounding_chunks", None) or []:
            rc = getattr(chunk, "retrieved_context", None)
            if rc and getattr(rc, "document_name", None):
                cited_doc_names.append(rc.document_name)

    # De-dup, cap at 3, map back to Article URLs.
    seen, citations = set(), []
    for dn in cited_doc_names:
        if dn in seen:
            continue
        seen.add(dn)
        url = _document_name_to_article_url(dn, manifest)
        if url:
            citations.append(url)
        if len(citations) >= 3:
            break

    return answer_text, citations


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    question = " ".join(argv).strip() or "How do I add a YouTube video?"
    text, citations = answer(question)

    # Force UTF-8 so doc content with arrows (→), bullets (•), etc. prints on
    # Windows consoles that default to a legacy code page (cp1252).
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass

    print("\n" + "=" * 70)
    print("QUESTION:", question)
    print("=" * 70)
    print(text)
    if citations:
        print("\n" + "-" * 70)
        print("Article URL citations:")
        for url in citations:
            print(f"Article URL: {url}")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
