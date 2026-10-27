"""Offline regression tests for mailbox verification and account-bound auth caches."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[1]

sys.path.insert(0, str(ROOT / "bridge"))
# Share the canonical module with setup/renewal tests. Replacing sys.modules
# here leaves earlier importers holding an unpatched, live HTTP client.
import eufy_cloud as cloud

AUTH_SPEC = importlib.util.spec_from_file_location(
    "auth_login_hardening", ROOT / "bridge/auth_login.py"
)
auth_login = importlib.util.module_from_spec(AUTH_SPEC)
assert AUTH_SPEC.loader is not None
AUTH_SPEC.loader.exec_module(auth_login)


def challenge() -> dict:
    return {
        "code": 0,
        "msg": "ok",
        "data": {
            "auth_token": "provisional-token",
            "fa_info": {"step": 26052},
            "server_secret_info": {"public_key": "server-public-key"},
        },
    }


@pytest.mark.asyncio
async def test_challenge_requests_code_through_official_push_endpoint():
    post = AsyncMock(
        side_effect=[challenge(), {"code": 0, "msg": "sent", "data": {}}]
    )
    with patch.object(cloud, "encrypted_post", post):
        with pytest.raises(cloud.EufyVerificationRequired, match="email verification"):
            await cloud.login(
                "owner@example.com", "correct horse battery staple",
                key_obj=object(),
            )

    send_call = post.await_args_list[1]
    assert send_call.args[0] == "/app/sendmsg/verify_code"
    assert send_call.args[1] == {
        "biz_type": 1004,
        "message_type": 2,
        "client_secret_info": {"public_key": "server-public-key"},
    }
    assert send_call.args[2] == "provisional-token"
    assert send_call.kwargs["service"] == "push"
    assert send_call.kwargs["extra_headers"]["X-Auth-Token"] == "provisional-token"


@pytest.mark.asyncio
async def test_six_digit_code_repeats_login_with_provisional_token():
    post = AsyncMock(
        side_effect=[
            challenge(),
            {
                "code": 0,
                "msg": "ok",
                "data": {"auth_token": "final-token", "user_id": "owner-id"},
            },
        ]
    )
    with patch.object(cloud, "encrypted_post", post):
        result = await cloud.login(
            "owner@example.com", "correct horse battery staple",
            verification_code="123456", key_obj=object(),
        )

    verify_call = post.await_args_list[1]
    assert verify_call.args[0] == "/passport/login"
    assert verify_call.args[1]["verify_code"] == "123456"
    assert verify_call.args[2] == "provisional-token"
    assert result["auth_token"] == "final-token"


@pytest.mark.asyncio
async def test_malformed_verification_code_is_rejected_locally():
    with patch.object(cloud, "encrypted_post", AsyncMock(return_value=challenge())):
        with pytest.raises(cloud.EufyCloudError, match="exactly six digits"):
            await cloud.login(
                "owner@example.com", "correct horse battery staple",
                verification_code="12ab", key_obj=object(),
            )


def test_cache_is_bound_to_account_region_and_country(tmp_path: Path):
    email, region, country = "Owner@Example.com", "us-pr", "AU"
    fingerprint = auth_login.account_fingerprint(email, region, country)
    cache = tmp_path / "auth.json"
    cache.write_text(
        json.dumps(
            {
                "cacheVersion": auth_login._CACHE_VERSION,
                "accountFingerprint": fingerprint,
                "authToken": "secret",
                "stationSn": "T8N000000000",
                "signalingUserId": "nvr-admin-id",
                "signalingIdentitySource": "nvr_admin",
            }
        ),
        encoding="utf-8",
    )

    assert auth_login.cache_matches(str(cache), email.lower(), region, country)
    assert not auth_login.cache_matches(str(cache), "other@example.com", region, country)
    assert not auth_login.cache_matches(str(cache), email, "eu-pr", country)
    assert not auth_login.cache_matches(str(cache), email, region, "US")
    assert "owner@example.com" not in fingerprint


def test_legacy_or_incomplete_cache_is_never_used_as_fallback(tmp_path: Path):
    cache = tmp_path / "auth.json"
    cache.write_text(
        json.dumps({"authToken": "old-token", "stationSn": "T8N000000000"}),
        encoding="utf-8",
    )
    assert not auth_login.cache_matches(
        str(cache), "owner@example.com", "us-pr", "US"
    )


@pytest.mark.asyncio
async def test_live_v1_cache_is_enriched_without_a_new_login(tmp_path: Path):
    email, region, country = "owner@example.com", "us-pr", "AU"
    cache = tmp_path / "auth.json"
    cache.write_text(
        json.dumps({
            "cacheVersion": 1,
            "accountFingerprint": auth_login.account_fingerprint(
                email, region, country
            ),
            "authToken": "private-token",
            "gtoken": "private-gtoken",
            "userId": "passport-id",
            "stationSn": "T8N000000000",
        }),
        encoding="utf-8",
    )
    class FakeResponse:
        status = 200

        async def json(self, **_kwargs):
            return {"code": 0, "data": "sign"}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

    class FakeSession:
        def get(self, *_args, **_kwargs):
            return FakeResponse()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None
    station_raw = {
        "data": [{
            "station_sn": "T8N000000000",
            "admin_user_id": "nvr-admin-id",
        }]
    }

    with (
        patch.object(auth_login.aiohttp, "ClientSession", return_value=FakeSession()),
        patch.object(
            auth_login.ec, "station_list", AsyncMock(return_value=station_raw)
        ),
        patch.object(auth_login.ec, "login", AsyncMock()) as login,
    ):
        assert await auth_login.cache_is_live(
            str(cache), email, region, country
        )

    login.assert_not_awaited()
    upgraded = json.loads(cache.read_text(encoding="utf-8"))
    assert upgraded["cacheVersion"] == auth_login._CACHE_VERSION
    assert upgraded["signalingUserId"] == "nvr-admin-id"
    assert upgraded["signalingIdentitySource"] == "nvr_admin"


@pytest.mark.asyncio
async def test_live_v1_cache_metadata_failure_does_not_force_login(tmp_path: Path):
    email, region, country = "owner@example.com", "us-pr", "AU"
    cache = tmp_path / "auth.json"
    original = {
        "cacheVersion": 1,
        "accountFingerprint": auth_login.account_fingerprint(
            email, region, country
        ),
        "authToken": "private-token",
        "gtoken": "private-gtoken",
        "userId": "passport-id",
        "stationSn": "T8N000000000",
    }
    cache.write_text(json.dumps(original), encoding="utf-8")

    class FakeResponse:
        status = 200

        async def json(self, **_kwargs):
            return {"code": 0, "data": "sign"}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

    class FakeSession:
        def get(self, *_args, **_kwargs):
            return FakeResponse()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

    with (
        patch.object(auth_login.aiohttp, "ClientSession", return_value=FakeSession()),
        patch.object(
            auth_login.ec, "station_list", AsyncMock(side_effect=RuntimeError())
        ),
        patch.object(auth_login.ec, "login", AsyncMock()) as login,
    ):
        assert await auth_login.cache_is_live(
            str(cache), email, region, country
        )

    login.assert_not_awaited()
    assert json.loads(cache.read_text(encoding="utf-8")) == original


def test_selected_station_admin_identity_is_found_without_exposing_it():
    raw = {
        "data": [
            {"station_sn": "OTHER", "admin_user_id": "wrong-id"},
            {
                "station_sn": "T8N000000000",
                "permissions": {"adminUserId": "nvr-admin-id"},
            },
        ]
    }
    assert auth_login._find_admin_user_id(raw, "T8N000000000") == "nvr-admin-id"


def test_admin_identity_fallback_requires_one_unique_value():
    assert auth_login._find_admin_user_id(
        {"data": [{"admin_user_id": "only-id"}]}, "T8N000000000"
    ) == "only-id"
    assert auth_login._find_admin_user_id(
        {"data": [{"admin_user_id": "one"}, {"admin_user_id": "two"}]},
        "T8N000000000",
    ) == ""


def test_identity_comparison_uses_private_fixed_length_digests():
    assert auth_login.signaling_identities_match("same", "same")
    assert not auth_login.signaling_identities_match("passport", "administrator")
    assert not auth_login.signaling_identities_match("", "administrator")


@pytest.mark.asyncio
async def test_login_caches_nvr_admin_identity_without_logging_ids(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    output = tmp_path / "auth.json"
    station_raw = {
        "data": [{
            "station_sn": "T8N000000000",
            "admin_user_id": "private-admin-id",
        }]
    }
    environment = {
        "EUFY_EMAIL": "owner@example.com",
        "EUFY_PASSWORD": "not-logged",
        "EUFY_COUNTRY": "AU",
        "EUFY_REGION": "US",
        "EUFY_AUTH": str(output),
    }
    credentials = {
        "auth_token": "private-token",
        "gtoken": "private-gtoken",
        "user_id": "private-passport-id",
    }

    with (
        patch.dict(auth_login.os.environ, environment, clear=True),
        patch.object(auth_login.sys, "argv", ["auth_login.py"]),
        patch.object(auth_login.ec, "login", AsyncMock(return_value=credentials)),
        patch.object(
            auth_login.ec, "station_list", AsyncMock(return_value=station_raw)
        ),
    ):
        assert await auth_login.main() == 0

    cached = json.loads(output.read_text(encoding="utf-8"))
    assert cached["userId"] == "private-passport-id"
    assert cached["signalingUserId"] == "private-admin-id"
    assert cached["signalingIdentitySource"] == "nvr_admin"
    captured = capsys.readouterr()
    rendered = captured.out + captured.err
    for secret in credentials.values():
        assert secret not in rendered
    assert "private-admin-id" not in rendered


def test_ws_sign_response_must_prove_cached_session_is_live():
    assert auth_login.sign_response_is_live(200, {"code": 0, "data": "sign"})
    assert not auth_login.sign_response_is_live(401, {"code": 26084})
    assert not auth_login.sign_response_is_live(200, {"code": 0, "data": ""})
    assert not auth_login.sign_response_is_live(200, {"code": 1, "data": "sign"})


def test_addon_validates_cache_before_login_or_starting_producers():
    run_script = (ROOT / "eufy_nvr/run.sh").read_text()

    assert 'auth_login.py --check-cache-live "${EUFY_AUTH}"' in run_script
    assert "skipped a redundant passport login" in run_script
    assert "no live account-bound cache exists" in run_script
    assert "using the cache bound" not in run_script


@pytest.mark.asyncio
@pytest.mark.parametrize("code,answer", [(10019, ""), (100056, "wrong-answer")])
async def test_captcha_challenge_is_structured_without_writing_or_echoing(code, answer):
    with (
        patch.object(cloud, "encrypted_post", AsyncMock(return_value={"code": code})),
        patch.object(cloud, "get_captcha", AsyncMock(return_value={
            "captcha_id": "private-challenge", "item": "image-payload",
        })),
        patch.object(cloud, "_save_captcha_image") as save,
    ):
        with pytest.raises(cloud.EufyCaptchaRequired) as caught:
            await cloud.login("owner@example.com", "password", key_obj=object(),
                              answer=answer, save_captcha=False)
    assert caught.value.captcha_id == "private-challenge"
    assert caught.value.image == "image-payload"
    assert "private-challenge" not in str(caught.value)
    assert "image-payload" not in str(caught.value)
    save.assert_not_called()


@pytest.mark.asyncio
async def test_captcha_fetch_failure_does_not_echo_vendor_error():
    with (
        patch.object(cloud, "encrypted_post", AsyncMock(return_value={"code": 10019})),
        patch.object(cloud, "get_captcha", AsyncMock(side_effect=ValueError("secret-token"))),
    ):
        with pytest.raises(cloud.EufyCaptchaRequired) as caught:
            await cloud.login("owner@example.com", "password", key_obj=object(),
                              save_captcha=False)
    assert "secret-token" not in str(caught.value)
    assert caught.value.captcha_id == ""


@pytest.mark.asyncio
@pytest.mark.parametrize("code", [100028, 100041])
async def test_vendor_login_limit_has_distinct_exception(code):
    with patch.object(cloud, "encrypted_post", AsyncMock(return_value={"code": code})):
        with pytest.raises(cloud.EufyLoginRateLimited):
            await cloud.login("owner@example.com", "password", key_obj=object())


@pytest.mark.asyncio
async def test_vendor_rejection_does_not_echo_credentials():
    with patch.object(cloud, "encrypted_post", AsyncMock(return_value={
        "code": 1000, "msg": "secret-token", "data": {"private-key": "secret-value"},
    })):
        with pytest.raises(cloud.EufyLoginRejected) as caught:
            await cloud.login("owner@example.com", "password", key_obj=object())
    assert str(caught.value) == "login(): credentials or verification were rejected"
