"""Allowlisted, in-memory metadata for a bounded multi-channel protocol test."""

from collections import Counter


class MultichannelProbe:
    """Count video by XZYH channel without retaining media or identifiers."""

    def __init__(self):
        self.channels = Counter()
        self.commands = Counter()
        self.bytes = 0
        self.invalid = 0

    def observe(self, frame: bytes) -> bool:
        if len(frame) < 38 or frame[:4] != b"XZYH":
            self.invalid += 1
            return False
        command = int.from_bytes(frame[4:6], "little")
        if command not in (1300, 1301, 1303):
            return False
        self.channels[frame[12]] += 1
        self.commands[command] += 1
        self.bytes += len(frame) - 38
        return True

    def summary(self) -> dict:
        return {"channel_frames": dict(sorted(self.channels.items())),
                "command_frames": dict(sorted(self.commands.items())),
                "video_bytes": self.bytes, "invalid_frames": self.invalid}
