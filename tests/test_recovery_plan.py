"""Discrete-event failure tests. No household, HTTP, login or wall-clock sleeps."""
import importlib.util
import json
from pathlib import Path
import sys

import pytest

spec = importlib.util.spec_from_file_location("recovery_plan", Path(__file__).parents[1] / "tools/recovery_plan.py")
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
Observation, RecoveryPlan = module.Observation, module.RecoveryPlan


class SimulatedSupervisor:
    """Commands keep running after response timeout; each engine has one queue.

    'complete' is an explicit fenced operation receipt, stronger than a process
    state sample. No production adapter claims to provide these receipts yet.
    """
    def __init__(self, delays=None, lost=(), candidate_views=0, stable_views=8, response_timeout=5):
        self.state = {"stable":"running", "candidate":"stopped"}
        self.events, self.complete, self.calls, self.tails = [], set(), [], {}
        self.delays, self.lost = delays or {}, set(lost)
        self.candidate_views, self.stable_views = candidate_views, stable_views
        self.response_timeout = response_timeout
        self.uncertain = []

    def submit(self, intent):
        assert not any(c['id']==intent['id'] for c in self.calls), 'duplicate mutation'
        self.calls.append(dict(intent))
        engine, operation, at = intent['engine'], intent['id'], intent['at']
        if operation in self.lost:
            self.tails[engine] = float('inf')
            self.uncertain.append(operation)
            return
        finish = max(at,self.tails.get(engine,at)) + self.delays.get(operation,2)
        self.tails[engine] = finish
        self.events.append((finish,intent))
        if finish-at > self.response_timeout:
            self.uncertain.append(operation)  # Accepted even though response times out.

    def observe(self, at):
        for finish,intent in sorted(self.events,key=lambda e:e[0]):
            if finish <= at and intent['id'] not in self.complete:
                self.state[intent['engine']] = 'running' if intent['action']=='start' else 'stopped'
                self.complete.add(intent['id'])
            assert not all(state=='running' for state in self.state.values()), 'concurrent video engines'
        return Observation(**self.state,complete=frozenset(self.complete),
                           stable_fresh_views=self.stable_views if self.state['stable']=='running' else 0,
                           candidate_fresh_views=self.candidate_views if self.state['candidate']=='running' else 0)


def run(backend, *, checkpoint_at=None):
    plan = RecoveryPlan()
    for at in range(181):
        for intent in plan.advance(at,backend.observe(at)):
            backend.submit(intent)
        if at == checkpoint_at:
            plan = RecoveryPlan.resume(json.loads(json.dumps(plan.checkpoint())))
        if plan.phase in {'accepted','restored','manual-recovery-required'}:
            return plan,at
    pytest.fail('Unbounded recovery decision loop')


def test_discovery_failure_reserves_time_for_accepted_timeout_and_slow_old_start():
    backend = SimulatedSupervisor(delays={'candidate-stop':25,'stable-start':60})
    plan,finished = run(backend)
    assert plan.phase == 'restored' and finished == 140
    assert backend.uncertain == ['candidate-stop','stable-start']
    assert [(c['id'],c['at']) for c in backend.calls] == [
        ('stable-stop',0),('candidate-start',2),('candidate-stop',55),('stable-start',80)]


def test_running_or_connected_without_eight_moving_views_is_not_success():
    plan,_ = run(SimulatedSupervisor(candidate_views=7))
    assert plan.phase == 'restored'
    plan,finished = run(SimulatedSupervisor(candidate_views=8))
    assert plan.phase == 'accepted' and finished == 4


def test_queued_late_start_cannot_wake_after_fallback_starts():
    backend = SimulatedSupervisor(delays={'candidate-start':70,'candidate-stop':20})
    plan,finished = run(backend)
    assert plan.phase == 'restored' and finished == 94
    assert next(c for c in backend.calls if c['id']=='stable-start')['at'] == 92


def test_unknown_candidate_stop_blocks_old_start_even_if_last_sample_says_stopped():
    backend = SimulatedSupervisor(lost={'candidate-start'})
    plan,finished = run(backend)
    assert plan.phase == 'manual-recovery-required' and finished == 180
    assert backend.state['candidate'] == 'stopped'
    assert 'candidate-stop' not in backend.complete  # Queued behind unresolved start.
    assert not any(c['id']=='stable-start' for c in backend.calls)


def test_rollback_stop_never_completes_no_concurrent_engine_or_fake_success():
    backend = SimulatedSupervisor(lost={'candidate-stop'})
    plan,finished = run(backend)
    assert plan.phase == 'manual-recovery-required' and finished == 180
    assert len(backend.calls)==3 and backend.state['stable']=='stopped'


def test_slow_old_start_truthfully_misses_budget_without_retrying():
    backend = SimulatedSupervisor(delays={'stable-start':150})
    plan,finished = run(backend)
    assert plan.phase == 'manual-recovery-required' and finished == 180
    assert backend.uncertain == ['stable-start']
    assert len([c for c in backend.calls if c['id']=='stable-start'])==1


def test_stable_stop_over_budget_skips_trial_and_recovers_after_receipt():
    backend = SimulatedSupervisor(delays={'stable-stop':30})
    plan,finished = run(backend)
    assert plan.phase == 'restored' and finished == 34
    assert not any(c['id']=='candidate-start' for c in backend.calls)


def test_no_late_retry_after_controller_resume_with_accepted_unknown_operations():
    backend = SimulatedSupervisor(delays={'candidate-stop':25,'stable-start':60})
    plan,finished = run(backend,checkpoint_at=70)
    assert plan.phase=='restored' and finished==140 and len(backend.calls)==4
    assert plan.journal == backend.calls


def test_command_intent_is_durable_before_execution_and_failed_delivery_does_not_retry():
    plan = RecoveryPlan()
    intents = plan.advance(0,Observation('running','stopped',stable_fresh_views=8))
    checkpoint = json.loads(json.dumps(plan.checkpoint()))
    assert checkpoint['journal'] == intents
    plan = RecoveryPlan.resume(checkpoint)
    assert plan.advance(1,Observation('running','stopped')) == []
    assert plan.advance(180,Observation('running','stopped')) == []
    assert plan.phase=='manual-recovery-required'


@pytest.mark.parametrize('baseline',[
    Observation('running','running',stable_fresh_views=8),
    Observation('stopped','stopped'),
    Observation('running','stopped',stable_fresh_views=0)])
def test_bad_preflight_cannot_mutate(baseline):
    plan=RecoveryPlan()
    with pytest.raises(ValueError):plan.advance(0,baseline)
    assert plan.journal==[]


def test_monotonic_guard_and_terminal_hold():
    backend=SimulatedSupervisor(candidate_views=8)
    plan,finished=run(backend)
    with pytest.raises(ValueError):plan.advance(finished-1,backend.observe(finished))
    assert plan.advance(finished+100,backend.observe(finished+100))==[]
