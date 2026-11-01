"""Publish reviewed CI image bytes and matching sources, never household data."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tarfile
import tempfile
import zipfile

REPOSITORY = 'Efficient-X/Eufy-NVR-Local'
IMAGE = 'ghcr.io/efficient-x/eufy-nvr-local'
ARCHITECTURES = {'amd64': 'amd64', 'aarch64': 'arm64'}


def run(*args, environment=None, data=None):
    result = subprocess.run(args, input=data, capture_output=True, text=True,
                            env=environment, timeout=1800)
    if result.returncode:
        # Do not include registry credentials or raw authenticated API output.
        detail = ''
        if args[:2] == ('docker', 'push'):
            detail = (result.stderr + '\n' + result.stdout)[-2000:]
            token = os.environ.get('GH_TOKEN')
            if token:
                detail = detail.replace(token, '[redacted]')
            detail = re.sub(r'https?://\S+|\b(?:gh[pousr]_|github_pat_|eyJ)[A-Za-z0-9_.-]+',
                            '[redacted]', detail)
        raise RuntimeError(f'{args[0]} {args[1]} failed (exit {result.returncode}) {detail}')
    return result.stdout.strip()


def api(path):
    return json.loads(run('gh', 'api', f'repos/{REPOSITORY}/{path}'))


def optional_api(path):
    result = subprocess.run(['gh', 'api', f'repos/{REPOSITORY}/{path}'],
                            capture_output=True, text=True, timeout=120)
    if result.returncode == 0:
        return json.loads(result.stdout)
    if '(HTTP 404)' in result.stderr:
        return None
    raise RuntimeError('GitHub lookup failed; absence was not established')


def sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def release_inputs(version, revision):
    if not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+-(alpha|beta)\.[0-9]+', version):
        raise ValueError('Only explicitly versioned experimental releases are permitted')
    if not re.fullmatch('[0-9a-f]{40}', revision):
        raise ValueError('An exact source commit is required')


def audit_revision(run_id):
    if not re.fullmatch('[1-9][0-9]*', run_id):
        raise ValueError('A numeric completed audit run is required')
    audit = api('actions/runs/' + run_id)
    if (audit['event'] != 'workflow_dispatch' or audit['status'] != 'completed'
            or audit['conclusion'] != 'success'
            or audit['path'] != '.github/workflows/source-audit.yml'
            or audit['head_repository']['full_name'] != REPOSITORY):
        raise ValueError('Use a successful manual source audit from this repository')
    revision = audit['head_sha']
    tests = api('actions/runs?head_sha=' + revision + '&event=push&per_page=100')
    ci = [r for r in tests['workflow_runs'] if r['path'] == '.github/workflows/ci.yml']
    if not ci or max(ci, key=lambda r: r['id'])['conclusion'] != 'success':
        raise ValueError('The exact source commit must also pass the complete CI workflow')
    return revision


def record_image(architecture, revision, version, archive, output):
    release_inputs(version, revision)
    if architecture not in ARCHITECTURES:
        raise ValueError('Unsupported architecture')
    metadata = json.loads(run('docker', 'image', 'inspect', 'eufy-source-audit'))[0]
    validate_image(metadata, architecture, revision, version)
    receipt = dict(architecture=architecture, source_revision=revision, version=version,
                   image_id=metadata['Id'], file=archive.name,
                   size=archive.stat().st_size, sha256=sha256(archive))
    output.write_text(json.dumps(receipt, indent=2) + '\n')


def validate_image(metadata, architecture, revision, version):
    labels = metadata['Config']['Labels']
    if (metadata['Os'] != 'linux' or metadata['Architecture'] != ARCHITECTURES[architecture]
            or labels.get('org.opencontainers.image.revision') != revision
            or labels.get('org.opencontainers.image.version') != version
            or labels.get('io.hass.arch') != architecture
            or labels.get('io.hass.version') != version):
        raise ValueError('Image architecture, source or Home Assistant labels differ')


def checked_file(root, name, size, digest):
    if Path(name).name != name or not re.fullmatch('[0-9a-f]{64}', digest):
        raise ValueError('Invalid release asset identity')
    path = root / name
    if path.is_symlink() or path.stat().st_size != size or sha256(path) != digest:
        raise ValueError('Release asset bytes differ from their audit receipt')
    return path


def source_parts(root, manifest, revision):
    if manifest['source_revision'] != revision or manifest['format'] != 'concatenated-tar':
        raise ValueError('Source evidence does not match the image commit')
    whole, size, paths = hashlib.sha256(), 0, []
    for item in manifest['parts']:
        path = checked_file(root, item['file'], item['size'], item['sha256'])
        with path.open('rb') as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b''):
                whole.update(block)
                size += len(block)
        paths.append(path)
    if not paths or size != manifest['size'] or whole.hexdigest() != manifest['sha256']:
        raise ValueError('Joined source archive identity differs')
    return paths


def login():
    token = os.environ['GH_TOKEN']
    run('docker', 'login', 'ghcr.io', '--username', os.environ['GITHUB_ACTOR'],
        '--password-stdin', data=token + '\n')


def require_absent(reference):
    result = subprocess.run(['docker', 'manifest', 'inspect', reference],
                            capture_output=True, text=True, timeout=120)
    if result.returncode == 0:
        raise ValueError('Refusing to overwrite an existing image version')
    if not re.search(r'not found|manifest unknown|MANIFEST_UNKNOWN|NAME_UNKNOWN',
                     result.stderr, re.IGNORECASE):
        raise RuntimeError('Registry lookup failed; absence was not established')


def image_manifest(reference):
    result = subprocess.run(['docker', 'buildx', 'imagetools', 'inspect', reference,
                             '--raw'], capture_output=True, timeout=120)
    if result.returncode == 0:
        manifest = json.loads(result.stdout)
        manifest['digest'] = 'sha256:' + hashlib.sha256(result.stdout).hexdigest()
        return manifest
    if re.search(r'not found|manifest unknown|MANIFEST_UNKNOWN|NAME_UNKNOWN',
                 result.stderr.decode('utf-8', errors='replace'), re.IGNORECASE):
        return None
    raise RuntimeError('Registry lookup failed; absence was not established')


def archive_config_digest(archive, architecture, revision, version):
    # Docker saves both classic images and OCI indices with this compatibility
    # manifest. Read bounded metadata only; never extract or execute an image.
    with tarfile.open(archive, 'r|*') as source:
        manifest = None
        for member in source:
            if member.name == 'manifest.json':
                if not member.isfile() or member.size > 1024 * 1024:
                    raise ValueError('Unexpected Docker archive manifest')
                manifest = json.loads(source.extractfile(member).read())
                break
    if not isinstance(manifest, list) or len(manifest) != 1:
        raise ValueError('Exactly one saved runtime platform is required')
    name = manifest[0]['Config']
    match = re.fullmatch(r'(?:blobs/sha256/([0-9a-f]{64})|([0-9a-f]{64})\.json)', name)
    if not match:
        raise ValueError('Unexpected archived image config identity')
    digest = match.group(1) or match.group(2)
    with tarfile.open(archive, 'r|*') as source:
        for member in source:
            if member.name != name:
                continue
            if not member.isfile() or member.size > 2 * 1024 * 1024:
                raise ValueError('Unexpected archived image config')
            raw = source.extractfile(member).read()
            if hashlib.sha256(raw).hexdigest() != digest:
                raise ValueError('Archived image config bytes differ')
            config = json.loads(raw)
            validate_image(dict(Os=config['os'], Architecture=config['architecture'],
                                Config=config['config']), architecture, revision, version)
            return 'sha256:' + digest
    raise ValueError('Archived image config is missing')


def zip_contents(path):
    with zipfile.ZipFile(path) as archive:
        if len(archive.namelist()) != len(set(archive.namelist())):
            raise ValueError('Duplicate notice archive members')
        return {i.filename: hashlib.sha256(archive.read(i)).hexdigest()
                for i in archive.infolist() if not i.is_dir()}


def retain_assets(tag, assets, workspace):
    release = api('releases/tags/' + tag)
    existing = {a['name']: a for a in release['assets']}
    for path in assets:
        item = existing.get(path.name)
        if item is None:
            run('gh', 'release', 'upload', tag, str(path), '--repo', REPOSITORY)
        elif item.get('digest') == 'sha256:' + sha256(path) and item['size'] == path.stat().st_size:
            continue
        elif path.name.endswith('.zip'):
            # Original ZIP timestamps may differ on a retry. Verify all original
            # member bytes, retain the published ZIP, and never overwrite it.
            previous = workspace / ('existing-' + path.name)
            previous.mkdir()
            run('gh', 'release', 'download', tag, '--repo', REPOSITORY,
                '--pattern', path.name, '--dir', str(previous))
            if zip_contents(previous / path.name) != zip_contents(path):
                raise ValueError('Published notice contents differ; refusing replacement')
        else:
            raise ValueError('Published source asset differs; refusing replacement')


def require_tag(tag, revision):
    ref = api('git/ref/tags/' + tag)
    identity = ref.get('object', {})
    # This publisher creates lightweight tags only. Never move an existing tag
    # or infer identity from a release's target_commitish or a branch label.
    if (ref.get('ref') != 'refs/tags/' + tag
            or identity.get('type') != 'commit' or identity.get('sha') != revision):
        raise ValueError('Tag is not this exact experimental source commit')


def require_release(tag, revision):
    require_tag(tag, revision)
    existing = api('releases/tags/' + tag)
    if (existing.get('tag_name') != tag or existing.get('draft') is not False
            or existing.get('prerelease') is not True):
        raise ValueError('Release is not this exact public experimental tag')


def prepare_tag(version, revision):
    release_inputs(version, revision)
    # Check both package versions at the audited commit without downloading or
    # running an image. The tag must not claim another package version.
    def source(path):
        document = api('contents/' + path + '?ref=' + revision)
        if document.get('encoding') != 'base64':
            raise ValueError('Audited package version could not be verified')
        return base64.b64decode(document['content']).decode('utf-8')
    component = json.loads(source('custom_components/eufy_nvr/manifest.json'))
    app = source('eufy_nvr/config.yaml')
    if (component.get('version') != version
            or re.findall(r'^version: ([^\r\n]+)$', app, re.MULTILINE) != [version]):
        raise ValueError('Audited package versions differ from the requested tag')
    tag = 'v' + version
    if optional_api('git/ref/tags/' + tag) is None:
        run('gh', 'api', '--method', 'POST', f'repos/{REPOSITORY}/git/refs',
            '--input', '-', data=json.dumps(dict(ref='refs/tags/' + tag, sha=revision)))
    # Read authoritative state even after successful POST; never trust only its
    # response, retry creation after uncertainty, or force/update a public ref.
    require_tag(tag, revision)
    print('Verified tag ' + tag + ' at ' + revision + '. No release or images published.')


def ensure_sources(tag, revision, assets, workspace):
    require_tag(tag, revision)
    existing = optional_api('releases/tags/' + tag)
    if existing is None:
        run('gh', 'release', 'create', tag, '--repo', REPOSITORY, '--verify-tag',
            '--prerelease', '--title', 'Eufy NVR Local ' + tag[1:],
            '--notes', 'Experimental prebuilt image publication is in progress. The app-store default is unchanged. Real-camera acceptance is pending. Matching sources and original notices are retained with this release.',
            *map(str, assets))
    require_release(tag, revision)
    retain_assets(tag, assets, workspace)
    require_release(tag, revision)


def loaded_image_identity(config_digest, arch, revision, version, workspace):
    # Docker's classic and containerd stores assign different image IDs. The
    # archive's fixed local tag locates it; a fresh export proves that import
    # retained the exact runtime config (including layer diff IDs and labels).
    reference = 'eufy-source-audit:latest'
    platform = 'linux/' + ARCHITECTURES[arch]
    metadata = json.loads(run('docker', 'image', 'inspect', '--platform', platform, reference))[0]
    validate_image(metadata, arch, revision, version)
    identity = metadata.get('Id', '')
    if not re.fullmatch(r'sha256:[0-9a-f]{64}', identity):
        raise ValueError('Loaded image identity is invalid')
    exported = workspace / ('loaded-runtime-' + arch + '.tar')
    run('docker', 'image', 'save', '--platform', platform, '--output', str(exported), reference)
    if archive_config_digest(exported, arch, revision, version) != config_digest:
        raise ValueError('Loaded runtime config differs from the audited archive')
    return identity


def stage(run_id, version, revision, workspace):
    run('gh', 'run', 'download', run_id, '--repo', REPOSITORY, '--dir', str(workspace))
    assets, images = [], []
    for arch in ARCHITECTURES:
        summary = workspace / ('source-summary-' + arch)
        source_manifest = summary / 'source-parts' / f'eufy-nvr-sources-{arch}.json'
        document = json.loads(source_manifest.read_text())
        if document['architecture'] != arch:
            raise ValueError('Source architecture differs')
        # Artifact directories separate parts; copy only the declared public files.
        parts = workspace / ('joined-' + arch)
        parts.mkdir()
        for index, item in enumerate(document['parts']):
            original = checked_file(workspace / f'sources-{arch}-part{index:02}',
                                    item['file'], item['size'], item['sha256'])
            (parts / original.name).hardlink_to(original)
        assets.extend(source_parts(parts, document, revision))
        assets.append(source_manifest)
        notice_zip = workspace / f'eufy-nvr-notices-{arch}.zip'
        with zipfile.ZipFile(notice_zip, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(summary.rglob('*')):
                if path.is_symlink():
                    raise ValueError('Unexpected link in public audit evidence')
                if path.is_file():
                    archive.write(path, path.relative_to(summary).as_posix())
        assets.append(notice_zip)
        image_root = workspace / ('candidate-image-' + arch)
        receipt = json.loads((image_root / f'image-{arch}.json').read_text())
        if (receipt['architecture'], receipt['source_revision'], receipt['version']) != (arch, revision, version):
            raise ValueError('Image receipt differs from the selected audit')
        archive = checked_file(image_root, receipt['file'], receipt['size'], receipt['sha256'])
        images.append((arch, receipt, archive))
    # Sources become permanently accessible before any image can be made public.
    tag = 'v' + version
    ensure_sources(tag, revision, assets, workspace)
    login()
    published = []
    for arch, receipt, archive in images:
        reference = IMAGE + ':' + version + '-' + arch
        config_digest = archive_config_digest(archive, arch, revision, version)
        run('docker', 'load', '--platform', 'linux/' + ARCHITECTURES[arch], '--input', str(archive))
        loaded_identity = loaded_image_identity(config_digest, arch, revision, version, workspace)
        remote = image_manifest(reference)
        if remote is None:
            run('docker', 'tag', loaded_identity, reference)
            # A saved OCI index can also contain build attestations. Publish
            # only its tested runtime platform; keep its exact config/layers.
            require_release(tag, revision)
            run('docker', 'push', '--platform', 'linux/' + ARCHITECTURES[arch], reference)
            remote = image_manifest(reference)
        if (remote is None or remote.get('config', {}).get('digest') != config_digest
                or not re.fullmatch(r'sha256:[0-9a-f]{64}', remote.get('digest', ''))):
            raise ValueError('Published platform differs from its tested runtime config')
        published.append(dict(receipt, reference=IMAGE + '@' + remote['digest'],
                              config_digest=config_digest))
    report = workspace / 'prebuilt-images.json'
    report.write_text(json.dumps(dict(schema=1, version=version, source_revision=revision,
                                     audit_run=run_id, images=published), indent=2) + '\n')
    retain_assets(tag, [report], workspace)
    print('Architecture images staged. Verify public package visibility, then run promote. App-store default unchanged.')


def promote(run_id, version, revision, workspace):
    run('gh', 'release', 'download', 'v' + version, '--repo', REPOSITORY,
        '--pattern', 'prebuilt-images.json', '--dir', str(workspace))
    report = json.loads((workspace / 'prebuilt-images.json').read_text())
    if (report['version'], report['source_revision'], report['audit_run']) != (version, revision, run_id):
        raise ValueError('Staged release differs from the audited candidate')
    if {i['architecture'] for i in report['images']} != set(ARCHITECTURES) or len(report['images']) != 2:
        raise ValueError('Both native image receipts are required')
    with tempfile.TemporaryDirectory(prefix='efx-anonymous-docker-') as config:
        environment = dict(os.environ, DOCKER_CONFIG=config)
        Path(config, 'config.json').write_text('{}\n')
        for item in report['images']:
            if not re.fullmatch(re.escape(IMAGE) + r'@sha256:[0-9a-f]{64}', item['reference']):
                raise ValueError('Unexpected image registry identity')
            platform = 'linux/' + ARCHITECTURES[item['architecture']]
            run('docker', 'pull', '--platform', platform, item['reference'], environment=environment)
            metadata = json.loads(run('docker', 'image', 'inspect', item['reference']))[0]
            validate_image(metadata, item['architecture'], revision, version)
        login()
        reference = IMAGE + ':' + version
        require_absent(reference)
        run('docker', 'buildx', 'imagetools', 'create', '--tag', reference,
            *(i['reference'] for i in report['images']))
        raw = run('docker', 'manifest', 'inspect', reference, environment=environment)
        manifest = json.loads(raw)
        actual = {(m['platform']['os'], m['platform']['architecture'], m['digest'])
                  for m in manifest['manifests']}
        expected = {('linux', ARCHITECTURES[i['architecture']], i['reference'].split('@')[1])
                    for i in report['images']}
        if actual != expected:
            raise ValueError('Published multi-architecture manifest differs')
    proof = workspace / 'prebuilt-manifest.json'
    proof.write_text(json.dumps(dict(version=version, source_revision=revision,
                                    reference=reference, manifest=manifest), indent=2) + '\n')
    run('gh', 'release', 'upload', 'v' + version, str(proof), '--repo', REPOSITORY)
    run('gh', 'release', 'edit', 'v' + version, '--repo', REPOSITORY, '--notes',
        'Experimental ready-built images are available for AMD64 and ARM64 and were verified with anonymous digest pulls. Matching source parts and original notices are attached. This is not hardware acceptance: real Eufy sign-in, eight live views, enlargement, restart and resource use still need controlled verification. The household app and default app-store pointer are unchanged. Donations are optional: https://buymeacoffee.com/efficientx')
    print('Versioned multi-architecture image verified publicly. Household hardware acceptance remains pending.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('record', 'prepare', 'stage', 'promote'))
    parser.add_argument('--version', required=True)
    parser.add_argument('--revision')
    parser.add_argument('--architecture', choices=tuple(ARCHITECTURES))
    parser.add_argument('--archive', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--audit-run')
    args = parser.parse_args()
    if os.environ.get('GITHUB_ACTIONS') != 'true':
        parser.error('Use disposable GitHub runners only; never a household host')
    if args.operation == 'record':
        record_image(args.architecture, args.revision, args.version, args.archive, args.output)
        return
    revision = audit_revision(args.audit_run)
    release_inputs(args.version, revision)
    if args.operation == 'prepare':
        prepare_tag(args.version, revision)
        return
    with tempfile.TemporaryDirectory(prefix='efx-public-release-') as folder:
        workspace = Path(folder)
        if args.operation == 'stage':
            stage(args.audit_run, args.version, revision, workspace)
        else:
            promote(args.audit_run, args.version, revision, workspace)


if __name__ == '__main__':
    main()
