"""Track only a launched Linux worker's descendants, including nested sessions.

PID file descriptors bind signals to a particular process, not a reused number.
Only /proc/stat fields are read; command lines and environments are never read.
"""
from __future__ import annotations

import os
from pathlib import Path
import select
import signal


def require_support():
    if not hasattr(os, "pidfd_open") or not hasattr(signal, "pidfd_send_signal"):
        raise RuntimeError("Linux process ownership support is required")
    handle = os.pidfd_open(os.getpid())
    os.close(handle)


def process_record(pid):
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        # state, parent PID, process group, ... start time (field 22).
        return int(fields[1]), int(fields[2]), int(fields[19])
    except (OSError, ValueError, IndexError):
        return None


def process_records():
    records = {}
    for path in Path("/proc").iterdir():
        if path.name.isascii() and path.name.isdecimal():
            pid = int(path.name)
            record = process_record(pid)
            if record is not None:
                records[pid] = record
    return records


class OwnedProcesses:
    def __init__(self, root_pid):
        self.root_pid = root_pid
        self.root_identity = process_record(root_pid)
        if (self.root_identity is None or self.root_identity[0] != os.getpid()
                or self.root_identity[1] != root_pid):
            raise RuntimeError("Worker process ownership could not be confirmed")
        self.handles = {}  # pid -> (kernel start time, pidfd)

    def refresh(self):
        records = process_records()
        # The worker owns a new process group. Preserve already captured children
        # after they start new sessions or are reparented during shutdown.
        owned = {pid for pid, (started, _) in self.handles.items()
                 if pid in records and records[pid][2] == started}
        if records.get(self.root_pid) == self.root_identity:
            owned.add(self.root_pid)
        # Once the original group has no identified member, never adopt a later
        # unrelated group that happens to reuse the same numeric group ID.
        if any(records[pid][1] == self.root_pid for pid in owned):
            owned.update(pid for pid, (_, group, _) in records.items() if group == self.root_pid)
        while True:
            children = {pid for pid, (parent, _, _) in records.items() if parent in owned}
            new = children - owned
            if not new:
                break
            owned.update(new)
        owned.discard(os.getpid())
        for pid in owned:
            if pid in self.handles and self.handles[pid][0] == records[pid][2]:
                continue
            try:
                handle = os.pidfd_open(pid)
            except ProcessLookupError:
                continue
            # Do not retain a handle if the numeric PID changed during capture.
            if process_record(pid) != records[pid]:
                os.close(handle)
                continue
            if pid in self.handles:
                os.close(self.handles[pid][1])
            self.handles[pid] = (records[pid][2], handle)

    def running(self):
        return any(not select.select([handle], [], [], 0)[0]
                   for _, handle in self.handles.values())

    def kill(self):
        for _, handle in self.handles.values():
            try:
                signal.pidfd_send_signal(handle, signal.SIGKILL)
            except ProcessLookupError:
                pass

    def close(self):
        for _, handle in self.handles.values():
            os.close(handle)
        self.handles.clear()
