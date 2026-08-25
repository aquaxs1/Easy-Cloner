"""
Adaptive pacing of API calls.

discord.py waits out rate limits internally — from the outside you never see
a 429, only a call that took unusually long. That is exactly how the pacer
notices Discord throttling, and widens the gap between calls. When everything
runs smoothly it eases back towards the desired value.

That is faster than a fixed, defensively large pause, and gentler than a
fixed small one.
"""

from __future__ import annotations

import asyncio
import logging
import time

log = logging.getLogger(__name__)

# From this duration on, a single API call counts as throttled.
SLOW_CALL_SECONDS = 1.5

# Bounds the pause moves between.
MIN_DELAY = 0.25
MAX_DELAY = 5.0

# How sharply it rises when throttled, and falls again on a quiet run.
BACKOFF_FACTOR = 1.6
RECOVER_FACTOR = 0.93

# This many undisturbed calls must pass before speeding up again.
RECOVER_AFTER = 5


class AdaptivePacer:
    """Holds the gap between API calls and keeps adjusting it."""

    def __init__(self, base_delay: float, *, cancel_event: asyncio.Event | None = None) -> None:
        self.base_delay = max(MIN_DELAY, base_delay)
        self.delay = self.base_delay
        self._cancel = cancel_event
        self._calm_streak = 0
        self.throttle_events = 0
        self.calls = 0

    # ── Messung ───────────────────────────────────────────────────────────────

    def observe(self, elapsed: float) -> None:
        """Reports back how long an API call took."""
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
        """Waits out the current pause. A cancellation takes effect at once."""
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
        """Runs an API call, measures it, then waits."""
        started = time.monotonic()
        try:
            return await coro
        finally:
            self.observe(time.monotonic() - started)

    @property
    def was_throttled(self) -> bool:
        return self.throttle_events > 0
