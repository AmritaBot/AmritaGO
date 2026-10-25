"""Attachments: images pasted into the composer and long text turned into files.

An image is stored as a data URL and passed on as ``ImageContent``; a long
paste is written under ``~/.amrita/attachments`` and referenced by path. Both
are per-conversation state, so they stay out of the workspace.
"""

from __future__ import annotations

import base64
import binascii
import re
import uuid
from pathlib import Path
from typing import Any, Literal

from amrita_core.types.content import ImageContent, ImageUrl, TextContent
from pydantic import BaseModel, Field

from app.core.config import PATHS

Kind = Literal["image", "text"]

# A prompt may be a plain string or a list of typed content items.
UserContent = str | list[ImageContent | TextContent]

# Pastes longer than this are offloaded to a file instead of the composer.
LONG_TEXT_THRESHOLD = 4000
# Images larger than this are rejected — a 20 MB phone photo is not a prompt.
MAX_IMAGE_BYTES = 8 * 1024 * 1024

_DATA_URL = re.compile(r"^data:(image/[A-Za-z0-9.+-]+);base64,(.+)$", re.DOTALL)
_EXTENSIONS = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
}


class AttachmentError(ValueError):
    """Raised when a client sends something that cannot be stored."""


class Attachment(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:16])
    kind: Kind
    name: str
    mime: str = "text/plain"
    size: int = 0
    path: str | None = None
    url: str | None = None

    def to_public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "name": self.name,
            "mime": self.mime,
            "size": self.size,
            "path": self.path,
            "url": self.url,
        }


def _root() -> Path:
    path = PATHS.data / "attachments"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _safe_name(name: str, fallback: str) -> str:
    cleaned = re.sub(r"[^\w.\-]+", "-", name).strip("-")
    return cleaned[:60] or fallback


def save_image(data_url: str, name: str = "pasted-image") -> Attachment:
    """Store a pasted image; return its data URL for the model to consume."""
    match = _DATA_URL.match(data_url.strip())
    if match is None:
        raise AttachmentError("expected an image data URL")
    mime, payload = match.groups()
    try:
        raw = base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise AttachmentError(f"invalid base64 image: {exc}") from exc
    if not raw:
        raise AttachmentError("image is empty")
    if len(raw) >= MAX_IMAGE_BYTES:
        raise AttachmentError(
            f"image is {len(raw) // 1024} KiB; the limit is {MAX_IMAGE_BYTES // 1024} KiB"
        )

    extension = _EXTENSIONS.get(mime, ".bin")
    stem = _safe_name(name, "image")
    if not stem.endswith(extension):
        stem = f"{stem}{extension}"
    target = _root() / f"{uuid.uuid4().hex[:12]}-{stem}"
    target.write_bytes(raw)

    return Attachment(
        kind="image",
        name=stem,
        mime=mime,
        size=len(raw),
        path=str(target),
        url=data_url,
    )


def save_text(content: str, name: str | None = None) -> Attachment:
    """Store a long paste as a real file the agent can open."""
    if not content.strip():
        raise AttachmentError("text is empty")
    stem = _safe_name(name or "pasted-text", "pasted-text")
    if "." not in stem:
        stem = f"{stem}.txt"
    target = _root() / f"{uuid.uuid4().hex[:12]}-{stem}"
    target.write_text(content, encoding="utf-8")
    return Attachment(
        kind="text",
        name=stem,
        mime="text/plain",
        size=len(content.encode("utf-8")),
        path=str(target),
    )


def load(attachment_id: str) -> str | None:
    """Read a stored text attachment by the id embedded in its filename."""
    for path in _root().iterdir():
        if path.name.startswith(attachment_id) and path.is_file():
            if path.suffix in {".png", ".jpg", ".webp", ".gif"}:
                return None
            return path.read_text(encoding="utf-8")
    return None


def delete(attachment_id: str) -> bool:
    removed = False
    for path in _root().iterdir():
        if path.name.startswith(attachment_id):
            path.unlink(missing_ok=True)
            removed = True
    return removed


def to_content(items: list[Attachment]) -> list[dict[str, Any]]:
    """Convert attachments into the content items AmritaCore understands.

    Images become ``image_url`` items; text files become a short text item that
    names the path, because the model can read the file itself and echoing a
    long paste back into the context is exactly what this feature avoids.
    """
    out: list[dict[str, Any]] = []
    for item in items:
        if item.kind == "image" and item.url:
            out.append({"type": "image_url", "image_url": {"url": item.url}})
        elif item.kind == "text" and item.path:
            out.append(
                {
                    "type": "text",
                    "text": (
                        f"[attached file: {item.name} -> {item.path}]\n"
                        "Read it with a shell or file tool if you need the contents."
                    ),
                }
            )
    return out


def build_user_content(text: str, attachments: list[dict[str, Any]]) -> UserContent:
    """A plain string when there is nothing attached, content items otherwise.

    Keeping the plain-string case matters: a bare ``str`` is what every adapter
    has always received, and some gateways are strict about arrays.
    """
    if not attachments:
        return text
    items: list[ImageContent | TextContent] = []
    if text.strip():
        items.append(TextContent(type="text", text=text))
    for item in attachments:
        kind = item.get("kind")
        if kind == "image" and item.get("url"):
            items.append(
                ImageContent(
                    type="image_url",
                    image_url=ImageUrl(url=str(item["url"])),
                )
            )
        elif kind == "text" and item.get("path"):
            items.append(
                TextContent(
                    type="text",
                    text=(
                        f"[attached file: {item.get('name')} -> {item['path']}]\n"
                        "Read it with a shell or file tool if you need the contents."
                    ),
                )
            )
    return items or text


__all__ = [
    "LONG_TEXT_THRESHOLD",
    "MAX_IMAGE_BYTES",
    "Attachment",
    "AttachmentError",
    "build_user_content",
    "delete",
    "load",
    "save_image",
    "save_text",
    "to_content",
]
