"""Synthetic HEVC timing test; no camera access or household media."""

import re
import subprocess
import tempfile
import time
from pathlib import Path

import pytest


def test_real_ffmpeg_paced_hevc_keeps_wallclock_duration():
    imageio_ffmpeg = pytest.importorskip('imageio_ffmpeg')
    av = pytest.importorskip('av')
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bridge'))
    from video_pipeline import grid_encoder_args
    binary = imageio_ffmpeg.get_ffmpeg_exe()
    generated = subprocess.run([
        binary, '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=size=320x180:rate=20',
        '-frames:v', '100', '-c:v', 'libx265', '-preset', 'ultrafast',
        '-x265-params', 'aud=1:repeat-headers=1:bframes=0:pools=1:frame-threads=1:log-level=error',
        '-f', 'hevc', 'pipe:1'], capture_output=True, check=True, timeout=20).stdout
    starts = [match.start() for match in re.finditer(b'\x00\x00\x00\x01\x46\x01', generated)]
    assert len(starts) == 100
    starts[0] = 0
    frames = [generated[start:end] for start, end in zip(starts, starts[1:] + [len(generated)])]
    inputs, outputs = grid_encoder_args()
    with tempfile.TemporaryDirectory(prefix='eufy-synthetic-timing-') as folder:
        target = Path(folder) / 'synthetic.ts'
        process = subprocess.Popen([
            binary, '-v', 'error', *inputs, '-i', 'pipe:0', *outputs,
            '-f', 'mpegts', str(target)], stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            started = time.monotonic()
            for index, frame in enumerate(frames):
                delay = started + index / 20 - time.monotonic()
                if delay > 0:
                    time.sleep(delay)
                process.stdin.write(frame)
                process.stdin.flush()
            process.stdin.close()
            assert process.wait(timeout=10) == 0
            with av.open(str(target)) as container:
                decoded = list(container.decode(video=0))
            duration = float(decoded[-1].time - decoded[0].time)
            assert 4.2 <= duration <= 5.5, duration
            assert 42 <= len(decoded) <= 56, len(decoded)
            assert (decoded[-1].width, decoded[-1].height) == (1280, 720)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
