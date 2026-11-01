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


@pytest.mark.parametrize('changed_config', [False, True])
def test_loaded_store_id_may_change_but_runtime_config_must_not(tmp_path, monkeypatch, changed_config):
    labels = {'org.opencontainers.image.revision': REVISION,
              'org.opencontainers.image.version': '1.0.0-alpha.1',
              'io.hass.arch': 'aarch64', 'io.hass.version': '1.0.0-alpha.1'}
    config = dict(os='linux', architecture='arm64', config=dict(Labels=labels),
                  rootfs=dict(type='layers', diff_ids=['sha256:' + 'c' * 64]))
    audited = json.dumps(config).encode()
    expected_digest = 'sha256:' + hashlib.sha256(audited).hexdigest()
    imported_id = 'sha256:' + 'd' * 64
    assert imported_id != expected_digest
    def command(*args):
        assert '--platform' in args and 'linux/arm64' in args
        assert args[-1] == 'eufy-source-audit:latest'
        if args[:3] == ('docker', 'image', 'inspect'):
            return json.dumps([dict(Id=imported_id, Os='linux', Architecture='arm64',
                                    Config=dict(Labels=labels))])
        assert args[:3] == ('docker', 'image', 'save')
        # An imported image can retain its public labels while its layer content
        # changes. A plain-tar export must match the original config hash too.
        if changed_config:
            config['rootfs']['diff_ids'] = ['sha256:' + 'e' * 64]
        raw = json.dumps(config).encode()
        digest = hashlib.sha256(raw).hexdigest()
        filename = digest + '.json'
        with tarfile.open(args[args.index('--output') + 1], 'w') as archive:
            for name, content in [(filename, raw), ('manifest.json', json.dumps([dict(Config=filename)]).encode())]:
                member = tarfile.TarInfo(name)
                member.size = len(content)
                archive.addfile(member, io.BytesIO(content))
        return ''
    monkeypatch.setattr(release, 'run', command)
    if changed_config:
        with pytest.raises(ValueError, match='Loaded runtime config differs'):
            release.loaded_image_identity(expected_digest, 'aarch64', REVISION, '1.0.0-alpha.1', tmp_path)
    else:
        assert release.loaded_image_identity(expected_digest, 'aarch64', REVISION,
            '1.0.0-alpha.1', tmp_path) == imported_id


VERSION = '1.0.0-alpha.1'
TAG = 'v' + VERSION


@pytest.fixture
def publication_state(monkeypatch):
    import base64
    state = dict(ref=None, release=None, calls=[], events=[])
    def ref(sha=REVISION):
        return dict(ref='refs/tags/' + TAG, object=dict(type='commit', sha=sha))
    state['make_ref'] = ref
    def lookup(path):
        state['events'].append(('get', path))
        if path.startswith('contents/'):
            assert path.endswith('?ref=' + REVISION)
            raw = (json.dumps(dict(version=state.get('source_version', VERSION)))
                   if '/manifest.json?' in path else 'version: ' + state.get('app_version', VERSION) + '\n')
            return dict(encoding='base64', content=base64.b64encode(raw.encode()).decode())
        if path == 'git/ref/tags/' + TAG:
            if state.get('lookup_error'):
                raise RuntimeError('Reference lookup failed')
            return state['ref']
        if path == 'releases/tags/' + TAG:
            return state['release']
        pytest.fail('Unexpected API read: ' + path)
    def command(*args, **kwargs):
        state['calls'].append((args, kwargs))
        state['events'].append(('command', args))
        if args[:4] == ('gh', 'api', '--method', 'POST'):
            assert args[4:] == (f'repos/{release.REPOSITORY}/git/refs', '--input', '-')
            assert json.loads(kwargs['data']) == dict(ref='refs/tags/' + TAG, sha=REVISION)
            state['ref'] = ref(state.get('created_sha', REVISION))
            if state.get('fail_read_after_post'):
                state['lookup_error'] = True
        elif args[:3] == ('gh', 'release', 'create'):
            assert state['ref'] == ref()
            assert '--verify-tag' in args and '--target' not in args
            state['release'] = dict(tag_name=TAG, draft=False, prerelease=True, assets=[])
            if state.get('change_after_release'):
                state['ref'] = ref('2' * 40)
        else:
            pytest.fail('Unexpected public write: ' + repr(args))
        return ''
    monkeypatch.setattr(release, 'api', lookup)
    monkeypatch.setattr(release, 'optional_api', lookup)
    monkeypatch.setattr(release, 'run', command)
    return state


def test_prepare_creates_explicit_sha_then_reads_back_without_release_or_image(publication_state):
    state = publication_state
    release.prepare_tag(VERSION, REVISION)
    assert len(state['calls']) == 1
    assert state['events'][-1] == ('get', 'git/ref/tags/' + TAG)
    assert state['release'] is None


def test_prepare_is_idempotent_for_existing_exact_ref(publication_state):
    state = publication_state
    state['ref'] = state['make_ref']()
    release.prepare_tag(VERSION, REVISION)
    assert state['calls'] == []


def test_prepare_cli_finishes_without_staging_or_promoting(monkeypatch):
    calls = []
    monkeypatch.setenv('GITHUB_ACTIONS', 'true')
    monkeypatch.setattr(sys, 'argv', ['prebuilt_release.py', 'prepare',
                                    '--audit-run', '123', '--version', VERSION])
    monkeypatch.setattr(release, 'audit_revision', lambda run_id: REVISION)
    monkeypatch.setattr(release, 'prepare_tag', lambda *args: calls.append(args))
    monkeypatch.setattr(release, 'stage', lambda *args: pytest.fail('Prepare must not stage'))
    monkeypatch.setattr(release, 'promote', lambda *args: pytest.fail('Prepare must not promote'))
    release.main()
    assert calls == [(VERSION, REVISION)]


def test_prepare_workflow_preserves_existing_permission_boundary():
    import yaml
    root = Path(__file__).resolve().parents[1]
    workflow = yaml.safe_load((root / '.github/workflows/publish-prebuilt.yml').read_text())
    events = workflow.get('on', workflow.get(True))
    assert events['workflow_dispatch']['inputs']['operation']['options'] == ['prepare', 'stage', 'promote']
    assert workflow['jobs']['publish']['permissions'] == {
        'actions': 'read', 'contents': 'write', 'packages': 'write'}
    assert workflow['permissions'] == {'contents': 'read'}
    assert 'github.event.repository.default_branch' in workflow['jobs']['publish']['if']


def test_wrong_existing_ref_is_never_retargeted(publication_state):
    state = publication_state
    state['ref'] = state['make_ref']('2' * 40)
    with pytest.raises(ValueError, match='exact experimental source commit'):
        release.prepare_tag(VERSION, REVISION)
    assert state['calls'] == []


def test_post_success_cannot_substitute_for_authoritative_ref_readback(publication_state):
    state = publication_state
    state['created_sha'] = '2' * 40
    with pytest.raises(ValueError, match='exact experimental source commit'):
        release.prepare_tag(VERSION, REVISION)
    assert len(state['calls']) == 1 and state['release'] is None


def test_uncertain_post_outcome_is_not_retried(publication_state):
    state = publication_state
    state['fail_read_after_post'] = True
    with pytest.raises(RuntimeError, match='lookup failed'):
        release.prepare_tag(VERSION, REVISION)
    assert len(state['calls']) == 1 and state['release'] is None


@pytest.mark.parametrize('field', ['source_version', 'app_version'])
def test_prepare_rejects_wrong_source_version_before_any_write(publication_state, field):
    state = publication_state
    state[field] = '1.0.0-alpha.2'
    with pytest.raises(ValueError, match='package versions differ'):
        release.prepare_tag(VERSION, REVISION)
    assert state['calls'] == []


@pytest.mark.parametrize('identity', [
    dict(ref='refs/tags/' + TAG, object=dict(type='tag', sha=REVISION)),
    dict(ref='refs/heads/' + TAG, object=dict(type='commit', sha=REVISION)),
    dict(ref='refs/tags/' + TAG, object=dict(type='commit', sha='2' * 40)),
])
def test_ref_identity_must_be_the_exact_lightweight_tag(publication_state, identity):
    publication_state['ref'] = identity
    with pytest.raises(ValueError, match='exact experimental source commit'):
        release.require_tag(TAG, REVISION)
    assert publication_state['calls'] == []


def test_new_release_uses_preverified_tag_and_rechecks_after_creation(tmp_path, publication_state):
    state = publication_state
    state['ref'] = state['make_ref']()
    release.ensure_sources(TAG, REVISION, [], tmp_path)
    commands = [event for event in state['events'] if event[0] == 'command']
    assert len(commands) == 1 and commands[0][1][:3] == ('gh', 'release', 'create')
    create_index = state['events'].index(commands[0])
    assert ('get', 'git/ref/tags/' + TAG) in state['events'][:create_index]
    assert ('get', 'git/ref/tags/' + TAG) in state['events'][create_index + 1:]
    assert state['events'][-1] == ('get', 'releases/tags/' + TAG)


def test_post_create_wrong_sha_stops_before_asset_upload(tmp_path, publication_state):
    state = publication_state
    state['ref'] = state['make_ref']()
    state['change_after_release'] = True
    asset = tmp_path / 'public.part00'
    asset.write_bytes(b'public test source')
    with pytest.raises(ValueError, match='exact experimental source commit'):
        release.ensure_sources(TAG, REVISION, [asset], tmp_path)
    assert [call[0][:3] for call in state['calls']] == [('gh', 'release', 'create')]


@pytest.mark.parametrize('changed', [dict(tag_name='wrong-tag'), dict(draft=True), dict(prerelease=False)])
def test_release_metadata_is_authoritative_not_target_label(publication_state, changed):
    state = publication_state
    state['ref'] = state['make_ref']()
    state['release'] = dict(tag_name=TAG, draft=False, prerelease=True,
                            target_commitish=REVISION)
    state['release'].update(changed)
    with pytest.raises(ValueError, match='public experimental tag'):
        release.require_release(TAG, REVISION)
    assert state['calls'] == []


@pytest.mark.parametrize('drift_arch', [None, 'amd64', 'aarch64'])
def test_store_scoped_ids_are_not_tag_sources_and_ref_drift_stops_the_affected_push(
        tmp_path, monkeypatch, publication_state, drift_arch):
    state = publication_state
    state['ref'] = state['make_ref']()
    state['release'] = dict(tag_name=TAG, draft=False, prerelease=True, assets=[])
    for arch in release.ARCHITECTURES:
        summary = tmp_path / ('source-summary-' + arch) / 'source-parts'
        summary.mkdir(parents=True)
        (summary / f'eufy-nvr-sources-{arch}.json').write_text(json.dumps(dict(
            architecture=arch, source_revision=REVISION, parts=[])))
        image = tmp_path / ('candidate-image-' + arch)
        image.mkdir()
        archive = image / 'image.tar.gz'
        archive.write_bytes(b'synthetic archive; never executed')
        (image / f'image-{arch}.json').write_text(json.dumps(dict(
            architecture=arch, source_revision=REVISION, version=VERSION,
            file=archive.name, size=archive.stat().st_size, sha256=release.sha256(archive))))
    config_digest = 'sha256:' + 'c' * 64
    published = set()
    original_command = release.run
    def command(*args, **kwargs):
        if args[:3] == ('gh', 'run', 'download') or args[:3] == ('gh', 'release', 'upload'):
            return ''
        if args[0] == 'docker':
            state['calls'].append((args, kwargs))
            if args[1] == 'tag':
                # Docker 29 can inspect/export a platform by the local name
                # while its platform-scoped ID is not a globally taggable ID.
                assert args[2] == 'eufy-source-audit:latest'
            if (args[1] == 'load' and drift_arch is not None
                    and 'linux/' + release.ARCHITECTURES[drift_arch] in args):
                state['ref'] = state['make_ref']('2' * 40)
            if args[1] == 'push':
                published.add(args[-1])
            return ''
        return original_command(*args, **kwargs)
    monkeypatch.setattr(release, 'run', command)
    monkeypatch.setattr(release, 'source_parts', lambda *args: [])
    monkeypatch.setattr(release, 'login', lambda: None)
    monkeypatch.setattr(release, 'archive_config_digest', lambda *args: config_digest)
    monkeypatch.setattr(release, 'loaded_image_identity', lambda *args: 'sha256:' + 'd' * 64)
    monkeypatch.setattr(release, 'image_manifest', lambda reference:
        dict(config=dict(digest=config_digest), digest='sha256:' + 'e' * 64)
        if reference in published else None)
    if drift_arch is None:
        release.stage('123', VERSION, REVISION, tmp_path)
        report = json.loads((tmp_path / 'prebuilt-images.json').read_text())
        assert {entry['architecture'] for entry in report['images']} == {'amd64', 'aarch64'}
        assert all(entry['config_digest'] == config_digest for entry in report['images'])
        assert len(published) == 2
    else:
        with pytest.raises(ValueError, match='exact experimental source commit'):
            release.stage('123', VERSION, REVISION, tmp_path)
        assert len(published) == (0 if drift_arch == 'amd64' else 1)
        assert release.IMAGE + ':' + VERSION + '-' + drift_arch not in published
