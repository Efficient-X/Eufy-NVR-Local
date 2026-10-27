"""Privacy-safe codec and encoder diagnostics; never return input bytes/lines."""

import re


def annexb_codec(data: bytes) -> str:
    for match in list(re.finditer(b"\x00\x00\x00?\x01", data))[:32]:
        header = data[match.end():match.end() + 2]
        if len(header) < 2 or header[0] & 0x80:
            continue
        if header[0] & 0x1F in (7, 8):
            return "h264"
        if (header[0] >> 1) & 0x3F in (32, 33, 34) and header[1] & 7:
            return "hevc"
    return "unknown"


def ffmpeg_error_category(line: str) -> str | None:
    value = line.lower()
    categories = (("401 unauthorized", "publish_auth"),
                  ("404 not found", "publish_not_found"),
                  ("connection refused", "publish_unreachable"),
                  ("invalid data found", "invalid_input"),
                  ("could not find codec parameters", "codec_parameters"),
                  ("unknown encoder", "encoder_missing"),
                  ("error while opening encoder", "encoder_open"),
                  ("could not write header", "publish_header"),
                  ("broken pipe", "publish_closed"))
    return next((category for marker, category in categories if marker in value), None)
