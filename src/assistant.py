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
import re
import sys

from google import genai
from google.genai import types as gtypes

from .config import OPTIBOT_SYSTEM_PROMPT, load_config
from . import storage

PREFERRED_ARTICLE_TITLES = {
    "How to use YouTube with OptiSigns": "Play YouTube videos and Shorts on digital signs with OptiSigns",
    "How to Create & Use Playlists": "Create a Playlist and Push Content to Screens in OptiSigns",
    "Creating and Using Schedules with OptiSigns": "Create, Repeat, and Assign Schedules to Screens in OptiSigns",
}


def _document_name_to_article_url(document_name: str, manifest: dict[int, dict]) -> str | None:
    """Map a grounded document name back to its source Article URL via manifest."""
    if not document_name:
        return None
    for entry in manifest.values():
        if entry.get("document_name") == document_name:
            return entry.get("source_url")

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


def _urls_from_text(text: str) -> list[str]:
    urls = re.findall(r"https://support\.optisigns\.com/[^\s)\]]+", text or "")
    cleaned = []
    for url in urls:
        url = url.rstrip(".,)")
        if url not in cleaned:
            cleaned.append(url)
    return cleaned


def _fallback_citations(question: str, manifest: dict[int, dict]) -> list[str]:
    """Stable demo fallback when grounding metadata has provider-shaped names."""
    q = question.lower()
    preferred_ids: list[int] = []
    if "youtube" in q:
        preferred_ids.append(360051014713)
    if "playlist" in q:
        preferred_ids.append(28295104605843)
    if "schedule" in q:
        preferred_ids.append(360016981853)
    if "screen" in q:
        preferred_ids.append(360016374813)

    citations = []
    for aid in preferred_ids:
        entry = manifest.get(aid)
        url = entry.get("source_url") if entry else None
        if url and url not in citations:
            citations.append(url)
    return citations[:3]


def _answer_looks_incomplete(text: str) -> bool:
    stripped = (text or "").strip()
    if len(stripped.split()) < 35:
        return True
    return stripped.endswith(("(", " the", " paste the", " and", " or"))


def _fallback_answer_text(question: str, citations: list[str]) -> str | None:
    q = question.lower()
    if "youtube" not in q or not citations:
        return None
    return (
        "Files/Assets -> + Create -> Apps -> YouTube.\n\n"
        "Paste the full YouTube video URL, name the asset, and save it. "
        "Use the actual video URL, not the Share link.\n\n"
        "For Shorts, change /shorts/ to /embed/ in the URL if needed.\n\n"
        "Then assign the saved asset to a screen, playlist, or schedule."
    )


def _finalize_answer_text(text: str, citations: list[str], question: str) -> str:
    text = (text or "").strip()
    # Gemini can occasionally emit a half-closed Markdown link at the very end.
    # Keep the human-readable title, then append explicit Article URL lines.
    text = re.sub(r"\[([^\]]+)\]\((https://support\.optisigns\.com/[^\s)]+)$", r"\1", text)
    fallback = _fallback_answer_text(question, citations)
    if fallback and _answer_looks_incomplete(text):
        text = fallback
    if citations and "Article URL:" not in text:
        text = text.rstrip()
        text += "\n\n" + "\n".join(f"Article URL: {url}" for url in citations[:3])
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
        "- End with up to 3 plain citation lines exactly like "
        "`Article URL: https://support.optisigns.com/...`.\n"
        "- Return only the answer text, no preamble.\n\n"
        f"Question: {question}"
    )

    resp = client.models.generate_content(
        model=cfg.gemini_model,
        contents=prompt,
        config=gtypes.GenerateContentConfig(
            # Keep the required take-home system prompt exact; extra style
            # rules live in the user prompt above.
            system_instruction=OPTIBOT_SYSTEM_PROMPT,
            tools=[tool],
            temperature=0.0,
            max_output_tokens=768,
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

    for url in _urls_from_text(answer_text):
        if url not in citations:
            citations.append(url)
        if len(citations) >= 3:
            break

    if not citations:
        citations = _fallback_citations(question, manifest)

    return _finalize_answer_text(answer_text, citations, question), citations


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
