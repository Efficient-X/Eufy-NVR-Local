"""Verify supplemental-source identity and split archives without running apps."""
import hashlib
import io
import json
from pathlib import Path
import sys
import tarfile

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import extra_image_sources as extras
import image_sources as sources
import source_bundle


def test_split_archive_reassembles_exact_source_bytes(tmp_path):
    root, output = tmp_path / 'source', tmp_path / 'parts'
    root.mkdir()
    (root / 'notice.txt').write_text('Copyright the upstream authors\n')
    (root / 'package.tar.gz').write_bytes(bytes(range(256)) * 25)
    report = source_bundle.bundle(root, output, 'amd64', 'a' * 40, part_size=2048)
    reconstructed = b''
    for part in report['parts']:
        raw = (output / part['file']).read_bytes()
        assert len(raw) <= 2048
        assert hashlib.sha256(raw).hexdigest() == part['sha256']
        reconstructed += raw
    assert hashlib.sha256(reconstructed).hexdigest() == report['sha256']
    with tarfile.open(fileobj=io.BytesIO(reconstructed)) as archive:
        assert archive.extractfile('source-review/notice.txt').read() == (root / 'notice.txt').read_bytes()
        assert archive.extractfile('source-review/package.tar.gz').read() == (root / 'package.tar.gz').read_bytes()
        assert archive.getmember('source-review/notice.txt').uid == 0
    assert json.loads((output / 'eufy-nvr-sources-amd64.json').read_text()) == report


def test_bundle_does_not_overwrite_or_archive_its_own_output(tmp_path):
    root = tmp_path / 'source'
    root.mkdir()
    with pytest.raises(ValueError, match='outside'):
        source_bundle.bundle(root, root / 'parts', 'amd64', 'a' * 40)
    output = tmp_path / 'existing'
    output.mkdir()
    with pytest.raises(FileExistsError):
        source_bundle.bundle(root, output, 'amd64', 'a' * 40)


def test_go_binary_must_match_clean_pinned_source():
    revision = 'a' * 40
    info = 'binary: go1.24.2\n\tbuild\tvcs.revision=' + revision + '\n\tbuild\tvcs.modified=false\n'
    assert extras.go_build_info(info, revision) == 'go1.24.2'
    with pytest.raises(ValueError, match='revision'):
        extras.go_build_info(info, 'b' * 40)
    with pytest.raises(ValueError, match='clean'):
        extras.go_build_info(info.replace('modified=false', 'modified=true'), revision)
    with pytest.raises(ValueError, match='toolchain'):
        extras.go_build_info(info.replace('go1.24.2', 'devel'), revision)


def test_go_checksum_must_describe_the_exact_source_archive():
    catalogue = [dict(version='go1.24.2', files=[dict(kind='source',
                       filename='go1.24.2.src.tar.gz', sha256='a' * 64)])]
    assert extras.go_source_metadata(catalogue, 'go1.24.2')['sha256'] == 'a' * 64
    with pytest.raises(ValueError, match='unique source checksum'):
        extras.go_source_metadata(catalogue, 'go1.24.3')
    catalogue[0]['files'][0]['kind'] = 'archive'
    with pytest.raises(ValueError, match='unique source checksum'):
        extras.go_source_metadata(catalogue, 'go1.24.2')


def test_upstream_archive_excludes_git_state(tmp_path):
    root = tmp_path / 'checkout'
    (root / '.git').mkdir(parents=True)
    (root / '.git/config').write_text('not public payload')
    (root / 'LICENSE').write_text('upstream licence')
    archive_path = tmp_path / 'public.tar.gz'
    extras.source_archive(root, archive_path, 'example')
    with tarfile.open(archive_path) as archive:
        assert archive.getnames() == ['example/LICENSE']


def test_upstream_archive_preserves_only_internal_relative_links(tmp_path):
    root = tmp_path / 'checkout'
    root.mkdir()
    (root / 'LICENSE').write_text('upstream licence')
    link = root / 'COPYING'
    try:
        link.symlink_to('LICENSE')
    except OSError:
        pytest.skip('Symlink creation unavailable on this test system')
    archive_path = tmp_path / 'public.tar.gz'
    extras.source_archive(root, archive_path, 'example')
    with tarfile.open(archive_path) as archive:
        member = archive.getmember('example/COPYING')
        assert member.issym() and member.linkname == 'LICENSE'


def test_upstream_archive_refuses_links_outside_the_checkout(tmp_path):
    root = tmp_path / 'checkout'
    root.mkdir()
    private = tmp_path / 'private'
    private.write_text('private fixture')
    try:
        (root / 'COPYING').symlink_to(private)
    except OSError:
        pytest.skip('Symlink creation unavailable on this test system')
    with pytest.raises(ValueError, match='escapes'):
        extras.source_archive(root, tmp_path / 'public.tar.gz', 'example')


def test_sbom_crate_uses_fixed_registry_and_exact_hash():
    component = dict(name='example', version='1.2.3', purl='pkg:cargo/example@1.2.3',
                     hashes=[dict(alg='SHA-256', content='a' * 64)])
    result = sources.sbom_sources({'components': [component]})
    assert result[0]['url'] == 'https://static.crates.io/crates/example/example-1.2.3.crate'
    assert result[0]['sha256'] == 'a' * 64
    component['hashes'] = []
    with pytest.raises(ValueError, match='SHA-256'):
        sources.sbom_sources({'components': [component]})


def test_sbom_openssl_cannot_redirect_to_an_unrelated_source():
    component = dict(name='openssl', version='4.0.2', purl='pkg:generic/openssl@4.0.2',
                     hashes=[dict(alg='SHA-256', content='a' * 64)],
                     externalReferences=[dict(type='distribution', url='https://example.org/source')])
    with pytest.raises(ValueError, match='expected source'):
        sources.sbom_sources({'components': [component]})


def test_sbom_must_match_its_inventory_hash(tmp_path):
    metadata = tmp_path / 'metadata'
    metadata.mkdir()
    (metadata / 'bom.json').write_text('{"components": []}')
    inventory = {'python': [{'sboms': [{'file': 'bom.json', 'sha256': '0' * 64}]}]}
    with pytest.raises(ValueError, match='differs'):
        sources.collect_sbom_sources(inventory, metadata, tmp_path / 'output')


def test_almalinux_source_uses_reviewed_source_hash_not_the_binary_hash():
    bom = {'components': [dict(name='libxcb', version='1.13.1-1.el8',
                              purl='pkg:rpm/almalinux/libxcb@1.13.1-1.el8',
                              hashes=[dict(alg='SHA-256', content='0' * 64)])]}
    source = sources.sbom_sources(bom)[0]
    assert source['sha256'] == sources.ALMA_SOURCE_RPMS[('libxcb', '1.13.1-1.el8')][1]
    assert source['filename'].endswith('.src.rpm')


def test_repository_notices_preserve_text_without_copying_git_state(tmp_path):
    root = tmp_path / 'source'
    (root / '.git').mkdir(parents=True)
    (root / 'COPYING').write_bytes(b'Copyright upstream authors\nLicence text\n')
    (root / '.git/LICENSE').write_text('private fixture')
    records = extras.repository_notices(root, tmp_path / 'review', 'example')
    assert len(records) == 1
    assert (tmp_path / 'review' / records[0]['file']).read_bytes() == (root / 'COPYING').read_bytes()


def test_overlay_recipe_change_fails_before_any_download(tmp_path, monkeypatch):
    root = tmp_path / 'overlay'
    (root / 'conf').mkdir(parents=True)
    (root / 'conf/versions').write_text('SKALIBS_VERSION=v0.0.0\n')
    def must_not_download(*args, **kwargs):
        pytest.fail('A mismatched recipe must not download anything')
    monkeypatch.setattr(extras, 'download', must_not_download)
    with pytest.raises(ValueError, match='pinned build recipe'):
        extras.collect_overlay_sources(root, tmp_path / 'review')
