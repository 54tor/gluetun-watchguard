"""Anti-flap logic: only act after sustained failures, never in a tight loop."""

from __future__ import annotations

import time
from collections.abc import Callable


class FailureTracker:
    """Decide when a recovery action is warranted.

    An action is only allowed when ALL of these hold:

    * ``threshold`` consecutive failures have been recorded;
    * the tracker is past its ``grace`` window (things may still be booting);
    * at least ``cooldown`` seconds elapsed since the last action.

    This deliberately errs on the side of *not* acting, so a slow disk or a
    container that is merely slow to come back up never triggers a restart
    storm. After an action the grace window restarts, giving the restarted
    container time to recover before failures are counted again.
    """

    def __init__(
        self,
        threshold: int,
        cooldown: int,
        grace: int,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._threshold = max(1, threshold)
        self._cooldown = max(0, cooldown)
        self._grace = max(0, grace)
        self._clock = clock
        self._consecutive = 0
        self._started_at = clock()
        self._last_action_at: float | None = None

    @property
    def consecutive(self) -> int:
        return self._consecutive

    @property
    def threshold(self) -> int:
        return self._threshold

    def record_success(self) -> None:
        self._consecutive = 0

    def record_failure(self) -> None:
        self._consecutive += 1

    def _in_grace(self) -> bool:
        return (self._clock() - self._started_at) < self._grace

    def _in_cooldown(self) -> bool:
        if self._last_action_at is None:
            return False
        return (self._clock() - self._last_action_at) < self._cooldown

    def should_act(self) -> bool:
        if self._consecutive < self._threshold:
            return False
        if self._in_grace():
            return False
        if self._in_cooldown():
            return False
        return True

    def mark_action(self) -> None:
        """Record that an action was taken; reset counters and start cooldown."""
        now = self._clock()
        self._last_action_at = now
        # Treat the moment after an action like a fresh boot: give the
        # restarted container a full grace window to recover.
        self._started_at = now
        self._consecutive = 0
