"""Exercise the built image's actual video/audio tools with synthetic input only.

Run in a no-network, read-only container with a small writable /tmp. No camera,
account, Home Assistant or household filesystem is used.
"""
from pathlib import Path
import subprocess
import tempfile

import av


def encode(destination: Path, *arguments: str) -> None:
    result = subprocess.run(
        ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-nostdin', '-y',
         *arguments, str(destination)],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode:
        raise RuntimeError('Synthetic encoding failed: ' + result.stderr[-2000:])


def main() -> None:
    with tempfile.TemporaryDirectory(prefix='efx-codecs-') as directory:
        folder = Path(directory)
        for encoder, codec in [('libx264', 'h264'), ('libx265', 'hevc')]:
            target = folder / (codec + '.mkv')
            arguments = ['-f', 'lavfi', '-i', 'testsrc2=s=160x96:r=5',
                         '-frames:v', '3', '-c:v', encoder, '-preset', 'ultrafast',
                         '-threads', '1']
            if codec == 'hevc':
                arguments += ['-x265-params', 'pools=none:frame-threads=1:log-level=error']
            encode(target, *arguments)
            with av.open(str(target)) as container:
                frames = list(container.decode(video=0))
            if len(frames) != 3 or any((f.width, f.height) != (160, 96) for f in frames):
                raise RuntimeError('Synthetic video decode failed: ' + codec)
            print('PASS: FFmpeg encode -> PyAV decode ' + codec, flush=True)
        for encoder, suffix in [('libopus', '.ogg'), ('aac', '.m4a')]:
            target = folder / (encoder + suffix)
            encode(target, '-f', 'lavfi', '-i', 'anullsrc=r=48000:cl=stereo',
                   '-t', '0.1', '-c:a', encoder, '-threads', '1')
            with av.open(str(target)) as container:
                samples = sum(frame.samples for frame in container.decode(audio=0))
            if samples <= 0:
                raise RuntimeError('Synthetic audio decode failed: ' + encoder)
            print('PASS: FFmpeg encode -> PyAV decode ' + encoder, flush=True)


if __name__ == '__main__':
    main()
