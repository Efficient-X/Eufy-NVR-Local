"""Split public CI source evidence into verified, downloadable tar parts."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path
import re
import tarfile


class PartsWriter(io.RawIOBase):
    def __init__(self, output: Path, prefix: str, limit: int):
        self.output, self.prefix, self.limit = output, prefix, limit
        self.handle = None
        self.current_size = 0
        self.current_hash = None
        self.whole_hash = hashlib.sha256()
        self.parts = []
        self.total = 0

    def writable(self):
        return True

    def finish_part(self):
        if self.handle is not None:
            self.handle.close()
            self.parts.append(dict(file=self.handle.name.rsplit('/', 1)[-1].rsplit('\\', 1)[-1],
                                   size=self.current_size, sha256=self.current_hash.hexdigest()))
            self.handle = None

    def write(self, data):
        original = len(data)
        view = memoryview(data)
        while view:
            if self.handle is None:
                if len(self.parts) >= 32:
                    raise ValueError('Source archive has too many parts')
                path = self.output / f'{self.prefix}.tar.part{len(self.parts):02}'
                self.handle = path.open('xb')
                self.current_size, self.current_hash = 0, hashlib.sha256()
            size = min(len(view), self.limit - self.current_size)
            block, view = view[:size], view[size:]
            self.handle.write(block)
            self.current_hash.update(block)
            self.whole_hash.update(block)
            self.current_size += size
            self.total += size
            if self.current_size == self.limit:
                self.finish_part()
        return original

    def close(self):
        self.finish_part()
        super().close()


def bundle(source: Path, output: Path, architecture: str, revision: str,
           *, part_size=400 * 1024 * 1024) -> dict:
    if architecture not in ('amd64', 'aarch64') or not re.fullmatch('[0-9a-f]{40}', revision):
        raise ValueError('An explicit architecture and exact source revision are required')
    if not 1024 <= part_size <= 400 * 1024 * 1024:
        raise ValueError('Source parts must stay below the connector download limit')
    source = source.resolve(strict=True)
    output = output.absolute()
    if output.resolve().is_relative_to(source):
        raise ValueError('Keep archive output outside its input directory')
    paths = sorted(source.rglob('*'))
    if any(path.is_symlink() for path in paths):
        raise ValueError('Source evidence must not contain symlinks')
    output.mkdir(parents=True, exist_ok=False)
    prefix = f'eufy-nvr-sources-{architecture}'
    writer = PartsWriter(output, prefix, part_size)
    try:
        with tarfile.open(mode='w|', fileobj=writer, format=tarfile.PAX_FORMAT) as archive:
            for path in paths:
                if path.is_file():
                    name = 'source-review/' + path.relative_to(source).as_posix()
                    info = archive.gettarinfo(str(path), arcname=name)
                    # Keep public source bytes and executable flags, not runner identities.
                    info.uid = info.gid = info.mtime = 0
                    info.uname = info.gname = ''
                    with path.open('rb') as handle:
                        archive.addfile(info, handle)
    finally:
        writer.close()
    report = dict(schema=1, architecture=architecture, source_revision=revision,
                  format='concatenated-tar', size=writer.total,
                  sha256=writer.whole_hash.hexdigest(), parts=writer.parts)
    (output / f'{prefix}.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--architecture', choices=('amd64', 'aarch64'), required=True)
    parser.add_argument('--source-revision', required=True)
    args = parser.parse_args()
    report = bundle(args.source, args.output, args.architecture, args.source_revision)
    print(json.dumps(report))


if __name__ == '__main__':
    main()
