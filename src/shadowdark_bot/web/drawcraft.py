"""Explicitly shared DrawCraft drawings for the public web app and Activity.

The configuration lives in the bot's private data volume. Its view tokens are
read-only and scoped to one drawing; neither the file nor its contents belong
in the static assets or the repository.
"""

import json
import re
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

VIEW_TOKEN = re.compile(r"[A-Za-z0-9_-]{43}\Z")
PUBLIC_ASSETS = Path(__file__).parent / "static" / "assets"


@dataclass(frozen=True)
class DrawcraftView:
    id: str
    title: str
    token: str


def load_drawcraft_views(path: str) -> tuple[DrawcraftView, ...]:
    """Read the optional, private JSON file once when the public app starts."""
    if not path:
        return ()
    try:
        private_file = Path(path).resolve()
        if private_file.is_relative_to(PUBLIC_ASSETS.resolve()):
            raise ValueError
        entries = json.loads(private_file.read_text(encoding="utf-8"))
        if not isinstance(entries, list):
            raise ValueError
        views = []
        seen = set()
        for entry in entries:
            if not isinstance(entry, dict):
                raise ValueError
            drawing_id = entry["id"]
            title = entry["title"]
            token = entry["token"]
            if (not isinstance(drawing_id, str) or str(UUID(drawing_id)) != drawing_id
                    or not isinstance(title, str) or not title.strip() or len(title) > 100
                    or not isinstance(token, str) or not VIEW_TOKEN.fullmatch(token)
                    or drawing_id in seen):
                raise ValueError
            seen.add(drawing_id)
            views.append(DrawcraftView(drawing_id, title.strip(), token))
        return tuple(views)
    except (OSError, ValueError, KeyError, TypeError):
        # Never include the offending entry or parser error: it could contain a token.
        raise ValueError("DRAWCRAFT_VIEWS_FILE is missing or invalid") from None
