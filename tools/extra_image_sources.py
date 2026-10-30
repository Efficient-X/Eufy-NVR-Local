"""Collect upstream Go/base sources in CI; never run the NVR or use HA data."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
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
    's6-overlay': ('just-containers/s6-overlay', 'f1fecfd867a13723588aa3ed6fe2ce480860dda3'),
    'pyav-ffmpeg': ('PyAV-Org/pyav-ffmpeg', 'b84838a003b1626152ef530750d5c3e0022c3db9'),
    'pylibsrtp': ('aiortc/pylibsrtp', '2ed38a30f6901315d247bd5d98c6779867a9475a'),
    'libsrtp': ('cisco/libsrtp', 'ee1a77c9f9dc02c42bda9901038c500c5efe4cfa'),
    'libcrc32c': ('google/crc32c', '02e65f4fd3065d27b2e29324800ca6d04df16126'),
    'aioquic-openssl': ('aiortc/aioquic-openssl', '107757823165a1a25511e84b44a15d659c271f4b'),
    'openssl-3.5.4': ('openssl/openssl', 'c1eeb9406b6142148f267594197d853403d10208'),
    's6-overlay-helpers': ('just-containers/s6-overlay-helpers', '8cd03adbfc0ada2d7562be1dbb46e1136050fe8f'),
    'bearssl': ('https://www.bearssl.org/git/BearSSL', '7bea48e5e850ab4cafbe68d3765cdaba13a86d6f'),
}


def command(*args, cwd=None, env=None, timeout=120):
    result = subprocess.run(args, cwd=cwd, env=env, capture_output=True,
                            text=True, timeout=timeout)
    if result.returncode:
        detail = re.sub(r'https?://\S+', '[upstream URL]', result.stderr).strip()[:1500]
        raise RuntimeError(f'{args[0]} failed: {detail}')
    return result.stdout.strip()


def inspect_go_binary(binary: Path, workspace: Path) -> str:
    # Official go2rtc release binaries are UPX-packed. Unpack a temporary copy
    # solely for Go's data reader; never execute or change the shipped program.
    with binary.open('rb') as handle:
        packed = b'UPX!' in handle.read(1024)
    inspect_path = binary
    if packed:
        assets = {
            'x86_64': ('amd64', 'ddc2654063fe4dc80d95b420788494e4db078ebb01a650692d623b5a9906e61e'),
            'aarch64': ('arm64', '100310f74eb6f67694d1d0377f1c729b6a49238ce8c4de21ea2e7d3406186f8b'),
        }
        architecture, digest = assets[platform.machine()]
        stem = f'upx-5.0.2-{architecture}_linux'
        archive = workspace / (stem + '.tar.xz')
        executable = workspace / 'upx'
        if not executable.exists():
            download(f'https://github.com/upx/upx/releases/download/v5.0.2/{stem}.tar.xz',
                     archive, digest)
            with tarfile.open(archive) as source:
                member = source.getmember(stem + '/upx')
                if not member.isfile() or member.size > 16 * 1024 * 1024:
                    raise ValueError('Unexpected official UPX executable')
                executable.write_bytes(source.extractfile(member).read())
            executable.chmod(0o700)
        inspect_path = workspace / (binary.name + '.unpacked')
        command(str(executable), '-d', '-o', str(inspect_path), str(binary))
    return command('go', 'version', '-m', str(inspect_path))


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
    root = root.resolve(strict=True)
    paths = [p for p in sorted(root.rglob('*')) if '.git' not in p.relative_to(root).parts]
    for path in paths:
        if path.is_symlink() and (Path(os.readlink(path)).is_absolute()
                                  or not path.resolve().is_relative_to(root)):
            raise ValueError('Upstream source link escapes its repository')
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise ValueError('Never overwrite source evidence')
    with tarfile.open(destination, 'w:gz') as archive:
        for path in paths:
            if path.is_file() or path.is_symlink():
                info = archive.gettarinfo(str(path), arcname=name + '/' + path.relative_to(root).as_posix())
                info.uid = info.gid = info.mtime = 0
                info.uname = info.gname = ''
                if info.issym():
                    archive.addfile(info)
                else:
                    with path.open('rb') as handle:
                        archive.addfile(info, handle)
    return dict(file=destination.name, size=destination.stat().st_size, sha256=sha256(destination))


def collect_repository(name: str, workspace: Path, output: Path, *, binary: Path | None = None) -> dict:
    repository, revision = REPOSITORIES[name]
    root = workspace / name
    url = repository if repository.startswith('https://') else 'https://github.com/' + repository + '.git'
    command('git', '-c', 'core.autocrlf=false', 'clone', '--quiet', '--no-checkout',
            '--filter=blob:none', url, str(root))
    command('git', 'checkout', '--quiet', '--detach', revision, cwd=root)
    if command('git', 'rev-parse', 'HEAD', cwd=root) != revision:
        raise ValueError('Upstream Git revision differs')
    record = dict(repository=repository, revision=revision)
    if binary is not None:
        info = inspect_go_binary(binary, workspace)
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
    record['notices'] = repository_notices(root, output, name)
    print('Collected upstream source:', name, revision, flush=True)
    return record


def repository_notices(root: Path, output: Path, name: str) -> list[dict]:
    records = []
    root = root.resolve(strict=True)
    for path in sorted(root.rglob('*')):
        relative = path.relative_to(root)
        filename = path.name.lower()
        if '.git' in relative.parts or not filename.startswith(('license', 'licence', 'copying', 'copyright', 'notice')):
            continue
        if path.suffix.lower() in ('.py', '.pyc', '.c', '.h', '.rs', '.go', '.js'):
            continue
        resolved = path.resolve(strict=True)
        if not resolved.is_relative_to(root) or not resolved.is_file():
            continue
        raw = resolved.read_bytes()
        if len(raw) > 4 * 1024 * 1024 or b'\0' in raw:
            raise ValueError('Unexpected public source notice')
        identity = hashlib.sha256(relative.as_posix().encode()).hexdigest()[:24]
        destination = output / 'notices' / f'{name}-{identity}.txt'
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(raw)
        records.append(dict(file=destination.relative_to(output).as_posix(),
                            source_path=relative.as_posix(), sha256=sha256(destination)))
    return records


def collect_overlay_sources(source_root: Path, output: Path) -> list[dict]:
    # Parse pinned recipe declarations as text, never source/execute a shell.
    versions = dict(re.findall(r'^([A-Z0-9_]+)_VERSION=(\S+)$',
                              (source_root / 'conf/versions').read_text(), re.MULTILINE))
    pins = json.loads(Path(__file__).with_name('overlay_source_inputs.json').read_text())
    records = []
    for item in pins:
        if item['key'] is not None and versions.get(item['key']) != item['version']:
            raise ValueError('Overlay source input differs from the pinned build recipe')
        path = output / item['filename']
        download(item['url'], path, item['sha256'])
        with tarfile.open(path) as archive:
            member = archive.getmember(item['notice'])
            if not member.isfile() or member.size > 4 * 1024 * 1024:
                raise ValueError('Overlay notice is not a regular bounded source file')
            raw = archive.extractfile(member).read()
        if b'\0' in raw:
            raise ValueError('Overlay notice contains binary data')
        notice = output / 'notices' / (item['name'] + '-COPYING.txt')
        notice.parent.mkdir(parents=True, exist_ok=True)
        notice.write_bytes(raw)
        records.append(dict(item, file=path.name,
                            notice_file=notice.relative_to(output).as_posix(),
                            notice_sha256=sha256(notice)))
    return records


def go_source_metadata(catalogue: list, version: str) -> dict:
    if not re.fullmatch(r'go[0-9]+\.[0-9]+\.[0-9]+', version):
        raise ValueError('Invalid Go toolchain source version')
    matches = [item for release in catalogue if release['version'] == version
               for item in release['files'] if item['kind'] == 'source'
               and item['filename'] == version + '.src.tar.gz']
    if len(matches) != 1 or not re.fullmatch('[0-9a-f]{64}', matches[0]['sha256']):
        raise ValueError('The official Go catalogue has no unique source checksum')
    return matches[0]


def collect_toolchain(version: str, output: Path) -> dict:
    # Go serves archive checksums in its official JSON catalogue, not .sha256
    # sidecar files. The query is fixed public metadata, never a credential.
    with urlopen('https://go.dev/dl/?mode=json&include=all', timeout=60) as response:
        if urlsplit(response.geturl()).scheme != 'https':
            raise ValueError('Go catalogue redirect left HTTPS')
        raw = response.read(16 * 1024 * 1024 + 1)
    if len(raw) > 16 * 1024 * 1024:
        raise ValueError('Go catalogue exceeded its size limit')
    metadata = go_source_metadata(json.loads(raw), version)
    url = 'https://go.dev/dl/' + version + '.src.tar.gz'
    destination = output / (version + '.src.tar.gz')
    result = download(url, destination, metadata['sha256'])
    result['file'] = destination.name
    return result


def collect(output: Path) -> dict:
    output.mkdir(parents=True, exist_ok=False)
    version = command('docker', 'image', 'inspect', 'eufy-source-audit', '--format',
                      '{{index .Config.Labels "io.hass.base.version"}}')
    if version != '2026.08.0':
        raise ValueError('Review base-component pins before changing the HA base image')
    report = dict(schema=1, base_version=version, repositories={}, go_toolchains=[],
                  base_notices=[], overlay_inputs=[], distribution_review='pending')
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
        for name in ('ha-base', 'bashio', 's6-overlay', 'pyav-ffmpeg', 'pylibsrtp',
                     'libsrtp', 'libcrc32c', 'aioquic-openssl', 'openssl-3.5.4',
                     's6-overlay-helpers', 'bearssl'):
            report['repositories'][name] = collect_repository(name, workspace, output)
        report['overlay_inputs'] = collect_overlay_sources(workspace / 's6-overlay', output / 'overlay-inputs')
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
                          overlay_notices=len(report['overlay_inputs']),
                          distribution_review=report['distribution_review'])))


if __name__ == '__main__':
    main()
