"""Run actual setup against HA's foreground/background lifecycle contract.

Offline unit regressions, not a replacement for real HA installation tests.
"""
import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


def make_harness(*, block_seed=False, seed_timeout=1.0):
    source = Path(__file__).resolve().parents[1] / "custom_components/eufy_nvr/__init__.py"
    module = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    setup = next(node for node in module.body
                 if isinstance(node, ast.AsyncFunctionDef) and node.name == "async_setup_entry")
    seed_started, seed_release, primer_started = (asyncio.Event() for _ in range(3))
    forwarded, warnings = [], []

    class Coordinator:
        def __init__(self, hass, entry):
            pass

        async def async_config_entry_first_refresh(self):
            pass

        async def async_prime_frames(self):
            seed_started.set()
            if block_seed:
                await seed_release.wait()

        async def async_prime_frames_forever(self):
            primer_started.set()
            await asyncio.Event().wait()

    class Hass:
        def __init__(self):
            self.regular, self.background, self.created = set(), set(), []
            self.config_entries = SimpleNamespace(async_forward_entry_setups=self.forward)

        async def forward(self, entry, platforms):
            forwarded.append(entry)

        def _create(self, target, name, bucket):
            task = asyncio.create_task(target, name=name)
            bucket.add(task)
            task.add_done_callback(bucket.discard)
            self.created.append(task)
            return task

        def async_create_task(self, target, name):
            return self._create(target, name, self.regular)

        def async_create_background_task(self, target, name, eager_start=True):
            return self._create(target, name, self.background)

        async def async_block_till_done(self):
            while self.regular:
                await asyncio.gather(*self.regular)

    class Entry:
        entry_id = "test"
        data = {"username": "test-user", "password": "test-only-placeholder"}

        def __init__(self):
            self.callbacks, self.background = [], set()

        def async_on_unload(self, callback):
            self.callbacks.append(callback)

        def async_create_background_task(self, hass, target, name, eager_start=True):
            task = hass.async_create_background_task(target, name, eager_start)
            self.background.add(task)
            task.add_done_callback(self.background.discard)
            return task

        async def unload(self):
            tasks = tuple(self.background)
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            for callback in self.callbacks:
                assert callback() is None

    namespace = {
        "asyncio": asyncio, "HomeAssistant": object, "EufyNvrConfigEntry": object,
        "CONF_USERNAME": "username", "CONF_PASSWORD": "password", "DOMAIN": "eufy_nvr",
        "PLATFORMS": [], "FRAME_SETUP_PRIME_TIMEOUT": seed_timeout,
        "validate_credentials": lambda username, password: None,
        "EufyNvrCoordinator": Coordinator, "ConfigEntryAuthFailed": RuntimeError,
        "async_register_card": AsyncMock(return_value=lambda: None),
        "_LOGGER": SimpleNamespace(warning=lambda *args: warnings.append(args)),
    }
    exec(compile(ast.Module(body=[setup], type_ignores=[]), str(source), "exec"), namespace)
    hass, entry = Hass(), Entry()

    async def cleanup():
        for task in hass.created:
            task.cancel()
        await asyncio.gather(*hass.created, return_exceptions=True)

    return SimpleNamespace(
        setup=namespace["async_setup_entry"], hass=hass, entry=entry,
        seed_started=seed_started, seed_release=seed_release, primer_started=primer_started,
        forwarded=forwarded, warnings=warnings, cleanup=cleanup,
    )


@pytest.mark.asyncio
async def test_lifetime_primer_does_not_block_ha_startup_completion():
    h = make_harness()
    try:
        assert await h.setup(h.hass, h.entry) is True
        await asyncio.wait_for(h.primer_started.wait(), 0.5)
        await asyncio.wait_for(h.hass.async_block_till_done(), 0.05)
        assert len(h.hass.background) == 1
        assert not h.hass.created[0].done()
        assert h.hass.created[0].get_name() == "eufy_nvr snapshot primer"
    finally:
        await h.cleanup()


@pytest.mark.asyncio
async def test_entry_owns_primer_and_unload_cancels_it():
    h = make_harness()
    try:
        assert await h.setup(h.hass, h.entry) is True
        await asyncio.wait_for(h.primer_started.wait(), 0.5)
        assert h.entry.background == set(h.hass.created)
        await h.entry.unload()
        assert h.hass.created[0].cancelled()
    finally:
        await h.cleanup()


@pytest.mark.asyncio
async def test_initial_seed_still_finishes_before_entities_are_forwarded():
    h = make_harness(block_seed=True)
    task = asyncio.create_task(h.setup(h.hass, h.entry))
    try:
        await asyncio.wait_for(h.seed_started.wait(), 0.5)
        assert not task.done()
        assert not h.forwarded
        assert not h.hass.created
        h.seed_release.set()
        assert await asyncio.wait_for(task, 0.5) is True
        assert h.forwarded == [h.entry]
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await h.cleanup()


@pytest.mark.asyncio
async def test_seed_timeout_still_exposes_entities_and_schedules_retry():
    h = make_harness(block_seed=True, seed_timeout=0.01)
    try:
        assert await asyncio.wait_for(h.setup(h.hass, h.entry), 0.5) is True
        await asyncio.wait_for(h.primer_started.wait(), 0.5)
        assert h.forwarded == [h.entry]
        assert len(h.warnings) == 1
        assert not h.hass.created[0].done()
    finally:
        await h.cleanup()
