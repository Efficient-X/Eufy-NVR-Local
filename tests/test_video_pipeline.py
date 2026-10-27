import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bridge'))
from video_pipeline import BoundedVideoWriter, grid_encoder_args


class Writer:
    def __init__(self, stall=False):
        self.data = []
        self.stall = stall

    def write(self, data):
        self.data.append(data)

    async def drain(self):
        if self.stall:
            await asyncio.Event().wait()


def test_grid_limits_work_and_preserves_arrival_timeline():
    inputs, outputs = grid_encoder_args()
    assert '-r' not in inputs
    assert inputs[inputs.index('-use_wallclock_as_timestamps') + 1] == '1'
    assert 'fps=10' in outputs[outputs.index('-vf') + 1]
    assert 'scale=1280:720' in outputs[outputs.index('-vf') + 1]
    assert outputs[outputs.index('-threads') + 1] == '2'
    assert '-bf' in outputs


@pytest.mark.asyncio
async def test_writer_delivers_in_order_and_cleans_up():
    writer, errors = Writer(), []
    bounded = BoundedVideoWriter(writer, errors.append)
    bounded.submit(b'one')
    bounded.submit(b'two')
    for _ in range(8):
        await asyncio.sleep(0)
    await bounded.close()
    assert writer.data == [b'one', b'two']
    assert not errors
    assert bounded.pending_bytes == 0


@pytest.mark.asyncio
async def test_overflow_fails_once_without_continuing_corrupted_stream():
    writer, errors = Writer(), []
    bounded = BoundedVideoWriter(writer, errors.append, max_bytes=4)
    bounded.submit(b'one')
    bounded.submit(b'two')
    bounded.submit(b'more')
    await bounded.close()
    assert errors == ['byte_limit']
    assert writer.data == []
    assert bounded.pending_bytes == 0


@pytest.mark.asyncio
async def test_stalled_encoder_fails_with_bounded_age():
    errors = []
    bounded = BoundedVideoWriter(Writer(stall=True), errors.append, max_age=.01)
    bounded.submit(b'frame')
    await asyncio.wait_for(bounded.task, .5)
    assert errors == ['drain_timeout']
    await bounded.close()
    assert bounded.pending_bytes == 0


@pytest.mark.asyncio
async def test_old_queued_input_is_never_published():
    errors, writer = [], Writer()
    now = [0.]
    bounded = BoundedVideoWriter(writer, errors.append, clock=lambda: now[0])
    bounded.submit(b'old')
    now[0] = 2.
    await bounded.task
    await bounded.close()
    assert errors == ['age_limit']
    assert writer.data == []


@pytest.mark.asyncio
async def test_close_cancels_blocked_drain_without_reporting_failure():
    errors = []
    bounded = BoundedVideoWriter(Writer(stall=True), errors.append)
    bounded.submit(b'frame')
    await asyncio.sleep(0)
    await bounded.close()
    assert not errors
    assert bounded.pending_bytes == 0
