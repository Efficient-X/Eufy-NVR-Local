"""Persistent, app-wide setup retry budget. One service process owns this file."""
import json
import math
import time
from pathlib import Path

from auth_login import _write_private_json


class SetupBudget:
    def __init__(self, path, *, clock=time.time, limit=5, window=60):
        self.path = Path(path)
        self.clock, self.limit, self.window = clock, limit, window
        self.attempts, self.blocked_until = [], 0.0
        self.failed = False
        try:
            if self.path.exists():
                if self.path.stat().st_size > 8192:
                    raise ValueError("Invalid budget")
                data = json.loads(self.path.read_text(encoding="utf-8"))
                attempts, blocked = data["attempts"], data["blocked_until"]
                if (data.get("version") != 1 or not isinstance(attempts, list)
                        or len(attempts) > limit
                        or any(type(t) not in (int, float) or not math.isfinite(t) or t < 0
                               for t in [*attempts, blocked])):
                    raise ValueError("Invalid budget")
                self.attempts, self.blocked_until = attempts, blocked
        except (OSError, ValueError, KeyError, TypeError):
            # Corrupt protection must not silently turn into unlimited logins.
            self.failed = True

    def _save(self):
        if self.failed:
            raise OSError("Setup protection is unavailable")
        try:
            _write_private_json(str(self.path), {
                "version": 1, "attempts": self.attempts,
                "blocked_until": self.blocked_until,
            })
        except OSError:
            self.failed = True
            raise OSError("Setup protection is unavailable") from None

    def reserve(self):
        """Persist before contacting Eufy; return seconds to wait, or zero."""
        if self.failed:
            raise OSError("Setup protection is unavailable")
        now = self.clock()
        if self.blocked_until > now:
            return math.ceil(self.blocked_until - now)
        self.attempts = [at for at in self.attempts if now - at < self.window]
        if len(self.attempts) >= self.limit:
            return max(1, math.ceil(min(self.attempts) + self.window - now))
        self.attempts.append(now)
        self._save()
        return 0

    def block(self, seconds):
        self.blocked_until = max(self.blocked_until, self.clock() + seconds)
        self._save()

