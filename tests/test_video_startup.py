from pathlib import Path


def test_transcoder_keeps_first_random_access_frame():
    source = (Path(__file__).resolve().parents[1] / 'bridge/eufy_stream.py').read_text()
    command = source.split('ffmpeg_proc = await asyncio.create_subprocess_exec(', 1)[1].split('stdin=', 1)[0]
    assert '"nobuffer"' not in command
    inputs = source.split('if GRID_2X2:', 1)[1].split('ffmpeg_proc = await', 1)[0]
    assert '"nobuffer"' not in inputs
    assert 'input_args = ["-probesize", "32", "-analyzeduration", "0", "-f", "hevc"]' in inputs
    assert '*input_args' in command
