"""Execute setup helpers/flow methods with isolated HA framework boundaries."""
import ast
import asyncio
import importlib.util
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import voluptuous as vol

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components/eufy_nvr"


@pytest.fixture
def app(monkeypatch):
    package = ModuleType("setup_test_package")
    package.__path__ = [str(COMPONENT)]
    monkeypatch.setitem(sys.modules, package.__name__, package)
    for name in ("const", "go2rtc_api", "app_connection"):
        fullname = f"{package.__name__}.{name}"
        spec = importlib.util.spec_from_file_location(fullname, COMPONENT / f"{name}.py")
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, fullname, module)
        spec.loader.exec_module(module)
    return module


def info(**overrides):
    values = dict(slug="test_eufy_nvr", name="Eufy NVR", version="0.8.0-beta.1",
                  state="started", host_network=True, hostname="test-eufy-nvr", ingress=False,
                  options={"go2rtc_username": "example", "go2rtc_password": "not-a-real-password",
                           "rtsp_port": 8558, "password": "cloud-secret-must-not-be-copied"})
    values.update(overrides)
    return SimpleNamespace(**values)


def test_candidates_are_only_running_nvr_apps_without_credentials(app):
    result = app.candidate_apps([info(), info(slug="other_eufy_nvr"),
        info(slug="stopped_eufy_nvr", state="stopped"), info(slug="unrelated")])
    assert set(result) == {"test_eufy_nvr", "other_eufy_nvr"}
    assert "secret" not in json.dumps(result) and "password" not in json.dumps(result)


def test_connection_honours_port_and_copies_only_local_credentials(app):
    result = app.connection_from_info(info())
    assert result == {"host": "homeassistant.local", "api_port": 1985, "rtsp_port": 8558,
                      "username": "example", "password": "not-a-real-password"}
    assert app.connection_from_info(info(host_network=False))["host"] == "test-eufy-nvr"


@pytest.mark.parametrize("changes", [{"state": "stopped"}, {"slug": "unrelated"},
    {"options": {}}, {"options": {"go2rtc_username": "test", "go2rtc_password": "short"}},
    {"options": {"go2rtc_username": "test", "go2rtc_password": "not-a-real-password", "rtsp_port": 0}}])
def test_invalid_connection_fails_closed_without_secret_in_error(app, changes):
    with pytest.raises(ValueError) as failure:
        app.connection_from_info(info(**changes))
    assert "not-a-real-password" not in str(failure.value)


@pytest.mark.asyncio
async def test_endpoint_aliases_and_dns_failure(app, monkeypatch):
    resolver = AsyncMock(side_effect=[[(2, 1, 6, "", ("192.0.2.1", 1985))]] * 2)
    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", resolver)
    assert await app.same_endpoint("example.local", "192.0.2.1", 1985)
    resolver.side_effect = OSError("example resolution failure")
    assert not await app.same_endpoint("unreachable.local", "192.0.2.1", 1985)


def flow_harness(app, entries=()):
    tree = ast.parse((COMPONENT / "config_flow.py").read_text())
    original = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "EufyNvrConfigFlow")
    methods = [n for n in original.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
               and n.name in {"async_step_user", "async_step_app", "_endpoint_in_use",
                              "async_step_app_security", "async_step_app_finish"}]
    wrapper = ast.ClassDef(name="Flow", bases=[], keywords=[], body=methods, decorator_list=[])
    namespace = {"vol": vol, "Any": object, "ConfigFlowResult": dict,
        "CONF_HOST": "host", "CONF_API_PORT": "api_port", "CONF_USERNAME": "username",
        "CONF_PASSWORD": "password", "normalize_host": lambda x: x,
        "validate_port": lambda x: x, "installed_apps": AsyncMock(return_value=app.candidate_apps([info()])),
        "installed_connection": AsyncMock(return_value=app.connection_from_info(info())),
        "_validate_go2rtc": AsyncMock(return_value=("homeassistant.local", 9)),
        "same_endpoint": AsyncMock(return_value=False)}
    namespace.update(LocalVideoAlreadyConfigured=app.LocalVideoAlreadyConfigured,
                     LocalVideoSetupRequired=app.LocalVideoSetupRequired,
                     create_installed_local_video=AsyncMock(return_value=True))
    exec(compile(ast.fix_missing_locations(ast.Module(body=[wrapper], type_ignores=[])), "flow", "exec"), namespace)
    flow = namespace["Flow"]()
    flow.hass = object()
    flow._async_current_entries = lambda: entries
    flow.async_show_form = lambda **kw: {"type": "form", **kw}
    flow.async_show_menu = lambda **kw: {"type": "menu", **kw}
    flow.async_abort = lambda **kw: {"type": "abort", **kw}
    flow.async_create_entry = lambda **kw: {"type": "create", **kw}
    flow.async_set_unique_id = AsyncMock()
    flow._abort_if_unique_id_configured = lambda: None
    return flow, namespace


@pytest.mark.asyncio
async def test_automatic_flow_creates_from_server_side_credentials(app):
    flow, ns = flow_harness(app)
    assert (await flow.async_step_user())["type"] == "create"
    form = await flow.async_step_app()
    assert list(map(str, form["data_schema"].schema)) == ["app"]
    result = await flow.async_step_app({"app": "test_eufy_nvr"})
    assert result["type"] == "create" and result["data"]["rtsp_port"] == 8558
    assert result["data"]["app_slug"] == "test_eufy_nvr"
    assert "cloud-secret" not in repr(result)


@pytest.mark.asyncio
async def test_flow_refuses_alias_duplicate_and_masks_supervisor_errors(app):
    flow, ns = flow_harness(app, [SimpleNamespace(entry_id="old", data={"host": "192.0.2.1", "api_port": 1985})])
    ns["same_endpoint"].return_value = True
    assert await flow.async_step_app({"app": "test_eufy_nvr"}) == {"type": "abort", "reason": "already_configured"}
    ns["installed_connection"].side_effect = RuntimeError("SECRET")
    result = await flow.async_step_app({"app": "test_eufy_nvr"})
    assert result["errors"] == {"base": "app_not_ready"}
    assert "SECRET" not in repr(result)
    ns["installed_apps"].return_value = {}
    assert (await flow.async_step_app())["errors"] == {"base": "app_unavailable"}


def test_password_fields_have_no_saved_value_defaults():
    tree = ast.parse((COMPONENT / "config_flow.py").read_text())
    fields = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
              and isinstance(n.func, ast.Attribute) and n.func.attr == "Required"
              and n.args and isinstance(n.args[0], ast.Name) and n.args[0].id == "CONF_PASSWORD"]
    assert len(fields) == 2
    assert all(not field.keywords for field in fields)
    assert (COMPONENT / "strings.json").read_bytes() == (COMPONENT / "translations/en.json").read_bytes()

def fresh_info(**overrides):
    values = dict(ingress=True, options={
        "setup_mode": "guided", "go2rtc_username": "eufy", "go2rtc_password": "",
        "rtsp_port": 8558, "shared_grid": True, "grid_lens": "both",
    })
    values.update(overrides)
    return info(**values)


def test_only_blank_guided_password_offers_creation(app):
    assert app.needs_local_video_setup(fresh_info())
    with pytest.raises(app.LocalVideoSetupRequired):
        app.connection_from_info(fresh_info())
    assert not app.needs_local_video_setup(info())
    assert not app.needs_local_video_setup(fresh_info(ingress=False))
    for value in ["already-set-password", "short", "!secret stream_password", None, False]:
        options = {**fresh_info().options, "go2rtc_password": value}
        assert not app.needs_local_video_setup(fresh_info(options=options))
    for key in ("email", "password", "browser_session_url"):
        assert not app.needs_local_video_setup(fresh_info(options={**fresh_info().options, key: "existing"}))


@pytest.mark.asyncio
async def test_first_time_password_is_random_preserves_settings_and_restarts_one_app(app):
    current = fresh_info()
    initial = dict(current.options)
    client = SimpleNamespace(addon_info=AsyncMock(side_effect=lambda slug: current),
                             restart_addon=AsyncMock())
    async def save(slug, body):
        assert slug == current.slug
        assert vars(body).keys() == {"config"}  # No boot/security/network changes.
        assert current.options == initial
        current.options = body.config
    client.set_addon_options = AsyncMock(side_effect=save)
    assert await app.provision_local_video(client, current.slug, SimpleNamespace)
    new = current.options["go2rtc_password"]
    assert len(new) >= 40 and new != initial["go2rtc_password"]
    assert {k: v for k, v in current.options.items() if k != "go2rtc_password"} == {
        k: v for k, v in initial.items() if k != "go2rtc_password"}
    client.set_addon_options.assert_awaited_once()
    client.restart_addon.assert_awaited_once_with(current.slug)
    # An uncertain previous reply/repeated click never rotates or restarts again.
    assert await app.provision_local_video(client, current.slug, SimpleNamespace) is False
    assert current.options["go2rtc_password"] == new
    assert client.set_addon_options.await_count == client.restart_addon.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("existing", ["already-configured-password", "short", "!secret existing"])
async def test_nonempty_password_is_never_overwritten_even_if_invalid(app, existing):
    current = fresh_info(options={**fresh_info().options, "go2rtc_password": existing})
    client = SimpleNamespace(addon_info=AsyncMock(return_value=current),
                             set_addon_options=AsyncMock(), restart_addon=AsyncMock())
    try:
        await app.provision_local_video(client, current.slug, SimpleNamespace)
    except app.LocalVideoSetupError:
        pass
    client.set_addon_options.assert_not_awaited()
    client.restart_addon.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["options", "slug", "state"])
async def test_changed_settings_or_identity_blocks_write(app, change):
    first = fresh_info()
    second = fresh_info()
    if change == "options":
        second.options["rtsp_port"] = 8559
    elif change == "slug":
        second.slug = "other_eufy_nvr"
    else:
        second.state = "stopped"
    client = SimpleNamespace(addon_info=AsyncMock(side_effect=[first, second]),
                             set_addon_options=AsyncMock(), restart_addon=AsyncMock())
    with pytest.raises(app.LocalVideoSetupError):
        await app.provision_local_video(client, first.slug, SimpleNamespace)
    client.set_addon_options.assert_not_awaited()
    client.restart_addon.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["write", "readback", "restart"])
async def test_uncertain_outcome_is_not_retried_or_logged(app, failure, capsys):
    current = fresh_info()
    client = SimpleNamespace(addon_info=AsyncMock(side_effect=lambda slug: current),
                             restart_addon=AsyncMock())
    async def save(slug, body):
        current.options = body.config
        if failure == "write":
            raise RuntimeError("private credential reply")
        if failure == "readback":
            current.options = {**body.config, "rtsp_port": 8559}
    client.set_addon_options = AsyncMock(side_effect=save)
    if failure == "restart":
        client.restart_addon.side_effect = RuntimeError("private restart details")
    with pytest.raises(app.LocalVideoSetupError) as caught:
        await app.provision_local_video(client, current.slug, SimpleNamespace)
    assert str(caught.value) == "Local video setup could not be completed"
    assert client.set_addon_options.await_count == 1
    assert client.restart_addon.await_count == (1 if failure == "restart" else 0)
    assert current.options["go2rtc_password"]  # Do not roll back/delete a possibly saved password.
    assert capsys.readouterr() == ("", "")


@pytest.mark.asyncio
async def test_flow_asks_before_any_credential_write_or_restart(app):
    flow, ns = flow_harness(app)
    ns["installed_connection"].side_effect = app.LocalVideoSetupRequired()
    result = await flow.async_step_app({"app": "test_eufy_nvr"})
    assert result["step_id"] == "app_security"
    ns["create_installed_local_video"].assert_not_awaited()
    result = await flow.async_step_app_security({"confirm": False})
    assert result["errors"]["base"] == "local_video_confirmation_required"
    ns["create_installed_local_video"].assert_not_awaited()
    result = await flow.async_step_app_security({"confirm": True})
    assert result["step_id"] == "app_finish"
    ns["create_installed_local_video"].assert_awaited_once_with(flow.hass, "test_eufy_nvr")
    ns["_validate_go2rtc"].assert_not_awaited()


@pytest.mark.asyncio
async def test_flow_masks_creation_error_and_can_finish_after_signin(app):
    flow, ns = flow_harness(app)
    flow._local_setup_slug = "test_eufy_nvr"
    ns["create_installed_local_video"].side_effect = RuntimeError("private")
    result = await flow.async_step_app_security({"confirm": True})
    assert result["errors"]["base"] == "local_video_setup_uncertain"
    assert "private" not in repr(result)
    result = await flow.async_step_app_finish({})
    assert result["type"] == "create"

@pytest.fixture
def supervisor_environment(app, monkeypatch):
    client = SimpleNamespace(addon_info=AsyncMock(return_value=fresh_info()),
                             set_addon_options=AsyncMock(), restart_addon=AsyncMock())
    for name in ("homeassistant", "homeassistant.components", "aiohasupervisor", "aiohasupervisor.models"):
        module = ModuleType(name)
        module.__path__ = []
        monkeypatch.setitem(sys.modules, name, module)
    ha = ModuleType("homeassistant.components.hassio")
    ha.get_supervisor_client = lambda hass: SimpleNamespace(addons=client)
    models = ModuleType("aiohasupervisor.models.addons")
    models.AddonsOptions = SimpleNamespace
    monkeypatch.setitem(sys.modules, ha.__name__, ha)
    monkeypatch.setitem(sys.modules, models.__name__, models)
    hass = SimpleNamespace(data={}, config_entries=SimpleNamespace(async_entries=lambda domain: []))
    return hass, client


@pytest.mark.asyncio
async def test_same_app_integration_blocks_first_time_write(app, supervisor_environment):
    hass, client = supervisor_environment
    hass.config_entries.async_entries = lambda domain: [SimpleNamespace(
        data={"app_slug": "test_eufy_nvr"})]
    with pytest.raises(app.LocalVideoAlreadyConfigured):
        await app.create_installed_local_video(hass, "test_eufy_nvr")
    client.addon_info.assert_not_awaited()
    client.set_addon_options.assert_not_awaited()
    client.restart_addon.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("old_slug", ["old_eufy_nvr", None])
async def test_other_app_endpoint_does_not_block_new_app_preparation(
        app, supervisor_environment, old_slug):
    hass, client = supervisor_environment
    old_data = {"host": "homeassistant.local", "api_port": 1985,
                "rtsp_port": 8558, "username": "old-user", "password": "old-password"}
    if old_slug is not None:
        old_data["app_slug"] = old_slug
    previous = dict(old_data)
    hass.config_entries.async_entries = lambda domain: [SimpleNamespace(data=old_data)]
    current = fresh_info()
    client.addon_info.side_effect = lambda slug: current
    async def save(slug, body):
        assert slug == current.slug
        current.options = body.config
    client.set_addon_options.side_effect = save
    assert await app.create_installed_local_video(hass, current.slug)
    assert old_data == previous
    assert len(current.options["go2rtc_password"]) >= 40
    assert all(call.args == (current.slug,) for call in client.addon_info.await_args_list)
    client.set_addon_options.assert_awaited_once()
    client.restart_addon.assert_awaited_once_with(current.slug)


@pytest.mark.asyncio
async def test_same_app_setup_actions_are_serialized(app, supervisor_environment, monkeypatch):
    hass, client = supervisor_environment
    inside = 0
    peak = 0
    async def provision(*args):
        nonlocal inside, peak
        inside += 1
        peak = max(inside, peak)
        await asyncio.sleep(0)
        inside -= 1
        return True
    monkeypatch.setattr(app, "provision_local_video", provision)
    await asyncio.gather(app.create_installed_local_video(hass, "test_eufy_nvr"),
                         app.create_installed_local_video(hass, "test_eufy_nvr"))
    assert peak == 1


@pytest.mark.asyncio
async def test_unrecognised_app_cannot_trigger_supervisor_writes(app, supervisor_environment):
    hass, client = supervisor_environment
    for slug in ("other_app", "../eufy_nvr", None):
        with pytest.raises(app.LocalVideoSetupError):
            await app.create_installed_local_video(hass, slug)
    client.addon_info.assert_not_awaited()
    client.set_addon_options.assert_not_awaited()
    client.restart_addon.assert_not_awaited()


@pytest.mark.asyncio
async def test_flow_reports_same_app_block_without_uncertain_write_message(app):
    flow, ns = flow_harness(app)
    flow._local_setup_slug = "test_eufy_nvr"
    ns["LocalVideoAlreadyConfigured"] = app.LocalVideoAlreadyConfigured
    ns["create_installed_local_video"].side_effect = app.LocalVideoAlreadyConfigured()
    assert await flow.async_step_app_security({"confirm": True}) == {
        "type": "abort", "reason": "already_configured"}
    ns["_validate_go2rtc"].assert_not_awaited()


@pytest.mark.asyncio
async def test_failed_new_app_selection_is_not_reset_to_old_app(app):
    flow, ns = flow_harness(app)
    ns["installed_apps"].return_value = {"old_eufy_nvr": "Old", "new_eufy_nvr": "New"}
    ns["installed_connection"].side_effect = RuntimeError("private failure")
    result = await flow.async_step_app({"app": "new_eufy_nvr"})
    assert result["errors"] == {"base": "app_not_ready"}
    assert result["data_schema"]({}) == {"app": "new_eufy_nvr"}


@pytest.mark.asyncio
async def test_single_app_bypasses_picker_but_still_requires_secret_consent(app):
    flow, ns = flow_harness(app)
    ns["installed_connection"].side_effect = app.LocalVideoSetupRequired()
    result = await flow.async_step_user()
    assert result["step_id"] == "app_security"
    ns["create_installed_local_video"].assert_not_awaited()


@pytest.mark.asyncio
async def test_multiple_apps_remain_a_deliberate_choice(app):
    flow, ns = flow_harness(app)
    ns["installed_apps"].return_value = {"old_eufy_nvr": "Old", "new_eufy_nvr": "New"}
    assert (await flow.async_step_user())["menu_options"] == ["app", "manual"]
    ns["installed_connection"].assert_not_awaited()
