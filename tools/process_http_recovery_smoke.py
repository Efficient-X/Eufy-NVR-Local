"""Bounded Windows-only synthetic HTTP/process test. NEVER a HA adapter.

No household API, credentials, Docker, persistent listener or remote machine.
The fake service provides explicit operation fences NOT established for HA.
Only temporary child processes created by this file are ever terminated.
"""
from __future__ import annotations

import asyncio
from contextlib import suppress
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import time

from aiohttp import ClientSession, ClientTimeout, web

HERE = Path(__file__).resolve().parent
PLAN = HERE / 'recovery_plan.py'
PLAN_SHA = 'b2fb89d3ab328413ac245aa227e09d881b1216b3fdacd0378d7fd5a3df37ff60'
assert hashlib.sha256(PLAN.read_bytes()).hexdigest() == PLAN_SHA, 'Reviewed model bytes changed'
spec = importlib.util.spec_from_file_location('reviewed_recovery_plan', PLAN)
model = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = model
spec.loader.exec_module(model)

CHILD = '''import json,time
sequence=0
while True:
    sequence+=1
    print(json.dumps({'sequence':sequence}),flush=True)
    time.sleep(.01)
'''
SCALE = .02  # 180 model seconds = 3.6 actual seconds; not a real 3-minute test.


class SyntheticService:
    def __init__(self, *, blocked=(), delays=None):
        self.blocked, self.delays = set(blocked), delays or {}
        self.processes, self.latest, self.sampled = {}, {}, {}
        self.all_processes, self.readers, self.jobs, self.commands = [], [], [], []
        self.fences, self.overlap = set(), False
        self.locks = {name:asyncio.Lock() for name in ('stable','candidate')}

    async def _read(self, name, process):
        while line := await process.stdout.readline():
            self.latest[name] = json.loads(line)['sequence']

    async def _spawn(self, name):
        assert not self.running(name), 'Repeated process start'
        other = 'stable' if name == 'candidate' else 'candidate'
        assert not self.running(other), 'Would run both synthetic engines'
        self.latest[name] = self.sampled[name] = 0
        process = await asyncio.create_subprocess_exec(sys.executable,'-u','-c',CHILD,
            stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL)
        self.processes[name] = process
        self.all_processes.append(process)
        self.readers.append(asyncio.create_task(self._read(name,process)))
        async with asyncio.timeout(2):
            while not self.latest.get(name):await asyncio.sleep(.005)

    def running(self, name):
        return name in self.processes and self.processes[name].returncode is None

    async def _stop(self, name):
        process = self.processes.get(name)
        if process and process.returncode is None:
            process.terminate()
            await asyncio.wait_for(process.wait(),2)

    async def _operation(self, intent):
        name, action, operation = intent['engine'],intent['action'],intent['id']
        async with self.locks[name]:
            if operation in self.blocked:
                await asyncio.Event().wait()  # Deliberately unresolved; cleaned at end.
            await asyncio.sleep(self.delays.get(operation,2)*SCALE)
            if action == 'start':await self._spawn(name)
            else:await self._stop(name)
            self.fences.add(operation)  # Receipt after actual process operation completes.

    async def post_command(self, request):
        intent = await request.json()
        allowed = {f'{name}-{action}' for name in self.locks for action in ('start','stop')}
        if (set(intent) != {'id','engine','action','at'} or intent['id'] not in allowed
                or intent['id'] != f"{intent['engine']}-{intent['action']}"):
            return web.json_response({'error':'invalid synthetic command'},status=400)
        if any(c['id']==intent['id'] for c in self.commands):
            return web.json_response({'error':'duplicate mutation'},status=409)
        self.commands.append(intent)
        self.jobs.append(asyncio.create_task(self._operation(intent)))
        # Accept/start the operation, but delay its response beyond the CLIENT
        # timeout. The operation continues independently after disconnection.
        await asyncio.sleep(.15)
        return web.json_response({'accepted':intent['id']},status=202)

    async def get_status(self, request):
        fresh={}
        for name in self.locks:
            sequence=self.latest.get(name,0)
            fresh[name]=8 if self.running(name) and sequence>self.sampled.get(name,0) else 0
            self.sampled[name]=sequence
        return web.json_response({
            'stable':'running' if self.running('stable') else 'stopped',
            'candidate':'running' if self.running('candidate') else 'stopped',
            'complete':sorted(self.fences),'stable_fresh_views':fresh['stable'],
            # Candidate deliberately emits synthetic heartbeat bytes but never
            # supplies accepted video readiness in this failure scenario.
            'candidate_fresh_views':0})

    async def monitor(self):
        while True:
            if self.running('stable') and self.running('candidate'):self.overlap=True
            await asyncio.sleep(.002)

    async def close(self):
        for job in self.jobs:job.cancel()
        await asyncio.gather(*self.jobs,return_exceptions=True)
        for name in self.locks:await self._stop(name)
        await asyncio.gather(*self.readers,return_exceptions=True)
        assert all(p.returncode is not None for p in self.all_processes), 'Synthetic process leaked'


async def scenario(name, *, blocked=(), delays=None, expected):
    service=SyntheticService(blocked=blocked,delays=delays)
    await service._spawn('stable')
    app=web.Application()
    app.router.add_get('/synthetic/status',service.get_status)
    app.router.add_post('/synthetic/commands',service.post_command)
    runner=web.AppRunner(app,access_log=None)
    await runner.setup()
    monitor=asyncio.create_task(service.monitor())
    try:
        site=web.TCPSite(runner,'127.0.0.1',0)
        await site.start()
        port=runner.addresses[0][1]
        url=f'http://127.0.0.1:{port}/synthetic'
        plan=model.RecoveryPlan()
        response_timeouts=[]
        clock=time.monotonic()
        with tempfile.TemporaryDirectory(prefix='efx-synthetic-recovery-') as temporary:
            journal=Path(temporary)/'journal.json'
            async with ClientSession(timeout=ClientTimeout(total=.06)) as client:
                async with asyncio.timeout(10):
                    while True:
                        async with client.get(url+'/status') as response:
                            assert response.status==200
                            data=await response.json()
                        data['complete']=frozenset(data['complete'])
                        # Preflight waits for an actual new child heartbeat.
                        if plan.phase=='preflight' and not data['stable_fresh_views']:
                            await asyncio.sleep(.01)
                            continue
                        intents=plan.advance((time.monotonic()-clock)/SCALE,model.Observation(**data))
                        temporary_journal=journal.with_suffix('.tmp')
                        temporary_journal.write_text(json.dumps(plan.checkpoint()))
                        temporary_journal.replace(journal)  # Durable intent BEFORE HTTP dispatch.
                        for intent in intents:
                            try:
                                async with client.post(url+'/commands',json=intent) as response:
                                    assert response.status==202
                            except TimeoutError:
                                response_timeouts.append(intent['id'])  # Never replay.
                        if plan.phase in {'restored','accepted','manual-recovery-required'}:break
                        await asyncio.sleep(.005)
                persisted=json.loads(journal.read_text())
        elapsed=time.monotonic()-clock
        assert plan.phase==expected,(name,plan.phase)
        assert plan.journal==service.commands==persisted['journal']
        assert len({c['id'] for c in service.commands})==len(service.commands)
        assert response_timeouts, 'Real accepted-response timeout was not exercised'
        assert not service.overlap
        if expected=='restored':
            assert service.running('stable') and not service.running('candidate')
            assert 'candidate-stop' in service.fences and 'stable-start' in service.fences
            assert elapsed<180*SCALE+.5
        if 'candidate-stop' in blocked:
            assert not any(c['id']=='stable-start' for c in service.commands)
        receipt={'name':name,'result':plan.phase,'actual_seconds':round(elapsed,3),
                 'model_seconds':round(elapsed/SCALE,2),'response_timeouts':response_timeouts,
                 'commands':service.commands,'completed_fences':sorted(service.fences),
                 'journal_readback_matches':True,'concurrent_engines_observed':False,
                 'synthetic_pids':[p.pid for p in service.all_processes]}
    finally:
        monitor.cancel()
        with suppress(asyncio.CancelledError):await monitor
        await service.close()
        await runner.cleanup()
    receipt['all_synthetic_processes_terminated']=True
    receipt['loopback_listener_closed']=True
    return receipt


async def main():
    receipts=[]
    for name,options in [
        ('accepted_timeout_slow_restore',dict(delays={'candidate-stop':25,'stable-start':60},expected='restored')),
        ('unresolved_candidate_stop',dict(blocked={'candidate-stop'},expected='manual-recovery-required')),
        ('old_start_exceeds_budget',dict(delays={'stable-start':150},expected='manual-recovery-required')),
    ]:
        receipts.append(await scenario(name,**options))
    report={'kind':'Real local HTTP/subprocess integration; NOT a Home Assistant Supervisor test',
            'candidate_model_sha256':PLAN_SHA,
            'harness_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'time_scale':SCALE,'scenarios':receipts,
            'limitations':['Fake service supplies completion fences not validated for Supervisor',
                           'Synthetic heartbeat counters, no camera frames',
                           'Scaled time, not real 180-second HA recovery',
                           'Windows child processes, not Linux/S6/container process groups'],
            'household_calls':0,'remote_calls':0,'installed_software':False}
    (Path(tempfile.gettempdir())/'EFX_SYNTHETIC_PROCESS_HTTP_RESULTS.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))


if __name__=='__main__':asyncio.run(main())
