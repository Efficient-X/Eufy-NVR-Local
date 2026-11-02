"""OFFLINE decision model only. No HA adapter, networking, credentials or CLI.

An executor MUST serialize commands per engine and provide a completion fence
after each accepted operation, even if its HTTP response times out. A sampled
'stopped' state alone is not that fence: an accepted start could still be queued.
This model cannot make an unbounded Supervisor operation meet a time guarantee.
"""
from dataclasses import asdict, dataclass, field


@dataclass(frozen=True)
class Observation:
    stable: str
    candidate: str
    complete: frozenset[str] = frozenset()
    stable_fresh_views: int = 0
    candidate_fresh_views: int = 0


@dataclass
class RecoveryPlan:
    phase: str = "preflight"
    started: float | None = None
    last_now: float = -1
    abort_trial: bool = False
    journal: list = field(default_factory=list)
    total_budget: float = 180
    trial_cutoff: float = 55
    stable_stop_budget: float = 20
    required_views: int = 8

    def _issue(self, engine, action, now):
        operation = f"{engine}-{action}"
        if any(item["id"] == operation for item in self.journal):
            raise RuntimeError("Mutation already journalled; reconcile rather than retry")
        intent = {"id": operation, "engine": engine, "action": action, "at": now}
        self.journal.append(intent)  # Persist BEFORE handing the intent to an executor.
        return [intent]

    def advance(self, now, observed):
        """One nonblocking decision. Caller journals state before executing output.

        Fresh-view counts must represent moving frame evidence, not TCP, ICE,
        process-running or cached-image checks. Simulation supplies synthetic
        evidence; this says nothing about the real recorder's video.
        """
        if now < self.last_now:
            raise ValueError("Use a monotonic clock")
        self.last_now = now
        if self.phase in {"restored", "accepted", "manual-recovery-required"}:
            return []
        if self.phase == "preflight":
            if (observed.stable != "running" or observed.candidate != "stopped"
                    or observed.stable_fresh_views < self.required_views):
                raise ValueError("Do not begin without a verified working baseline and stopped candidate")
            self.started = now
            self.phase = "stopping-stable"
            return self._issue("stable", "stop", now)
        elapsed = now - self.started
        if elapsed >= self.total_budget:
            self.phase = "manual-recovery-required"
            return []  # Truthful deadline failure; never imply a timed-out call failed to apply.
        if self.phase == "stopping-stable":
            if elapsed >= self.stable_stop_budget:
                self.abort_trial = True
            if observed.stable == "stopped" and "stable-stop" in observed.complete:
                if self.abort_trial or observed.candidate != "stopped":
                    self.phase = "stopping-candidate"
                    return self._issue("candidate", "stop", now)
                self.phase = "trial"
                return self._issue("candidate", "start", now)
        elif self.phase == "trial":
            if (not self.abort_trial and elapsed < self.trial_cutoff
                    and "candidate-start" in observed.complete and observed.stable == "stopped"
                    and observed.candidate == "running"
                    and observed.candidate_fresh_views >= self.required_views):
                self.phase = "accepted"
            elif elapsed >= self.trial_cutoff or self.abort_trial:
                self.phase = "stopping-candidate"
                return self._issue("candidate", "stop", now)
        elif self.phase == "stopping-candidate":
            if observed.candidate == "stopped" and "candidate-stop" in observed.complete:
                self.phase = "restoring-stable"
                return self._issue("stable", "start", now)
        elif self.phase == "restoring-stable":
            if (observed.candidate == "stopped" and "stable-start" in observed.complete
                    and observed.stable == "running"
                    and observed.stable_fresh_views >= self.required_views):
                self.phase = "restored"
        return []

    def checkpoint(self):
        return asdict(self)

    @classmethod
    def resume(cls, checkpoint):
        """Trusted private model checkpoint, not a user input/configuration parser."""
        return cls(**checkpoint)
