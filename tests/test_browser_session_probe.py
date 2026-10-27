import hashlib
import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location('browser_session_probe', Path(__file__).resolve().parents[1] / 'bridge/browser_session_probe.py')
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


def fixture():
    old = {'userId': 'test-user', 'stationSn': 'test-station', 'region': 'US', 'authToken': 'old'}
    incoming = {'token': 'new', 'gtoken': hashlib.md5(b'test-user').hexdigest(), 'sn': 'test-station', 'region': 'US'}
    return old, incoming


def test_import_preserves_account_binding_and_changes_only_session():
    old, incoming = fixture()
    updated = probe.validate_session(old, incoming)
    assert updated['authToken'] == 'new'
    assert updated['userId'] == old['userId']
    assert old['authToken'] == 'old'


@pytest.mark.parametrize('field,value', [('gtoken','wrong'), ('sn','other'), ('region','EU'), ('token','')])
def test_import_rejects_wrong_binding(field, value):
    old, incoming = fixture()
    incoming[field] = value
    with pytest.raises(ValueError):
        probe.validate_session(old, incoming)


def test_private_atomic_write(tmp_path):
    target = tmp_path / 'session.json'
    probe.private_write(target, {'fixture': True})
    assert target.read_text() == '{"fixture": true}'
    assert not target.with_suffix('.probe-tmp').exists()
