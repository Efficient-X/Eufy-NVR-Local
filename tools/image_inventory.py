"""Inventory an isolated build image; never run against household app data.

Only public software metadata and licence files under fixed system directories
are read. This does not collect /data, /config, environment variables or logs.
It deliberately reports pending review: an inventory is not corresponding source.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path
import platform
import shutil
import subprocess
from urllib.parse import quote


NOTICE_ROOTS = (
    Path('/usr/share/doc'), Path('/usr/share/common-licenses'),
    Path('/usr/share/licenses'), Path('/usr/local/lib'), Path('/usr/lib'),
)


def run(*args: str) -> str:
    result = subprocess.run(
        args, check=True, capture_output=True, text=True, timeout=30,
    )
    return result.stdout.strip()


def copy_notice(source: Path, output: Path, label: str,
                roots: tuple[Path, ...] = NOTICE_ROOTS) -> dict:
    """Follow licence symlinks only within the approved public software roots."""
    resolved = source.resolve(strict=True)
    if not resolved.is_file() or not any(
        resolved.is_relative_to(root.resolve()) for root in roots
    ):
        raise ValueError('Notice is outside the public software directories')
    if '/' in label or '\\' in label or label in ('', '.', '..'):
        raise ValueError('Invalid notice label')
    # Debian multiarch names contain ':'; preserve them in metadata but encode
    # notice filenames for GitHub artifacts and Windows filesystems.
    destination = output / 'notices' / quote(label, safe='._-')
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(resolved, destination)
    with destination.open('rb') as handle:
        digest = hashlib.file_digest(handle, 'sha256').hexdigest()
    return {
        'file': destination.relative_to(output).as_posix(),
        'sha256': digest,
    }


def inventory(output: Path) -> dict:
    output.mkdir(parents=True, exist_ok=False)
    report = {
        'schema': 1,
        'architecture': platform.machine(),
        'distribution_review': 'pending',
        'hardware_acceptance': 'not_tested',
        'debian': [], 'python': [], 'notices': [], 'missing_notices': [],
    }
    # The installed source-package fields include Debian's fallback to the
    # binary package name/version where no separate Source field exists.
    rows = run('dpkg-query', '-W', '-f',
               '${binary:Package}\t${Version}\t${source:Package}\t${source:Version}\n')
    for line in rows.splitlines():
        name, version, source, source_version = line.split('\t')
        report['debian'].append({
            'name': name, 'version': version, 'source': source,
            'source_version': source_version,
        })
        notice = Path('/usr/share/doc') / name.split(':')[0] / 'copyright'
        if notice.exists():
            report['notices'].append(copy_notice(notice, output, f'debian-{name}.txt'))
        else:
            report['missing_notices'].append(f'debian:{name}')
    for dist in sorted(importlib.metadata.distributions(),
                       key=lambda d: d.metadata.get('Name', '').lower()):
        name = dist.metadata['Name']
        record = {'name': name, 'version': dist.version, 'notices': []}
        for index, relative in enumerate(dist.files or ()):
            parts = tuple(part.lower() for part in relative.parts)
            filename = relative.name.lower()
            # Only installed distribution licence metadata, not arbitrary
            # package files or application configuration.
            if any(part.endswith('.dist-info') or part.endswith('.egg-info')
                   for part in parts) and (
                'licenses' in parts or filename.startswith(('license', 'copying', 'notice'))
            ):
                record['notices'].append(copy_notice(
                    Path(dist.locate_file(relative)), output,
                    f'python-{name}-{index}.txt',
                ))
        if not record['notices']:
            report['missing_notices'].append(f'python:{name}')
        report['python'].append(record)
    app_docs = Path('/usr/share/doc/eufy-nvr')
    for index, source in enumerate(sorted(app_docs.rglob('*'))):
        if source.is_file():
            report['notices'].append(copy_notice(source, output, f'project-{index}.txt'))
    import av
    report['video'] = {
        'pyav': av.__version__,
        'pyav_libraries': av.library_versions,
        'ffmpeg': run('ffmpeg', '-version'),
        'go2rtc': run('/opt/eufy/bridge/bin/go2rtc', '-version'),
        'node': run('node', '--version'),
    }
    report['debian'].sort(key=lambda item: item['name'])
    (output / 'inventory.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    report = inventory(args.output)
    print(json.dumps({
        'architecture': report['architecture'],
        'debian_packages': len(report['debian']),
        'python_packages': len(report['python']),
        'missing_notices': len(report['missing_notices']),
        'distribution_review': report['distribution_review'],
    }))


if __name__ == '__main__':
    main()
