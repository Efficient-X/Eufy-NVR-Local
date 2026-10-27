import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "multichannel_probe", Path(__file__).resolve().parents[1] / "bridge/multichannel_probe.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_only_allowlisted_metadata_retained():
    probe = module.MultichannelProbe()
    for channel in (0, 1, 2, 2):
        frame = bytearray(42)
        frame[:4] = b"XZYH"
        frame[4:6] = (1300).to_bytes(2, "little")
        frame[12] = channel
        frame[38:] = b"test"
        assert probe.observe(bytes(frame))
    assert probe.summary() == {"channel_frames": {0: 1, 1: 1, 2: 2},
                               "command_frames": {1300: 4},
                               "video_bytes": 16, "invalid_frames": 0}
    assert all(not isinstance(v, bytes) for v in vars(probe).values())


def test_invalid_and_nonvideo_frames_are_not_counted():
    probe = module.MultichannelProbe()
    assert not probe.observe(b"XZYH")
    assert not probe.observe(b"bad!" + bytes(40))
    assert not probe.observe(b"XZYH" + bytes(40))
    assert probe.summary()["invalid_frames"] == 2
    assert probe.summary()["channel_frames"] == {}
