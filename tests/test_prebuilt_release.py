"""Release byte identities must fail closed before any registry publication."""
import hashlib
from pathlib import Path
import sys

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
