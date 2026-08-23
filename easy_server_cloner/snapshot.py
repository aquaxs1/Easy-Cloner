"""
Backups: Bauplan ↔ Datei.

Ein Backup ist eine JSON-Datei mit dem kompletten Bauplan eines Servers. Weil
Icons, Emojis und Sticker als base64 mit drin stecken, wird ab einer gewissen
Größe automatisch gzip-komprimiert — das drückt die Datei typischerweise auf
ein Bruchteil und hält sie unter Discords Upload-Limit.
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

# Ab dieser Größe wird komprimiert statt als Klartext ausgeliefert.
GZIP_THRESHOLD = 1 * 1024 * 1024

# Discords Upload-Limit für Bots ohne Server-Boost.
DISCORD_UPLOAD_LIMIT = 10 * 1024 * 1024

GZIP_MAGIC = b"\x1f\x8b"


class SnapshotError(RuntimeError):
    """Beim Lesen oder Schreiben eines Backups ist etwas schiefgegangen."""


@dataclass(slots=True)
class Snapshot:
    """Ein serialisierter Bauplan samt Metadaten zur Datei."""

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
    """Macht aus einem Server-Namen einen brauchbaren Dateinamen."""
    keep = [c if (c.isalnum() or c in "-_ ") else "-" for c in name]
    cleaned = "".join(keep).strip().replace(" ", "-")
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    return (cleaned or "server")[:48].strip("-")


def dump(blueprint: GuildBlueprint) -> Snapshot:
    """Serialisiert einen Bauplan, komprimiert bei Bedarf."""
    raw = json.dumps(blueprint.to_dict(), ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M")
    base = f"{_safe_name(blueprint.name)}_{stamp}.esc.json"

    if len(raw) >= GZIP_THRESHOLD:
        return Snapshot(blueprint=blueprint, data=gzip.compress(raw, 6), filename=f"{base}.gz")
    return Snapshot(blueprint=blueprint, data=raw, filename=base)


def load(data: bytes) -> GuildBlueprint:
    """Liest ein Backup — egal ob komprimiert oder als Klartext."""
    if data[:2] == GZIP_MAGIC:
        try:
            data = gzip.decompress(data)
        # Eine abgeschnittene Datei liefert EOFError, eine verfälschte
        # zlib.error — beides ist kein OSError und würde sonst durchschlagen.
        except (OSError, EOFError, zlib.error) as exc:
            raise SnapshotError(
                f"Die Datei ist beschädigt oder unvollständig ({exc}). "
                f"Lade sie erneut hoch."
            ) from exc

    try:
        payload = json.loads(data.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise SnapshotError(
            "Die Datei ist keine gültige Textdatei. Erwartet wird ein Backup, "
            "das mit `/backup` erstellt wurde."
        ) from exc
    except json.JSONDecodeError as exc:
        raise SnapshotError(f"Die Datei enthält kein gültiges JSON: {exc}") from exc

    if not isinstance(payload, dict):
        raise SnapshotError("Die Datei enthält kein Backup-Objekt.")

    try:
        return GuildBlueprint.from_dict(payload)
    except (TypeError, ValueError) as exc:
        raise SnapshotError(f"Backup nicht lesbar: {exc}") from exc


def save_to_disk(snapshot: Snapshot) -> Path | None:
    """Legt zusätzlich eine Kopie neben dem Bot ab. Fehler sind nicht fatal."""
    try:
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        path = BACKUP_DIR / snapshot.filename
        path.write_bytes(snapshot.data)
        return path
    except OSError as exc:
        log.warning("Backup konnte nicht lokal gespeichert werden: %s", exc)
        return None


def human_size(num_bytes: int) -> str:
    if num_bytes < 1024:
        return f"{num_bytes} B"
    if num_bytes < 1024 * 1024:
        return f"{num_bytes / 1024:.1f} KB"
    return f"{num_bytes / (1024 * 1024):.1f} MB"
