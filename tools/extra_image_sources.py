"""Collect upstream Go/base sources in CI; never run the NVR or use HA data."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import tempfile
from urllib.parse import quote, urlsplit
from urllib.request import urlopen

from image_sources import download, sha256


REPOSITORIES = {
    'go2rtc': ('AlexxIT/go2rtc', 'b5948cfb25404cc5cb37b166ecaa2dca20b11d4b'),
    'tempio': ('home-assistant/tempio', '56918dfb7db5fec2161b2c5b37ee42f49176d34b'),
    'ha-base': ('home-assistant/docker-base', '6a3ff4c10f6ed8564a092c33051024a1b1042ee2'),
    'bashio': ('hassio-addons/bashio', 'f788a0b8e944c5fed623225fe37af8dda97ac2f2'),
    's6-overlay': ('just-containers/s6-overlay', '29fc4e475004f67c6e7ff479c2253a2ed6f79b2b'),
    'pyav-ffmpeg': ('PyAV-Org/pyav-ffmpeg', 'b84838a003b1626152ef530750d5c3e0022c3db9'),
}


def command(*args, cwd=None, env=None, timeout=120):
    return subprocess.run(args, cwd=cwd, env=env, check=True, capture_output=True,
                          text=True, timeout=timeout).stdout.strip()


def go_build_info(text: str, expected_revision: str) -> str:
    lines = text.splitlines()
    match = re.search(r'\b(go[0-9]+\.[0-9]+\.[0-9]+)$', lines[0] if lines else '')
    if not match:
        raise ValueError('The binary does not identify a stable Go toolchain')
    revisions = [line.split('vcs.revision=', 1)[1].strip() for line in lines
                 if 'vcs.revision=' in line]
    if revisions != [expected_revision] or any('vcs.modified=true' in line for line in lines):
        raise ValueError('Binary source revision does not match the pinned clean repository')
    return match.group(1)


def source_archive(root: Path, destination: Path, name: str) -> dict:
    paths = [p for p in sorted(root.rglob('*')) if '.git' not in p.relative_to(root).parts]
    if any(p.is_symlink() for p in paths):
        raise ValueError('Upstream source contains a symlink requiring review')
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise ValueError('Never overwrite source evidence')
    with tarfile.open(destination, 'w:gz') as archive:
        for path in paths:
            if path.is_file():
                info = archive.gettarinfo(str(path), arcname=name + '/' + path.relative_to(root).as_posix())
                info.uid = info.gid = info.mtime = 0
                info.uname = info.gname = ''
                with path.open('rb') as handle:
                    archive.addfile(info, handle)
    return dict(file=destination.name, size=destination.stat().st_size, sha256=sha256(destination))


def collect_repository(name: str, workspace: Path, output: Path, *, binary: Path | None = None) -> dict:
    repository, revision = REPOSITORIES[name]
    root = workspace / name
    command('git', '-c', 'core.autocrlf=false', 'clone', '--quiet', '--no-checkout',
            '--filter=blob:none', 'https://github.com/' + repository + '.git', str(root))
    command('git', 'checkout', '--quiet', '--detach', revision, cwd=root)
    if command('git', 'rev-parse', 'HEAD', cwd=root) != revision:
        raise ValueError('Upstream Git revision differs')
    record = dict(repository=repository, revision=revision)
    if binary is not None:
        info = command('go', 'version', '-m', str(binary))
        record['go_toolchain'] = go_build_info(info, revision)
        record['binary_sha256'] = sha256(binary)
        record['binary_buildinfo'] = info.splitlines()[1:]
        # Go's module downloader validates go.sum/checksum-server identities.
        # Vendoring downloads source; it does not execute dependency code.
        environment = dict(os.environ, GOPROXY='https://proxy.golang.org',
                           GOSUMDB='sum.golang.org', GOPRIVATE='', GONOSUMDB='',
                           GOTOOLCHAIN='auto', GOMODCACHE=str(workspace / 'gomodcache'),
                           GOCACHE=str(workspace / 'gocache'))
        command('go', 'mod', 'vendor', cwd=root, env=environment, timeout=600)
        command('git', 'diff', '--exit-code', '--', 'go.mod', 'go.sum', cwd=root)
        record['vendored_modules'] = (root / 'vendor/modules.txt').read_text()
    record.update(source_archive(root, output / (name + '-source.tar.gz'), name))
    print('Collected upstream source:', name, revision, flush=True)
    return record


def collect_toolchain(version: str, output: Path) -> dict:
    if not re.fullmatch(r'go[0-9]+\.[0-9]+\.[0-9]+', version):
        raise ValueError('Invalid Go toolchain source version')
    url = 'https://go.dev/dl/' + version + '.src.tar.gz'
    with urlopen(url + '.sha256', timeout=60) as response:
        if urlsplit(response.geturl()).scheme != 'https':
            raise ValueError('Go checksum redirect left HTTPS')
        digest = response.read(128).decode().strip()
    destination = output / (version + '.src.tar.gz')
    result = download(url, destination, digest)
    result['file'] = destination.name
    return result


def collect(output: Path) -> dict:
    output.mkdir(parents=True, exist_ok=False)
    version = command('docker', 'image', 'inspect', 'eufy-source-audit', '--format',
                      '{{index .Config.Labels "io.hass.base.version"}}')
    if version != '2026.08.0':
        raise ValueError('Review base-component pins before changing the HA base image')
    report = dict(schema=1, base_version=version, repositories={}, go_toolchains=[],
                  base_notices=[], distribution_review='pending')
    with tempfile.TemporaryDirectory(prefix='efx-upstream-sources-') as temporary:
        workspace = Path(temporary)
        container = command('docker', 'create', '--network', 'none', '--entrypoint',
                            '/bin/true', 'eufy-source-audit')
        if not re.fullmatch('[0-9a-f]{64}', container):
            raise ValueError('Docker did not return an isolated stopped container')
        try:
            for name, path in (('go2rtc', '/opt/eufy/bridge/bin/go2rtc'), ('tempio', '/usr/bin/tempio')):
                binary = workspace / (name + '.binary')
                command('docker', 'cp', container + ':' + path, str(binary))
                report['repositories'][name] = collect_repository(name, workspace, output, binary=binary)
            # Public OS installation notices only. No container is started and
            # no /data, /config, logs, account state or Docker socket is copied.
            installed = workspace / 'package'
            command('docker', 'cp', container + ':/package', str(installed))
            for path in sorted(installed.rglob('*')):
                if path.name.lower() not in ('copying', 'license', 'copyright', 'license.txt', 'copyright.txt'):
                    continue
                if path.is_symlink() or not path.is_file():
                    continue
                raw = path.read_bytes()
                if len(raw) > 4 * 1024 * 1024 or b'\0' in raw:
                    raise ValueError('Unexpected installed base notice')
                label = quote(path.relative_to(installed).as_posix(), safe='._-')
                target = output / 'base-notices' / label
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(raw)
                report['base_notices'].append(dict(file=target.relative_to(output).as_posix(),
                                                  sha256=hashlib.sha256(raw).hexdigest()))
        finally:
            command('docker', 'rm', container)
        for name in ('ha-base', 'bashio', 's6-overlay', 'pyav-ffmpeg'):
            report['repositories'][name] = collect_repository(name, workspace, output)
        for toolchain in sorted({r['go_toolchain'] for r in report['repositories'].values()
                                 if 'go_toolchain' in r}):
            report['go_toolchains'].append(collect_toolchain(toolchain, output))
    (output / 'upstream-report.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if os.environ.get('GITHUB_ACTIONS') != 'true':
        parser.error('This collector is for disposable GitHub CI runners only')
    report = collect(args.output)
    print(json.dumps(dict(repositories=len(report['repositories']),
                          go_toolchains=len(report['go_toolchains']),
                          base_notices=len(report['base_notices']),
                          distribution_review=report['distribution_review'])))


if __name__ == '__main__':
    main()
