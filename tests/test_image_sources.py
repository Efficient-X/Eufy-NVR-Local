"""Guard download identity and data-only upstream recipe inspection."""
import hashlib
import io
from pathlib import Path
import sys
from urllib.error import URLError

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import image_sources as sources


class Response(io.BytesIO):
    def __init__(self, content, url='https://files.pythonhosted.org/source.tar.gz'):
        super().__init__(content)
        self.url = url

    def geturl(self):
        return self.url


def test_checked_download_keeps_exact_bytes(tmp_path):
    content = b'public source archive'
    digest = hashlib.sha256(content).hexdigest()
    target = tmp_path / 'source.tar.gz'
    result = sources.download('https://files.pythonhosted.org/source.tar.gz', target,
                              digest, opener=lambda *a, **k: Response(content))
    assert target.read_bytes() == content
    assert result['sha256'] == digest
    assert result['size'] == len(content)


def test_download_rejects_changed_content_and_cleans_partial(tmp_path):
    target = tmp_path / 'source.tar.gz'
    with pytest.raises(ValueError, match='SHA-256'):
        sources.download('https://example.org/source', target, '0' * 64,
                         opener=lambda *a, **k: Response(b'changed archive'))
    assert list(tmp_path.iterdir()) == []


def test_download_rejects_insecure_redirect(tmp_path):
    target = tmp_path / 'source.tar.gz'
    with pytest.raises(ValueError, match='redirect'):
        sources.download('https://example.org/source', target, '0' * 64,
                         opener=lambda *a, **k: Response(b'archive', 'http://mirror/source'))
    assert not target.exists()


def test_download_retries_a_transient_mirror_failure(tmp_path):
    attempts = []
    content = b'source bytes'
    def flaky(*args, **kwargs):
        attempts.append(1)
        if len(attempts) == 1:
            raise URLError('temporary connection failure')
        return Response(content)
    target = tmp_path / 'source.tar.gz'
    sources.download('https://example.org/source', target,
                     hashlib.sha256(content).hexdigest(), opener=flaky)
    assert target.read_bytes() == content
    assert len(attempts) == 2


@pytest.mark.parametrize('url', [
    'file:///data/options.json', 'http://example.org/source',
    'https://user:password@example.org/source', 'https://example.org/source?token=x',
])
def test_credentials_and_non_https_urls_are_rejected(url):
    with pytest.raises(ValueError):
        sources.checked_url(url)


def metadata():
    return dict(info=dict(name='example-name', version='1.2.3'), urls=[dict(
        packagetype='sdist', filename='example_name-1.2.3.tar.gz',
        url='https://files.pythonhosted.org/example_name-1.2.3.tar.gz',
        digests={'sha256': 'a' * 64})])


def test_sdist_must_match_installed_package_and_version():
    assert sources.pypi_sdist(metadata(), 'example_name', '1.2.3')['version'] == '1.2.3'
    with pytest.raises(ValueError, match='different package or version'):
        sources.pypi_sdist(metadata(), 'example-name', '1.2.4')


def test_sdist_rejects_ambiguous_or_external_sources():
    data = metadata()
    data['urls'].append(dict(data['urls'][0]))
    with pytest.raises(ValueError, match='unambiguous'):
        sources.pypi_sdist(data, 'example-name', '1.2.3')
    data = metadata()
    data['urls'][0]['url'] = 'https://example.org/archive'
    with pytest.raises(ValueError, match='official PyPI'):
        sources.pypi_sdist(data, 'example-name', '1.2.3')


def test_recipe_parser_never_runs_recipe_code(tmp_path):
    marker = tmp_path / 'must-not-exist'
    recipe = (f"open({str(marker)!r}, 'w').write('executed')\n"
              "p = Package(name='ffmpeg', source_url='https://example.org/ffmpeg.tar.xz', "
              "sha256='" + 'b' * 64 + "', build_arguments=dangerous())\n")
    assert sources.recipe_sources(recipe)[0]['name'] == 'ffmpeg'
    assert not marker.exists()


def test_recipe_rejects_computed_source_metadata():
    recipe = "Package(name='x', source_url=get_secret(), sha256='" + 'c' * 64 + "')"
    with pytest.raises(ValueError):
        sources.recipe_sources(recipe)


def test_debian_http_recipe_is_upgraded_to_https():
    assert sources.checked_url('http://deb.debian.org/debian/source') == \
        'https://deb.debian.org/debian/source'


def test_installed_inventory_cannot_be_substituted(monkeypatch):
    monkeypatch.setattr(sources, 'installed_packages', lambda: (
        [dict(name='x', version='1', source='x', source_version='1')], []))
    with pytest.raises(ValueError, match='differ'):
        sources.verify_installed(dict(debian=[], python=[]))
