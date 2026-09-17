"""Offline profile routing for the portal; all files and credentials are fixtures."""
import base64
import importlib.util
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import helpers

PORTAL = ROOT / "portal" / "backend" / "main.py"
SCOPES = [
    ("https://api.fabric.microsoft.com/.default", "get_fabric_token"),
    ("https://analysis.windows.net/powerbi/api/.default", "get_powerbi_token"),
]


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


@pytest.fixture
def context(tmp_path, monkeypatch):
    src = tmp_path / "src"
    legacy_config = _write(src / "config.yaml", {
        "workspace_name": "Legacy workspace",
        "volumes": {"customers": 17, "campaigns": 2, "segments": 1},
    })
    legacy_state = _write(src / "state.json", {
        "workspace_id": "legacy-workspace", "report_id": "legacy-report",
        "semantic_model_id": "legacy-model", "data_agent_id": "legacy-agent",
    })
    profile = tmp_path / "deployments" / "next"
    config = {
        "tenant_id": "fixture-tenant", "az_subscription": "fixture-subscription",
        "workspace_name": "Selected workspace",
        "deployment": {"expected_account": "operator@example.invalid"},
        "semantic_model_name": "Selected model", "report_name": "Selected report",
        "storyline": {"culprit_campaign_name": "Fixture campaign"},
        "volumes": {"customers": 120, "campaigns": 7, "segments": 3},
        "foundry": {"supervisor": {"agent_name": "Selected-Supervisor"}},
    }
    state = {
        "_deployment_context": {
            "tenant_id": config["tenant_id"], "subscription_id": config["az_subscription"],
            "account": config["deployment"]["expected_account"],
            "workspace_name": config["workspace_name"],
        },
        "workspace_id": "selected-workspace", "report_id": "selected-report",
        "semantic_model_id": "selected-model", "data_agent_id": "selected-agent",
        "ontology_id": "selected-ontology",
        "foundry_supervisor_agent_name": "Selected-Supervisor",
    }
    _write(profile / "config.yaml", config)
    _write(profile / "state.json", state)
    monkeypatch.setattr(helpers, "ROOT", tmp_path)
    monkeypatch.setattr(helpers, "SCRIPT_DIR", src)
    monkeypatch.setattr(helpers, "CONFIG_FILE", legacy_config)
    monkeypatch.setattr(helpers, "STATE_FILE", legacy_state)
    monkeypatch.setattr(helpers, "ACTIVE_PROFILE_FILE", tmp_path / "deployments" / "active-profile.json")
    monkeypatch.setenv(helpers.PROFILE_ENV, str(profile))
    for name in ("WORKSPACE_ID", "REPORT_ID", "DATASET_ID", "DATA_AGENT_ID", "ONTOLOGY_ID"):
        monkeypatch.delenv(name, raising=False)

    no_cloud = Mock(side_effect=AssertionError("No cloud or auth calls in portal profile tests"))
    for name in ("get_fabric_token", "get_powerbi_token", "ensure_tenant"):
        monkeypatch.setattr(helpers, name, no_cloud)
    import azure.identity
    monkeypatch.setattr(azure.identity, "AzureCliCredential",
                        lambda **_: SimpleNamespace(get_token=no_cloud))
    return SimpleNamespace(profile=profile, config=config, state=state, no_cloud=no_cloud,
                           legacy_config=legacy_config, legacy_state=legacy_state,
                           root=tmp_path, monkeypatch=monkeypatch)


def _portal(context):
    context.monkeypatch.setattr(sys, "path", sys.path.copy())
    spec = importlib.util.spec_from_file_location("portal_profile_fixture", PORTAL)
    module = importlib.util.module_from_spec(spec)
    context.monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    return module


def _token(**claims):
    body = {"tid": "fixture-tenant", "exp": int(time.time()) + 3600, **claims}
    payload = base64.urlsafe_b64encode(json.dumps(body).encode()).decode().rstrip("=")
    return f"header.{payload}.signature"


def test_selected_portal_uses_shared_config_and_state_without_auth(context):
    before = context.legacy_config.read_bytes(), context.legacy_state.read_bytes()
    portal = _portal(context)
    assert portal.WORKSPACE_ID == "selected-workspace"
    assert portal.REPORT_ID == "selected-report"
    assert portal.DATASET_ID == "selected-model"
    assert portal.DATA_AGENT_ID == "selected-agent"
    assert portal.ONTOLOGY_ID == "selected-ontology"
    assert portal.WORKSPACE_NAME == "Selected workspace"
    assert portal.SM_NAME == "Selected model"
    assert portal.CULPRIT == "Fixture campaign"
    assert portal.DEMO_COUNTS == {"customers": 120, "campaigns": 7, "segments": 3}
    assert portal._config_dict()["foundry"]["supervisor"]["agent_name"] == "Selected-Supervisor"
    assert portal._state() == context.state
    context.no_cloud.assert_not_called()
    assert (context.legacy_config.read_bytes(), context.legacy_state.read_bytes()) == before


def test_portal_pointer_selection_uses_the_same_helpers(context, monkeypatch):
    monkeypatch.delenv(helpers.PROFILE_ENV)
    _write(helpers.ACTIVE_PROFILE_FILE, {"profile": "next"})
    assert _portal(context).WORKSPACE_ID == "selected-workspace"


def test_no_profile_preserves_legacy_paths_and_env_overrides(context, monkeypatch):
    monkeypatch.delenv(helpers.PROFILE_ENV)
    monkeypatch.setenv("REPORT_ID", "explicit-legacy-report")
    portal = _portal(context)
    assert portal.WORKSPACE_ID == "legacy-workspace"
    assert portal.REPORT_ID == "explicit-legacy-report"
    assert portal.WORKSPACE_NAME == "Legacy workspace"
    assert portal.DEMO_COUNTS["customers"] == 17
    context.no_cloud.assert_not_called()


def test_missing_legacy_config_keeps_default_names(context, monkeypatch):
    monkeypatch.delenv(helpers.PROFILE_ENV)
    context.legacy_config.unlink()
    portal = _portal(context)
    assert portal.WORKSPACE_NAME == "Customer 360 Marketing"
    assert portal.DEMO_COUNTS["customers"] == 0


def test_selected_missing_config_cannot_fall_back_to_legacy(context):
    (context.profile / "config.yaml").unlink()
    with pytest.raises(RuntimeError, match="config.yaml"):
        _portal(context)
    context.no_cloud.assert_not_called()


def test_selected_invalid_state_is_not_swallowed(context):
    (context.profile / "state.json").write_text("not json", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        _portal(context)


def test_selected_unstamped_state_is_rejected(context):
    _write(context.profile / "state.json", {"workspace_id": "legacy-workspace"})
    with pytest.raises(RuntimeError, match="does not belong"):
        _portal(context)


def test_selected_missing_state_shows_missing_ids_not_legacy_ids(context):
    (context.profile / "state.json").unlink()
    portal = _portal(context)
    assert portal.WORKSPACE_ID == ""
    assert portal.REPORT_ID == ""


@pytest.mark.parametrize("variable", [
    "WORKSPACE_ID", "REPORT_ID", "DATASET_ID", "DATA_AGENT_ID", "ONTOLOGY_ID",
])
def test_profile_rejects_inherited_old_item_overrides(context, monkeypatch, variable):
    monkeypatch.setenv(variable, "legacy-item")
    with pytest.raises(RuntimeError, match=variable):
        _portal(context)
    context.no_cloud.assert_not_called()


def test_matching_item_override_is_allowed(context, monkeypatch):
    monkeypatch.setenv("WORKSPACE_ID", "selected-workspace")
    assert _portal(context).WORKSPACE_ID == "selected-workspace"


@pytest.mark.parametrize("scope,getter_name", SCOPES)
def test_profile_tokens_use_checked_helpers_and_remain_cached(context, monkeypatch, scope, getter_name):
    portal = _portal(context)
    token = _token()
    getter = Mock(return_value=token)
    monkeypatch.setattr(helpers, getter_name, getter)
    assert portal._cached_token(scope) == token
    assert portal._cached_token(scope) == token
    getter.assert_called_once_with()
    context.no_cloud.assert_not_called()


def test_profile_token_error_cannot_fall_back_to_a_previous_cached_token(context, monkeypatch):
    portal = _portal(context)
    scope = SCOPES[0][0]
    portal._token_cache[scope] = ("previous", time.time() + 60)
    monkeypatch.setattr(helpers, "get_fabric_token", Mock(side_effect=RuntimeError("Wrong identity")))
    with pytest.raises(RuntimeError, match="Wrong identity"):
        portal._cached_token(scope, force=True)


def test_profile_token_without_expiry_is_not_cached_as_success(context, monkeypatch):
    portal = _portal(context)
    monkeypatch.setattr(helpers, "get_fabric_token", Mock(return_value=_token(exp=None)))
    with pytest.raises(RuntimeError, match="expiry"):
        portal.fabric_token()
    assert not portal._token_cache


def test_no_profile_retains_azure_cli_credential_cache(context, monkeypatch):
    monkeypatch.delenv(helpers.PROFILE_ENV)
    portal = _portal(context)
    getter = Mock(return_value=SimpleNamespace(token="legacy-token", expires_on=time.time() + 3600))
    portal.credential = SimpleNamespace(get_token=getter)
    assert portal.fabric_token() == "legacy-token"
    assert portal.fabric_token() == "legacy-token"
    getter.assert_called_once_with(SCOPES[0][0])
    context.no_cloud.assert_not_called()


def test_changing_profile_requires_restart_before_using_cached_ids_or_tokens(context, monkeypatch):
    portal = _portal(context)
    portal._token_cache[SCOPES[0][0]] = ("old-token", time.time() + 3600)
    monkeypatch.delenv(helpers.PROFILE_ENV)
    for call in (portal._state, portal._config_dict, portal.fabric_token):
        with pytest.raises(RuntimeError, match="restart the portal"):
            call()
    context.no_cloud.assert_not_called()


def test_launcher_uses_shared_profile_readers_instead_of_a_fixed_state_path():
    source = (ROOT / "portal" / "start.ps1").read_text(encoding="utf-8")
    assert "from helpers import load_state, profile_dir, state_path" in source
    assert "from helpers import ensure_tenant" in source
    assert 'Get-Content $statePath' not in source
