"""Run the actual coordinator's priming methods without a HA installation."""

import ast
import asyncio
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest


def coordinator_type():
    source = Path(__file__).resolve().parents[1] / "custom_components/eufy_nvr/coordinator.py"
    tree = ast.parse(source.read_text())
    original = next(node for node in tree.body if isinstance(node, ast.ClassDef))
    methods = [node for node in original.body if isinstance(node, ast.AsyncFunctionDef)
               and node.name in {"async_prime_frames", "async_prime_frames_forever"}]
    wrapper = ast.ClassDef(name="Coordinator", bases=[], keywords=[], body=methods,
                           decorator_list=[])
    module = ast.fix_missing_locations(ast.Module(body=[wrapper], type_ignores=[]))
    namespace = {
        "asyncio": asyncio, "Go2RtcError": RuntimeError,
        "_LOGGER": logging.getLogger(__name__),
        "FRAME_PRIME_INTERVAL": 300, "FRAME_PRIME_RETRY_INITIAL": 5,
        "FRAME_PRIME_RETRY_MAX": 60, "SHARED_GRID_STREAM": "eufy_shared_grid",
    }
    exec(compile(module, str(source), "exec"), namespace)
    return namespace["Coordinator"], namespace


@pytest.mark.asyncio
async def test_shared_grid_startup_never_captures_any_camera():
    coordinator, _ = coordinator_type()
    instance = coordinator()
    instance.data = {name: {} for name in ("eufy_shared_grid", "eufy_a", "eufy_b", "eufy_c")}
    instance._primed_streams = {"eufy_a"}

    async def forbidden_capture(stream):
        pytest.fail(f"Automatic capture must not start {stream}")

    instance.async_get_frame = forbidden_capture
    assert await instance.async_prime_frames() == 0
    assert not instance._primed_streams


@pytest.mark.asyncio
async def test_shared_grid_background_only_sleeps_and_cancels_cleanly():
    coordinator, namespace = coordinator_type()
    instance = coordinator()
    instance.data = {"eufy_shared_grid": {}}
    sleeps = []

    async def sleep_then_cancel(seconds):
        sleeps.append(seconds)
        raise asyncio.CancelledError

    async def forbidden_prime():
        pytest.fail("Background shared-grid mode must not call the primer")

    namespace["asyncio"] = SimpleNamespace(sleep=sleep_then_cancel)
    instance.async_prime_frames = forbidden_prime
    with pytest.raises(asyncio.CancelledError):
        await instance.async_prime_frames_forever()
    assert sleeps == [namespace["FRAME_PRIME_INTERVAL"]]


@pytest.mark.asyncio
async def test_legacy_priming_remains_sequential_and_only_seeds_missing():
    coordinator, _ = coordinator_type()
    instance = coordinator()
    instance.data = {"eufy_b": {}, "eufy_a": {}, "eufy_c": {}}
    instance._primed_streams = {"eufy_a", "removed"}
    calls = []

    async def capture(stream):
        calls.append(stream)
        return b"jpeg"

    instance.async_get_frame = capture
    assert await instance.async_prime_frames() == 3
    assert calls == ["eufy_b", "eufy_c"]
    assert instance._primed_streams == set(instance.data)


@pytest.mark.asyncio
async def test_legacy_failed_capture_is_not_marked_primed():
    coordinator, _ = coordinator_type()
    instance = coordinator()
    instance.data = {"eufy_a": {}, "eufy_b": {}}
    instance._primed_streams = set()

    async def capture(stream):
        if stream == "eufy_a":
            raise RuntimeError("unavailable")
        return b"jpeg"

    instance.async_get_frame = capture
    assert await instance.async_prime_frames() == 1
    assert instance._primed_streams == {"eufy_b"}
