"""Centralised configuration, loaded from environment variables and `.env`.

Nothing in this module touches the network or the Google API. It only reads
environment so the rest of the pipeline can depend on a single typed object.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

DEFAULT_SAMPLE_ARTICLE_IDS = (
    360051014713,    # How to use YouTube with OptiSigns
    360016981853,    # Creating and Using Schedules with OptiSigns
    28295104605843,  # How to Create & Use Playlists
    360016374813,    # Set up & add a screen
    360016382473,    # How to Use the Website App and Display URLs
)


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _as_int(value: str | None, default: int) -> int:
    try:
        return int(value) if value is not None else default
    except (TypeError, ValueError):
        return default


def _parse_ids(value: str | None) -> tuple[int, ...]:
    """Parse a comma-separated list of article IDs (e.g. "360051014713,123")."""
    if not value:
        return ()
    ids = []
    for token in value.split(","):
        token = token.strip()
        if token:
            try:
                ids.append(int(token))
            except ValueError:
                continue
    return tuple(ids)


@dataclass
class Config:
    secrets_base_dir: str = "data"

    google_api_key: str | None = None
    store_display_name: str = "optibot-support-kb"
    # Persistent resource name (fileSearchStores/...). Optional on first run --
    # once a store is created, the job persists it in data/state/store.json.
    store_name: str | None = None
    gemini_model: str = "gemini-2.5-flash"

    support_base_url: str = "https://support.optisigns.com"
    article_limit: int = 30
    upload_enabled: bool = True
    log_level: str = "INFO"

    # Local chunking rule used for the *estimated* chunk count logged per run,
    # AND the provider chunking config sent on upload. Google AI File Search
    # enforces max_tokens_per_chunk in [0, 512] and overlap < chunk size, so we
    # pin to 512 (the largest allowed) with a 64-token overlap (≈12.5%) for
    # context preservation across chunk boundaries.
    max_tokens_per_chunk: int = 512
    max_overlap_tokens: int = 64

    # Comma-separated OptiSigns article IDs pinned into the corpus ahead of the
    # recency-ordered listing. The Zendesk list endpoint returns articles
    # newest-first, so without pinning a small ARTICLE_LIMIT would miss the
    # docs behind the canonical sample question ("How do I add a YouTube
    # video?"). These are fetched via the single-article detail endpoint.
    # Default includes the dedicated YouTube app article so the sample question
    # is answerable out of the box; set SAMPLE_ARTICLE_IDS= (empty) to disable.
    sample_article_ids: tuple[int, ...] = DEFAULT_SAMPLE_ARTICLE_IDS

    @property
    def can_upload(self) -> bool:
        """True only when the job may actually call the Google API."""
        return self.upload_enabled and bool(self.google_api_key)


def load_config(env_file: str = ".env") -> Config:
    # load_dotenv silently no-ops if the file is missing (e.g. inside Docker when
    # env is injected directly), so this is safe to call unconditionally.
    load_dotenv(env_file)
    return Config(
        google_api_key=(os.getenv("GOOGLE_API_KEY") or "").strip() or None,
        store_display_name=os.getenv(
            "GOOGLE_FILE_SEARCH_STORE_DISPLAY_NAME", "optibot-support-kb"
        )
        or "optibot-support-kb",
        store_name=(os.getenv("GOOGLE_FILE_SEARCH_STORE_NAME") or "").strip() or None,
        gemini_model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash") or "gemini-2.5-flash",
        support_base_url=(os.getenv("SUPPORT_BASE_URL", "https://support.optisigns.com") or "").rstrip("/"),
        article_limit=_as_int(os.getenv("ARTICLE_LIMIT"), 30),
        upload_enabled=_as_bool(os.getenv("UPLOAD_ENABLED"), True),
        log_level=(os.getenv("LOG_LEVEL", "INFO") or "INFO").upper(),
        max_tokens_per_chunk=_as_int(os.getenv("MAX_TOKENS_PER_CHUNK"), 512),
        max_overlap_tokens=_as_int(os.getenv("MAX_OVERLAP_TOKENS"), 64),
        sample_article_ids=_parse_sample_ids(os.getenv("SAMPLE_ARTICLE_IDS")),
    )


def _parse_sample_ids(value: str | None) -> tuple[int, ...]:
    """SAMPLE_ARTICLE_IDS unset -> default demo articles; empty -> none."""
    if value is None:
        return DEFAULT_SAMPLE_ARTICLE_IDS
    return _parse_ids(value)


# Required assistant system instruction. Used verbatim by the job's query helper
# and (if a manual AI Studio assistant is configured) by the screenshot step.
OPTIBOT_SYSTEM_PROMPT = (
    "You are OptiBot, the customer-support bot for OptiSigns.com.\n"
    "• Tone: helpful, factual, concise.\n"
    "• Only answer using the uploaded docs.\n"
    "• Max 5 bullet points; else link to the doc.\n"
    '• Cite up to 3 "Article URL:" lines per reply.'
)


# Extra style guardrail for the local playground/query helper. The required
# prompt above stays verbatim; this appends observed OptiBot answer-shape rules
# so Gemini does not drift into long generic assistant explanations.
OPTIBOT_STYLE_PROMPT = (
    OPTIBOT_SYSTEM_PROMPT
    + "\n\n"
    "Mimic the real OptiBot support-widget answer style:\n"
    "- For how-to questions, start with the shortest portal path or first action, "
    "for example: Files/Assets → + Create → Apps → YouTube.\n"
    "- Do not start with generic intros like 'To do X, follow these steps'.\n"
    "- Prefer 2–4 short paragraphs, not long step lists.\n"
    "- When the answer is a workflow, put each action on its own short line; "
    "do not merge multiple actions into one paragraph.\n"
    "- Do not use numbered lists unless the user explicitly asks for detailed steps.\n"
    "- Include only the key caveats needed to complete the task; do not enumerate "
    "every form field unless the user asks.\n"
    "- For app setup questions, summarize form filling in one short sentence.\n"
    "- For add/create app-asset questions, use exactly this shape when supported "
    "by the docs: portal path; one sentence for what to paste/configure/save; "
    "one sentence for assigning/publishing to screens/playlists/schedules; citation.\n"
    "- Normalize app creation paths as `Files/Assets → + Create → Apps → <App>` "
    "when the docs describe creating an app asset from Files/Assets.\n"
    "- Do not mention optional captions, previews, or every field unless the user asks.\n"
    "- Do not add related-but-different features unless the user asks; answer the "
    "specific workflow only.\n"
    "- For schedule-content questions, start with `Use a Schedule, then assign it "
    "to your screen.` Then use two short sections: `Create the schedule` and "
    "`Assign the schedule to screens`.\n"
    "- End with either `For more details: [Article Title](Article URL)` or an "
    "`Article URL:` line. Do not invent links.\n"
    "- For vague troubleshooting, ask clarifying questions instead of guessing.\n"
    "- If private account or billing access is requested, say this mini-clone "
    "cannot access private account data.\n\n"
    "Preferred answer examples. Follow this structure closely:\n\n"
    "Q: How do I add a YouTube video?\n"
    "Files/Assets → + Create → Apps → YouTube.\n\n"
    "Paste the video URL (use the full video link, not the share link), name "
    "the asset, and save. For Shorts, change /shorts/ to /embed/ in the URL.\n\n"
    "Then assign the asset to a screen, playlist, or schedule.\n\n"
    "For more details: [Play YouTube videos and Shorts on digital signs with OptiSigns]"
    "(https://support.optisigns.com/hc/en-us/articles/360051014713-How-to-use-YouTube-with-OptiSigns)\n\n"
    "Q: How do I schedule content?\n"
    "Use a Schedule, then assign it to your screen.\n\n"
    "Create the schedule\n"
    "Go to Schedules (top bar).\n"
    "Click Create Schedule and name it.\n"
    "Click Add Event (or drag on the calendar).\n"
    "Choose an asset or playlist, set start/end time, and optionally set it to repeat.\n"
    "Click Save.\n\n"
    "Assign the schedule to screens\n"
    "Either: Screens → Edit that screen → Type = Schedule → pick your schedule → Save.\n"
    "Or: open the schedule → click Push to Screens → select your screen(s).\n\n"
    "For more details: [Create, Repeat, and Assign Schedules to Screens in OptiSigns]"
    "(https://support.optisigns.com/hc/en-us/articles/360016981853-Creating-and-Using-Schedules-with-OptiSigns)\n\n"
    "Q: How do I create a playlist?\n"
    "Playlists → Create Playlist.\n\n"
    "Click Playlists in the top bar.\n"
    "Click Create Playlist, name it.\n"
    "Drag in assets (images, videos, web links, apps) from the right panel.\n"
    "Click each item's duration to adjust how long it plays.\n"
    "Click Push to Screens at the top to assign it to one or more screens (now or scheduled).\n\n"
    "For more details: [Create a Playlist and Push Content to Screens in OptiSigns]"
    "(https://support.optisigns.com/hc/en-us/articles/28295104605843-How-to-Create-Use-Playlists)"
)
