import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "video_diagnostics", Path(__file__).resolve().parents[1] / "bridge/video_diagnostics.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_codec_detection_uses_parameter_nals():
    assert module.annexb_codec(b"\0\0\0\1\x67\x64\0") == "h264"
    assert module.annexb_codec(b"\0\0\1\x40\x01\0") == "hevc"
    assert module.annexb_codec(b"\0\0\1\x42\x01\0") == "hevc"
    assert module.annexb_codec(b"not a media header") == "unknown"
    assert module.annexb_codec(b"\0\0\1") == "unknown"


def test_errors_are_allowlisted_not_raw_lines():
    secret_line = "rtsp://user:private@host:8558/secret 401 Unauthorized"
    assert module.ffmpeg_error_category(secret_line) == "publish_auth"
    assert module.ffmpeg_error_category("Invalid data found when processing input") == "invalid_input"
    assert module.ffmpeg_error_category("private unrecognized information") is None
