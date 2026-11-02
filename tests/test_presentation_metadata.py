"""Validate actual presentation assets and source-package inclusion, offline."""
from pathlib import Path
import json
import struct
import sys
import zlib

import pytest
import yaml

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
import stage_public_repository as public
import stage_guided_candidate as guided


def png(path):
    data=path.read_bytes()
    assert data[:8]==b'\x89PNG\r\n\x1a\n'
    offset=8;dimensions=None;compressed=[];ended=False
    while offset<len(data):
        size=struct.unpack('>I',data[offset:offset+4])[0]
        kind=data[offset+4:offset+8];body=data[offset+8:offset+8+size]
        crc=struct.unpack('>I',data[offset+8+size:offset+12+size])[0]
        assert zlib.crc32(kind+body)&0xffffffff==crc
        if kind==b'IHDR':
            width,height,bits,colour,compression,filtering,interlace=struct.unpack('>IIBBBBB',body)
            assert (bits,colour,compression,filtering,interlace)==(8,6,0,0,0)
            dimensions=width,height
        elif kind==b'IDAT':compressed.append(body)
        elif kind==b'IEND':ended=True
        offset+=size+12
    assert offset==len(data) and ended and dimensions
    width,height=dimensions
    raw=zlib.decompress(b''.join(compressed))
    assert len(raw)==height*(width*4+1)
    return dimensions


@pytest.mark.parametrize('name,dimensions',[
    ('custom_components/eufy_nvr/brand/icon.png',(256,256)),
    ('custom_components/eufy_nvr/brand/icon@2x.png',(512,512)),
    ('custom_components/eufy_nvr/brand/logo.png',(660,200)),
    ('custom_components/eufy_nvr/brand/logo@2x.png',(1320,400)),
    ('custom_components/eufy_nvr/brand/dark_logo.png',(660,200)),
    ('custom_components/eufy_nvr/brand/dark_logo@2x.png',(1320,400)),
    ('eufy_nvr/icon.png',(128,128)),('eufy_nvr/logo.png',(330,100)),
])
def test_lossless_rgba_png_dimensions_and_crc(name,dimensions):
    assert png(ROOT/name)==dimensions


def test_hacs_keys_and_no_duplicate_update_provider():
    metadata=json.loads((ROOT/'hacs.json').read_text())
    assert set(metadata)<={'name','content_in_root','render_readme','homeassistant','hide_default_branch'}
    assert metadata['hide_default_branch'] is True
    assert not (ROOT/'custom_components/eufy_nvr/update.py').exists()
    component=json.loads((ROOT/'custom_components/eufy_nvr/manifest.json').read_text())
    assert 'release_url' not in component
    assert len((ROOT/'release-summary.txt').read_text().strip())<=255


def test_assets_survive_both_packaging_paths(tmp_path):
    target=tmp_path/'public'
    digest,_=public.stage(target)
    public.verify(target,digest)
    for name in public.SOURCE_FILES:
        if name.endswith('.png') or name.startswith('artwork/'):
            assert (target/name).read_bytes()==(ROOT/name).read_bytes()
    assert {'README.md','icon.png','logo.png'}<=set(guided.TOP_FILES)
    assert {'brand/icon.png','brand/icon@2x.png'}<=set(guided.COMPONENT_FILES)
    config=yaml.safe_load((target/'eufy_nvr/config.yaml').read_text())
    assert config['stage']=='experimental' and config['boot']=='manual'
