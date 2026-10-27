"""Test proof-mode selection without importing credentials or WebRTC runtime."""
import ast
from pathlib import Path

import pytest

source = Path(__file__).resolve().parents[1] / "bridge/eufy_stream.py"
tree = ast.parse(source.read_text())
function = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                and n.name == "select_signaling_identity")
namespace = {}
exec(compile(ast.Module(body=[function], type_ignores=[]), str(source), "exec"), namespace)
select_identity = namespace["select_signaling_identity"]


def test_empty_web_fallback_does_not_mutate_authenticated_session():
    auth = {"signalingUserId": "admin", "authToken": "private-token"}
    assert select_identity(auth, "passport", "web_empty") == ("", "web_empty")
    assert auth == {"signalingUserId": "admin", "authToken": "private-token"}


def test_default_admin_and_legacy_modes_preserve_identity():
    assert select_identity({"signalingUserId": "admin", "signalingIdentitySource": "nvr_admin"},
                           "passport", "nvr_admin") == ("admin", "nvr_admin")
    assert select_identity({}, "passport", "nvr_admin") == ("passport", "legacy_passport")


def test_invalid_mode_fails_closed():
    with pytest.raises(ValueError, match="signaling_identity"):
        select_identity({}, "passport", "unexpected")
