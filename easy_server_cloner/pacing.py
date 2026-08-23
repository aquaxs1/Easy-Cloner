"""
Adaptive Taktung der API-Aufrufe.

discord.py wartet Rate-Limits intern selbst ab — nach außen sieht man kein 429,
sondern nur einen Aufruf, der ungewöhnlich lange gedauert hat. Genau daran
erkennt der Pacer, dass Discord bremst, und vergrößert den Abstand zwischen den
Aufrufen. Läuft alles glatt, nähert er sich wieder dem Wunschwert an.

Das ist schneller als eine fest eingestellte, sicherheitshalber große Pause und
zugleich schonender als eine fest eingestellte kleine.
"""

from __future__ import annotations

import asyncio
import logging
import time

log = logging.getLogger(__name__)

# Ab dieser Dauer gilt ein einzelner API-Aufruf als ausgebremst.
SLOW_CALL_SECONDS = 1.5

# Grenzen, zwischen denen sich die Pause bewegt.
MIN_DELAY = 0.25
MAX_DELAY = 5.0

# Wie stark bei Bremsung erhöht bzw. bei ruhigem Lauf wieder gesenkt wird.
BACKOFF_FACTOR = 1.6
RECOVER_FACTOR = 0.93

# So viele störungsfreie Aufrufe müssen vergehen, bevor wieder beschleunigt wird.
RECOVER_AFTER = 5


class AdaptivePacer:
    """Hält den Abstand zwischen API-Aufrufen und passt ihn laufend an."""

    def __init__(self, base_delay: float, *, cancel_event: asyncio.Event | None = None) -> None:
        self.base_delay = max(MIN_DELAY, base_delay)
        self.delay = self.base_delay
        self._cancel = cancel_event
        self._calm_streak = 0
        self.throttle_events = 0
        self.calls = 0

    # ── Messung ───────────────────────────────────────────────────────────────

    def observe(self, elapsed: float) -> None:
        """Meldet die Dauer eines API-Aufrufs zurück."""
        self.calls += 1
        if elapsed >= SLOW_CALL_SECONDS:
            self.throttle_events += 1
            self._calm_streak = 0
            previous = self.delay
            self.delay = min(MAX_DELAY, self.delay * BACKOFF_FACTOR)
            if self.delay > previous:
                log.debug(
                    "Discord bremst (%.1fs) — Pause %.2fs → %.2fs", elapsed, previous, self.delay
                )
            return

        self._calm_streak += 1
        if self._calm_streak >= RECOVER_AFTER and self.delay > self.base_delay:
            self.delay = max(self.base_delay, self.delay * RECOVER_FACTOR)
            self._calm_streak = 0

    # ── Warten ────────────────────────────────────────────────────────────────

    async def wait(self) -> None:
        """Wartet die aktuelle Pause ab. Ein Abbruch wirkt sofort."""
        if self._cancel is not None and self._cancel.is_set():
            raise asyncio.CancelledError
        if self.delay <= 0:
            return
        if self._cancel is None:
            await asyncio.sleep(self.delay)
            return
        try:
            await asyncio.wait_for(self._cancel.wait(), timeout=self.delay)
        except asyncio.TimeoutError:
            return
        raise asyncio.CancelledError

    async def call(self, coro):
        """Führt einen API-Aufruf aus, misst ihn und wartet anschließend."""
        started = time.monotonic()
        try:
            return await coro
        finally:
            self.observe(time.monotonic() - started)

    @property
    def was_throttled(self) -> bool:
        return self.throttle_events > 0
