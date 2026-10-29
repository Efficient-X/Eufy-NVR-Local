"""Boundaries for build-image evidence: refuse private paths and preserve notices."""
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('image_inventory', ROOT / 'tools/image_inventory.py')
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


def test_notice_copy_preserves_bytes_and_reports_hash(tmp_path):
    public = tmp_path / 'public'
    public.mkdir()
    notice = public / 'copyright'
    notice.write_bytes(b'Example licence\nCopyright the authors\n')
    output = tmp_path / 'review'
    entry = audit.copy_notice(notice, output, 'example.txt', roots=(public,))
    copied = output / entry['file']
    assert copied.read_bytes() == notice.read_bytes()
    import hashlib
    assert entry['sha256'] == hashlib.sha256(notice.read_bytes()).hexdigest()


def test_notice_symlink_cannot_escape_public_software_tree(tmp_path):
    public = tmp_path / 'public'
    public.mkdir()
    private = tmp_path / 'private'
    private.mkdir()
    secret = private / 'settings'
    secret.write_text('private fixture')
    link = public / 'copyright'
    try:
        link.symlink_to(secret)
    except OSError:
        pytest.skip('Symlink creation unavailable on this test system')
    with pytest.raises(ValueError, match='outside'):
        audit.copy_notice(link, tmp_path / 'out', 'notice.txt', roots=(public,))
    assert not (tmp_path / 'out').exists()


@pytest.mark.parametrize('label', ['../private.txt', 'folder/file', 'folder\\file', '.', ''])
def test_notice_label_cannot_escape_output(tmp_path, label):
    public = tmp_path / 'public'
    public.mkdir()
    notice = public / 'notice'
    notice.write_text('licence')
    with pytest.raises(ValueError, match='label'):
        audit.copy_notice(notice, tmp_path / 'out', label, roots=(public,))


def test_inventory_refuses_to_overwrite_existing_evidence(tmp_path):
    output = tmp_path / 'existing'
    output.mkdir()
    with pytest.raises(FileExistsError):
        audit.inventory(output)
