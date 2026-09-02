"""
Backups: blueprint ↔ file.

A backup is a JSON file holding a server's complete blueprint. Because icons,
emojis and stickers are embedded as base64, anything past a certain size is
gzip-compressed automatically — which typically shrinks the file to a fraction
and keeps it under Discord's upload limit.
"""

from __future__ import annotations

import gzip
import json
import logging
import zlib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .blueprint import GuildBlueprint

log = logging.getLogger(__name__)

BACKUP_DIR = Path(__file__).resolve().parent.parent / "backups"

# Past this size it is compressed rather than shipped as plain text.
GZIP_THRESHOLD = 1 * 1024 * 1024

# Discord's upload limit for bots without a server boost.
DISCORD_UPLOAD_LIMIT = 10 * 1024 * 1024

GZIP_MAGIC = b"\x1f\x8b"


class SnapshotError(RuntimeError):
    """Something went wrong reading or writing a backup."""


@dataclass(slots=True)
class Snapshot:
    """A serialised blueprint plus metadata about the file."""

    blueprint: GuildBlueprint
    data: bytes
    filename: str

    @property
    def size(self) -> int:
        return len(self.data)

    @property
    def compressed(self) -> bool:
        return self.filename.endswith(".gz")

    @property
    def too_large_for_discord(self) -> bool:
        return self.size > DISCORD_UPLOAD_LIMIT


def _safe_name(name: str) -> str:
    """Turns a server name into a usable filename."""
    keep = [c if (c.isalnum() or c in "-_ ") else "-" for c in name]
    cleaned = "".join(keep).strip().replace(" ", "-")
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    return (cleaned or "server")[:48].strip("-")


def dump(blueprint: GuildBlueprint) -> Snapshot:
    """Serialises a blueprint, compressing when needed."""
    raw = json.dumps(blueprint.to_dict(), ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M")
    base = f"{_safe_name(blueprint.name)}_{stamp}.esc.json"

    if len(raw) >= GZIP_THRESHOLD:
        return Snapshot(blueprint=blueprint, data=gzip.compress(raw, 6), filename=f"{base}.gz")
    return Snapshot(blueprint=blueprint, data=raw, filename=base)


def load(data: bytes) -> GuildBlueprint:
    """Reads a backup, compressed or plain."""
    if data[:2] == GZIP_MAGIC:
        try:
            data = gzip.decompress(data)
        # A truncated file raises EOFError, a corrupted one zlib.error —
        # neither is an OSError, so both would otherwise slip through.
        except (OSError, EOFError, zlib.error) as exc:
            raise SnapshotError(
                f"The file is damaged or incomplete ({exc}). "
                f"Lade sie erneut hoch."
            ) from exc

    try:
        payload = json.loads(data.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise SnapshotError(
            "That is not a valid text file. A backup created with `/backup` "
            "is expected."
        ) from exc
    except json.JSONDecodeError as exc:
        raise SnapshotError(f"The file does not contain valid JSON: {exc}") from exc

    if not isinstance(payload, dict):
        raise SnapshotError("The file does not contain a backup object.")

    try:
        return GuildBlueprint.from_dict(payload)
    except (TypeError, ValueError) as exc:
        raise SnapshotError(f"Backup is not readable: {exc}") from exc


def save_to_disk(snapshot: Snapshot) -> Path | None:
    """Also drops a copy next to the bot. Failures here are not fatal."""
    try:
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        path = BACKUP_DIR / snapshot.filename
        path.write_bytes(snapshot.data)
        return path
    except OSError as exc:
        log.warning("Could not save the backup locally: %s", exc)
        return None


def human_size(num_bytes: int) -> str:
    if num_bytes < 1024:
        return f"{num_bytes} B"
    if num_bytes < 1024 * 1024:
        return f"{num_bytes / 1024:.1f} KB"
    return f"{num_bytes / (1024 * 1024):.1f} MB"
