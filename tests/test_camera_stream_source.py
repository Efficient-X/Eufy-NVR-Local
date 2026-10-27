"""Keep HA's shared camera URL valid for PyAV as well as go2rtc."""

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.mark.asyncio
async def test_stream_source_returns_unmodified_rtsp_url():
    path = Path(__file__).resolve().parents[1] / 'custom_components/eufy_nvr/camera.py'
    module = ast.parse(path.read_text(encoding='utf-8'))
    camera = next(node for node in module.body
                  if isinstance(node, ast.ClassDef) and node.name == 'EufyNvrCamera')
    method = next(node for node in camera.body
                  if isinstance(node, ast.AsyncFunctionDef) and node.name == 'stream_source')
    namespace = {}
    exec(compile(ast.Module(body=[method], type_ignores=[]), str(path), 'exec'), namespace)
    source = 'rtsp://example.invalid:8558/eufy_shared_grid'
    assert await namespace['stream_source'](SimpleNamespace(_stream_source=source)) == source
