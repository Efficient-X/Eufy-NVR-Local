"""Release byte identities must fail closed before any registry publication."""
import hashlib
import io
import json
from pathlib import Path
import sys
import tarfile
import zipfile

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import prebuilt_release as release

REVISION = '1' * 40


@pytest.mark.parametrize('version', ['latest', '1.0.0', '1.0.0-alpha.1;echo bad', '../alpha.1'])
def test_only_explicit_experimental_versions_are_accepted(version):
    with pytest.raises(ValueError):
        release.release_inputs(version, REVISION)


def test_image_identity_requires_architecture_revision_and_ha_labels():
    labels = {'org.opencontainers.image.revision': REVISION,
              'org.opencontainers.image.version': '1.0.0-alpha.1',
              'io.hass.arch': 'aarch64', 'io.hass.version': '1.0.0-alpha.1'}
    metadata = dict(Os='linux', Architecture='arm64', Config=dict(Labels=labels))
    release.validate_image(metadata, 'aarch64', REVISION, '1.0.0-alpha.1')
    labels['io.hass.version'] = '0.9.0'
    with pytest.raises(ValueError, match='labels differ'):
        release.validate_image(metadata, 'aarch64', REVISION, '1.0.0-alpha.1')


def test_source_bundle_checks_parts_and_joined_bytes(tmp_path):
    raw = b'public source evidence'
    path = tmp_path / 'source.part00'
    path.write_bytes(raw)
    item = dict(file=path.name, size=len(raw), sha256=hashlib.sha256(raw).hexdigest())
    manifest = dict(source_revision=REVISION, format='concatenated-tar',
                    size=len(raw), sha256=item['sha256'], parts=[item])
    assert release.source_parts(tmp_path, manifest, REVISION) == [path]
    manifest['sha256'] = '0' * 64
    with pytest.raises(ValueError, match='Joined source'):
        release.source_parts(tmp_path, manifest, REVISION)
    path.write_bytes(b'changed')
    with pytest.raises(ValueError, match='asset bytes differ'):
        release.source_parts(tmp_path, manifest, REVISION)


def test_release_files_cannot_escape_the_download_folder(tmp_path):
    with pytest.raises(ValueError, match='asset identity'):
        release.checked_file(tmp_path, '../outside', 1, '0' * 64)


def test_failed_or_foreign_source_audit_cannot_be_published(monkeypatch):
    audit = dict(event='pull_request', status='completed', conclusion='success',
                 path='.github/workflows/source-audit.yml', head_sha=REVISION,
                 head_repository=dict(full_name=release.REPOSITORY))
    monkeypatch.setattr(release, 'api', lambda path: audit)
    with pytest.raises(ValueError, match='manual source audit'):
        release.audit_revision('123')


def test_exact_commit_ci_must_pass_in_addition_to_source_collection(monkeypatch):
    audit = dict(event='workflow_dispatch', status='completed', conclusion='success',
                 path='.github/workflows/source-audit.yml', head_sha=REVISION,
                 head_repository=dict(full_name=release.REPOSITORY))
    failed_ci = dict(id=99, path='.github/workflows/ci.yml', conclusion='failure')
    monkeypatch.setattr(release, 'api', lambda path: audit if path.startswith('actions/runs/123')
                        else dict(workflow_runs=[failed_ci]))
    with pytest.raises(ValueError, match='complete CI'):
        release.audit_revision('123')


def test_publisher_is_manual_default_branch_only_and_pr_audits_remain_read_only():
    import yaml
    root = Path(__file__).resolve().parents[1]
    publisher = yaml.safe_load((root / '.github/workflows/publish-prebuilt.yml').read_text())
    # PyYAML's YAML 1.1 loader interprets the standard Actions key 'on' as True.
    events = publisher.get('on', publisher.get(True))
    assert set(events) == {'workflow_dispatch'}
    assert 'github.event.repository.default_branch' in publisher['jobs']['publish']['if']
    assert publisher['permissions'] == {'contents': 'read'}
    audit = yaml.safe_load((root / '.github/workflows/source-audit.yml').read_text())
    assert audit['permissions'] == {'contents': 'read'}
    assert all('packages' not in job.get('permissions', {}) for job in audit['jobs'].values())


@pytest.mark.parametrize('oci_layout', [False, True])
def test_saved_runtime_config_identity_survives_classic_and_oci_stores(tmp_path, oci_layout):
    config = dict(os='linux', architecture='amd64', config=dict(Labels={
        'org.opencontainers.image.revision': REVISION,
        'org.opencontainers.image.version': '1.0.0-alpha.1',
        'io.hass.arch': 'amd64', 'io.hass.version': '1.0.0-alpha.1'}))
    raw = json.dumps(config).encode()
    digest = hashlib.sha256(raw).hexdigest()
    name = 'blobs/sha256/' + digest if oci_layout else digest + '.json'
    path = tmp_path / 'image.tar.gz'
    with tarfile.open(path, 'w:gz') as archive:
        for filename, content in [(name, raw), ('manifest.json', json.dumps([dict(Config=name)]).encode())]:
            info = tarfile.TarInfo(filename)
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))
    assert release.archive_config_digest(path, 'amd64', REVISION, '1.0.0-alpha.1') == 'sha256:' + digest


def test_notice_retry_ignores_zip_timestamps_but_rejects_changed_text(tmp_path):
    a, b = tmp_path / 'a.zip', tmp_path / 'b.zip'
    for path, date in [(a, (2020, 1, 1, 0, 0, 0)), (b, (2026, 1, 1, 0, 0, 0))]:
        with zipfile.ZipFile(path, 'w') as archive:
            archive.writestr(zipfile.ZipInfo('original-notice.txt', date), b'Original copyright and terms')
    assert release.zip_contents(a) == release.zip_contents(b)
    with zipfile.ZipFile(b, 'w') as archive:
        archive.writestr('original-notice.txt', b'changed')
    assert release.zip_contents(a) != release.zip_contents(b)


def test_published_source_mismatch_stops_without_upload_or_overwrite(tmp_path, monkeypatch):
    path = tmp_path / 'source.part00'
    path.write_bytes(b'expected')
    monkeypatch.setattr(release, 'api', lambda name: dict(assets=[dict(
        name=path.name, size=path.stat().st_size, digest='sha256:' + '0' * 64)]))
    monkeypatch.setattr(release, 'run', lambda *args, **kwargs: pytest.fail('Must not upload changed sources'))
    with pytest.raises(ValueError, match='refusing replacement'):
        release.retain_assets('v1.0.0-alpha.1', [path], tmp_path)


def test_existing_version_tag_cannot_be_retargeted_by_resume(tmp_path, monkeypatch):
    monkeypatch.setattr(release, 'optional_api', lambda name: dict(draft=False, prerelease=True))
    monkeypatch.setattr(release, 'api', lambda name: dict(object=dict(type='commit', sha='2' * 40)))
    monkeypatch.setattr(release, 'run', lambda *args, **kwargs: pytest.fail('Must not replace the tag'))
    with pytest.raises(ValueError, match='exact experimental source commit'):
        release.ensure_sources('v1.0.0-alpha.1', REVISION, [], tmp_path)


def test_remote_manifest_identity_hashes_original_registry_bytes(monkeypatch):
    import subprocess
    raw = b'{ "schemaVersion":2, "config":{"digest":"sha256:example"} }'
    monkeypatch.setattr(release.subprocess, 'run', lambda *args, **kwargs:
                        subprocess.CompletedProcess([], 0, stdout=raw, stderr=b''))
    manifest = release.image_manifest('public-image-reference')
    assert manifest['config']['digest'] == 'sha256:example'
    assert manifest['digest'] == 'sha256:' + hashlib.sha256(raw).hexdigest()


def test_docker_push_error_never_echoes_the_job_token(monkeypatch):
    import subprocess
    monkeypatch.setenv('GH_TOKEN', 'test-only-job-token')
    monkeypatch.setattr(release.subprocess, 'run', lambda *args, **kwargs:
                        subprocess.CompletedProcess([], 1, stdout='',
                            stderr='denied test-only-job-token https://example.com/private'))
    with pytest.raises(RuntimeError) as error:
        release.run('docker', 'push', 'public-image-reference')
    assert 'denied' in str(error.value)
    assert 'test-only-job-token' not in str(error.value)
    assert 'example.com' not in str(error.value)
