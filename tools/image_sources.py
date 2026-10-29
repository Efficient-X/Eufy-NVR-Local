"""Collect public software sources in a disposable CI image, never in HA.

Downloads are data only: no setup.py, build backend or downloaded program runs.
This audit is deliberately not an image-publication or legal-clearance command.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.metadata
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from urllib.parse import quote, urlsplit
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


PYAV_RECIPE_COMMIT = 'b84838a003b1626152ef530750d5c3e0022c3db9'
PYAV_RECIPE_SHA256 = '40bdee1f7cc76667f0b7a3da5198d08c4fe79e6ab571cf1e1b5ee856f58128b3'
MAX_DOWNLOAD = 512 * 1024 * 1024


def sha256(path: Path) -> str:
    with path.open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def checked_url(url: str) -> str:
    # One reviewed upstream recipe uses Debian's HTTP URL. Upgrade transport;
    # the pinned source digest must still match. Never send credentials.
    if url.startswith('http://deb.debian.org/'):
        url = 'https://' + url[len('http://'):]
    parts = urlsplit(url)
    if (parts.scheme != 'https' or not parts.hostname or parts.username
            or parts.password or parts.query or parts.fragment):
        raise ValueError('A credential-free HTTPS source URL is required')
    return url


def _download_once(url: str, destination: Path, expected: str, *, opener=urlopen) -> dict:
    url = checked_url(url)
    if not re.fullmatch('[0-9a-f]{64}', expected):
        raise ValueError('A pinned SHA-256 is required')
    if destination.exists():
        raise ValueError('Source downloads must not overwrite files')
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + '.partial')
    try:
        request = Request(url, headers={'User-Agent': 'EFX-source-audit/1'})
        with opener(request, timeout=120) as response, temporary.open('xb') as handle:
            # A digest authenticates the content, but redirects must also keep
            # TLS. Do not copy the redirect URL (which may be signed) to reports.
            if urlsplit(response.geturl()).scheme != 'https':
                raise ValueError('Source redirect left HTTPS')
            total = 0
            while block := response.read(1024 * 1024):
                total += len(block)
                if total > MAX_DOWNLOAD:
                    raise ValueError('Source download exceeded the size limit')
                handle.write(block)
        if sha256(temporary) != expected:
            raise ValueError('Source SHA-256 does not match upstream metadata')
        temporary.rename(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return {'url': url, 'sha256': expected, 'size': total}


def download(url: str, destination: Path, expected: str, *, opener=urlopen) -> dict:
    # Public mirrors can briefly drop a connection. Retry transport failures,
    # never a content hash mismatch or other validation failure.
    for attempt in range(3):
        try:
            return _download_once(url, destination, expected, opener=opener)
        except HTTPError as error:
            if error.code not in (429, 500, 502, 503, 504) or attempt == 2:
                raise
        except (URLError, TimeoutError):
            if attempt == 2:
                raise
    raise AssertionError('Unreachable download state')


def json_metadata(url: str, *, opener=urlopen) -> dict:
    request = Request(checked_url(url), headers={'User-Agent': 'EFX-source-audit/1'})
    with opener(request, timeout=60) as response:
        if urlsplit(response.geturl()).scheme != 'https':
            raise ValueError('Metadata redirect left HTTPS')
        raw = response.read(2 * 1024 * 1024 + 1)
    if len(raw) > 2 * 1024 * 1024:
        raise ValueError('Source metadata is too large')
    return json.loads(raw)


def pypi_sdist(metadata: dict, name: str, version: str) -> dict:
    normalise = lambda value: re.sub('[-_.]+', '-', value).lower()
    info = metadata['info']
    if normalise(info['name']) != normalise(name) or info['version'] != version:
        raise ValueError('PyPI returned a different package or version')
    candidates = [item for item in metadata['urls'] if item['packagetype'] == 'sdist']
    if len(candidates) != 1:
        raise ValueError('An unambiguous source distribution is required')
    source = candidates[0]
    if urlsplit(checked_url(source['url'])).hostname != 'files.pythonhosted.org':
        raise ValueError('The source must come from official PyPI file hosting')
    filename = source['filename']
    if not re.fullmatch(r'[A-Za-z0-9_.+-]+', filename):
        raise ValueError('Unsafe source filename')
    return {'name': name, 'version': version, 'filename': filename,
            'url': source['url'], 'sha256': source['digests']['sha256']}


def recipe_sources(text: str) -> list[dict]:
    """Read literal package metadata, never import or execute the recipe."""
    sources = []
    for node in ast.walk(ast.parse(text)):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            continue
        if node.func.id != 'Package':
            continue
        values = {item.arg: item.value for item in node.keywords}
        entry = {key: ast.literal_eval(values[key])
                 for key in ('name', 'source_url', 'sha256')}
        if not re.fullmatch('[A-Za-z0-9_.+-]+', entry['name']):
            raise ValueError('Unsafe recipe package name')
        if not re.fullmatch('[0-9a-f]{64}', entry['sha256']):
            raise ValueError('Recipe source has no SHA-256')
        entry['url'] = checked_url(entry.pop('source_url'))
        sources.append(entry)
    if not sources or len({item['name'] for item in sources}) != len(sources):
        raise ValueError('Missing or duplicate recipe packages')
    return sorted(sources, key=lambda item: item['name'])


def installed_packages() -> tuple[list[dict], list[dict]]:
    result = subprocess.run(
        ['dpkg-query', '-W', '-f',
         '${binary:Package}\t${Version}\t${source:Package}\t${source:Version}\n'],
        check=True, capture_output=True, text=True, timeout=30,
    )
    debian = []
    for line in result.stdout.splitlines():
        name, version, source, source_version = line.split('\t')
        debian.append(dict(name=name, version=version, source=source,
                           source_version=source_version))
    python = [dict(name=dist.metadata['Name'], version=dist.version)
              for dist in importlib.metadata.distributions()]
    return debian, python


def verify_installed(inventory: dict) -> None:
    debian, python = installed_packages()
    for key, actual, fields in (
        ('debian', debian, ('name', 'version', 'source', 'source_version')),
        ('python', python, ('name', 'version')),
    ):
        project = lambda rows: sorted(tuple(row[field] for field in fields) for row in rows)
        if project(actual) != project(inventory[key]):
            raise ValueError('The installed packages differ from the reviewed inventory')


def debian_sources(inventory: dict, output: Path) -> list[dict]:
    """APT checks source downloads against signed official Debian indexes."""
    sources = sorted({(item['source'], item['source_version'])
                      for item in inventory['debian']})
    with tempfile.TemporaryDirectory(prefix='efx-deb-src-') as temporary:
        config = Path(temporary) / 'debian.sources'
        config.write_text(
            'Types: deb-src\nURIs: https://deb.debian.org/debian\n'
            'Suites: bookworm bookworm-updates\nComponents: main\n'
            'Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg\n\n'
            'Types: deb-src\nURIs: https://security.debian.org/debian-security\n'
            'Suites: bookworm-security\nComponents: main\n'
            'Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg\n',
            encoding='utf-8',
        )
        options = ['-o', 'Dir::Etc::sourcelist=/dev/null',
                   '-o', f'Dir::Etc::sourceparts={temporary}',
                   '-o', 'APT::Get::AllowUnauthenticated=false',
                   '-o', 'Acquire::Retries=3']
        subprocess.run(['apt-get', *options, 'update'], check=True, timeout=300)
        records = []
        for name, version in sources:
            if (not re.fullmatch('[a-z0-9][a-z0-9+.-]*', name)
                    or not re.fullmatch('[A-Za-z0-9.+:~_-]+', version)):
                raise ValueError('Invalid Debian source identity')
            folder = output / quote(name + '_' + version, safe='._-')
            folder.mkdir(parents=True, exist_ok=False)
            # No shell, package installation, extraction or source execution.
            result = subprocess.run(
                ['apt-get', *options, 'source', '--download-only', '--only-source',
                 name + '=' + version], cwd=folder, capture_output=True,
                text=True, timeout=300,
            )
            if result.returncode:
                # Retain the exact missing identity without arbitrary logs.
                records.append(dict(name=name, version=version, status='unavailable'))
                print('Unavailable Debian source:', name, version, flush=True)
                continue
            files = []
            for path in sorted(folder.iterdir()):
                if path.is_symlink() or not path.is_file():
                    raise ValueError('Unexpected Debian source output')
                files.append(dict(file=path.relative_to(output).as_posix(),
                                  sha256=sha256(path), size=path.stat().st_size))
            if not files or not any(item['file'].endswith('.dsc') for item in files):
                raise ValueError('Downloaded Debian source has no control file')
            records.append(dict(name=name, version=version, status='downloaded', files=files))
            print('Collected Debian source:', name, version, flush=True)
    return records


def collect(inventory: dict, output: Path, source_revision: str) -> dict:
    if not re.fullmatch('[0-9a-f]{40}', source_revision):
        raise ValueError('An exact source commit is required')
    verify_installed(inventory)
    output.mkdir(parents=True, exist_ok=False)
    report = dict(schema=1, source_revision=source_revision,
                  distribution_review='pending', hardware_acceptance='not_tested',
                  debian=[], python=[], pyav_recipe=[], failures=[])
    # Write progress even when the network or one archive is unavailable.
    def save():
        (output / 'source-report.json').write_text(json.dumps(report, indent=2) + '\n')
    save()
    try:
        report['debian'] = debian_sources(inventory, output / 'debian')
        for item in sorted(inventory['python'], key=lambda p: p['name'].lower()):
            name, version = item['name'], item['version']
            metadata = json_metadata('https://pypi.org/pypi/' + quote(name, safe='')
                                     + '/' + quote(version, safe='') + '/json')
            source = pypi_sdist(metadata, name, version)
            path = output / 'python' / source['filename']
            download(source['url'], path, source['sha256'])
            source['file'] = path.relative_to(output).as_posix()
            report['python'].append(source)
            save()
        recipe = output / 'recipes' / 'pyav-ffmpeg-pkg.py'
        download('https://raw.githubusercontent.com/PyAV-Org/pyav-ffmpeg/'
                 + PYAV_RECIPE_COMMIT + '/scripts/pkg.py', recipe, PYAV_RECIPE_SHA256)
        if not any(item['name'].lower() == 'av' and item['version'] == '17.1.0'
                   for item in inventory['python']):
            raise ValueError('Review the FFmpeg recipe before changing PyAV')
        # Collect all recipe source inputs, including optional platforms, rather
        # than guessing which libraries the upstream wheels bundled.
        for source in recipe_sources(recipe.read_text()):
            filename = urlsplit(source['url']).path.rsplit('/', 1)[-1]
            if not re.fullmatch('[A-Za-z0-9_.+-]+', filename):
                raise ValueError('Unsafe recipe filename')
            path = output / 'pyav-ffmpeg' / (source['name'] + '-' + filename)
            download(source['url'], path, source['sha256'])
            source['file'] = path.relative_to(output).as_posix()
            report['pyav_recipe'].append(source)
            save()
        common = Path('/usr/share/common-licenses')
        for path in sorted(common.iterdir()):
            resolved = path.resolve(strict=True)
            if resolved.is_file() and resolved.is_relative_to(common.resolve()):
                target = output / 'common-licenses' / path.name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(resolved, target)
    except Exception as error:
        # Exception messages can contain signed redirect URLs; retain the type
        # only, and fail the audit. No secret/environment/log capture.
        report['failures'].append(type(error).__name__)
        raise
    finally:
        report['unavailable_debian'] = [dict(name=p['name'], version=p['version'])
                                      for p in report['debian'] if p['status'] != 'downloaded']
        report['remaining_review'] = [
            'Go binary dependency sources/notices and exact toolchain metadata',
            'HA base overlay components and their build recipes',
            'Other native libraries bundled inside Python wheels and Rust crates',
            'Permanent release assets accompanying any distributed image',
        ]
        save()
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inventory', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--source-revision', required=True)
    args = parser.parse_args()
    # This tool updates only a disposable container's APT indexes. It must not
    # be used as an HA app, Supervisor terminal or general filesystem scanner.
    if (not Path('/.dockerenv').is_file() or Path('/data').exists()
            or Path('/config').exists()
            or not Path('/opt/eufy/bridge/guided_launcher.py').is_file()):
        parser.error('Run only in the disposable CI NVR image, without HA data mounts')
    if args.output.is_symlink() or not args.output.resolve().is_relative_to(Path('/out')):
        parser.error('Source output must be a new directory under /out')
    report = collect(json.loads(args.inventory.read_text()), args.output, args.source_revision)
    print(json.dumps(dict(debian_sources=len(report['debian']),
                         python_sources=len(report['python']),
                         pyav_recipe_sources=len(report['pyav_recipe']),
                         unavailable_debian=len(report['unavailable_debian']),
                         distribution_review=report['distribution_review'])))
    if report['unavailable_debian']:
        raise SystemExit('Some exact Debian sources are unavailable; image publication is not enabled')


if __name__ == '__main__':
    main()
