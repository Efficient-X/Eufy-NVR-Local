"""Public source export: exact inventory, preview defaults and safe boundaries."""
from pathlib import Path
import sys
import json

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import stage_public_repository as public


def test_public_export_has_matching_preview_and_funding(tmp_path):
    config_path = ROOT / 'eufy_nvr/config.yaml'
    original = config_path.read_bytes()
    target = tmp_path / 'preview'
    digest, count = public.stage(target)
    assert public.verify(target, digest) == count
    assert config_path.read_bytes() == original
    config = yaml.safe_load((target / 'eufy_nvr/config.yaml').read_text())
    manifest = json.loads((target / 'custom_components/eufy_nvr/manifest.json').read_text())
    assert config['version'] == public.APP_VERSION == '0.8.0-alpha.2'
    assert manifest['version'] == public.VERSION == '0.8.0-alpha.3'
    assert config['options']['setup_mode'] == 'guided'
    assert config.get('image') == public.PUBLIC_IMAGE
    assert config['options']['go2rtc_password'] == ''
    assert not {'email','password','browser_session_url'}.intersection(config['options'])
    assert config['boot'] == 'manual' and config['panel_admin'] and config['homeassistant_api']
    assert 'FROM guided AS release' in (target / 'eufy_nvr/Dockerfile').read_text()
    assert (target / '.github/FUNDING.yml').read_text().strip() == 'buy_me_a_coffee: efficientx'
    assert 'https://buymeacoffee.com/efficientx' in (target / 'README.md').read_text()
    assert not (target / '.git').exists()
    assert len(list(target.rglob('*.wasm'))) == 0
    assert not list(target.rglob('auth.json'))


def test_public_export_refuses_overwrite(tmp_path):
    target = tmp_path / 'preview'
    target.mkdir()
    (target / 'keep.txt').write_text('keep')
    with pytest.raises(FileExistsError):
        public.stage(target)
    assert (target / 'keep.txt').read_text() == 'keep'


def test_public_export_rejects_unreviewed_image_before_writing(tmp_path, monkeypatch):
    read = public._read
    def changed(root, name):
        data = read(root, name)
        if name == 'eufy_nvr/config.yaml':
            config = yaml.safe_load(data)
            config['image'] = 'example.invalid/unreviewed'
            return yaml.safe_dump(config).encode()
        return data
    monkeypatch.setattr(public, '_read', changed)
    target = tmp_path / 'preview'
    with pytest.raises(ValueError, match='reviewed EFX registry'):
        public.stage(target)
    assert not target.exists()


def test_public_verify_rejects_added_or_modified_files(tmp_path):
    target = tmp_path / 'preview'
    digest, _ = public.stage(target)
    marker = target / 'unexpected.txt'
    marker.write_text('not reviewed')
    with pytest.raises(ValueError):
        public.verify(target,digest)
    marker.unlink()
    (target / 'README.md').write_text('changed')
    with pytest.raises(ValueError):
        public.verify(target,digest)


def test_public_export_rejects_missing_or_nested_sources(tmp_path):
    with pytest.raises(ValueError):
        public.stage(tmp_path / 'inside',root=tmp_path)
    outside = tmp_path / 'missing'
    with pytest.raises(ValueError):
        public.stage(outside,root=tmp_path / 'absent')
    assert not outside.exists()
