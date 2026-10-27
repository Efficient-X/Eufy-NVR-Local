"""Low-load shared-grid encoding and bounded, fail-closed video handoff."""

import asyncio
import time


def grid_encoder_args():
    """Use arrival timing, not an invented input FPS; cap output work."""
    inputs = ['-probesize', '512000', '-analyzeduration', '1000000',
              '-threads', '2', '-use_wallclock_as_timestamps', '1']
    outputs = ['-filter_threads', '1', '-vf',
               'setpts=PTS-STARTPTS,fps=10,scale=1280:720:flags=fast_bilinear',
               '-c:v', 'libx264', '-preset', 'ultrafast', '-tune', 'zerolatency',
               '-threads', '2', '-pix_fmt', 'yuv420p', '-g', '10',
               '-keyint_min', '10', '-sc_threshold', '0', '-bf', '0']
    return inputs, outputs


class BoundedVideoWriter:
    """Never accumulate unlimited compressed video behind a slow encoder.

    On overflow/stall retire the complete producer, rather than drop arbitrary
    HEVC interframes and continue with a corrupted or increasingly old picture.
    The existing producer supervisor performs bounded reconnect/backoff.
    """

    def __init__(self, writer, on_failure, *, max_bytes=1024 * 1024,
                 max_age=1.0, clock=time.monotonic):
        self.writer = writer
        self.on_failure = on_failure
        self.max_bytes = max_bytes
        self.max_age = max_age
        self.clock = clock
        self.queue = asyncio.Queue(maxsize=64)
        self.pending_bytes = 0
        self.failed = False
        self.closed = False
        self.task = asyncio.create_task(self._pump())

    def submit(self, data):
        if self.failed or self.closed:
            return
        if self.queue.full() or self.pending_bytes + len(data) > self.max_bytes:
            self._fail('byte_limit')
            return
        self.pending_bytes += len(data)
        self.queue.put_nowait((self.clock(), data))

    def _fail(self, reason):
        if self.failed or self.closed:
            return
        self.failed = True
        while not self.queue.empty():
            _, data = self.queue.get_nowait()
            self.pending_bytes -= len(data)
        self.on_failure(reason)

    async def _pump(self):
        try:
            while not self.failed and not self.closed:
                received, data = await self.queue.get()
                remaining = self.max_age - (self.clock() - received)
                if remaining <= 0:
                    self.pending_bytes -= len(data)
                    self._fail('age_limit')
                    return
                try:
                    self.writer.write(data)
                    await asyncio.wait_for(self.writer.drain(), remaining)
                finally:
                    self.pending_bytes -= len(data)
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            self._fail('drain_timeout')
        except Exception:
            self._fail('pipe_error')

    async def close(self):
        self.closed = True
        self.task.cancel()
        await asyncio.gather(self.task, return_exceptions=True)
        while not self.queue.empty():
            _, data = self.queue.get_nowait()
            self.pending_bytes -= len(data)
