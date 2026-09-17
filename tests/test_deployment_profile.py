"""Offline isolation and safe replay contracts; never use real private files."""
import base64
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import helpers


@pytest.fixture(autouse=True)
def isolated_paths(tmp_path, monkeypatch):
    monkeypatch.delenv(helpers.PROFILE_ENV, raising=False)
    monkeypatch.delenv("AZURE_CONFIG_DIR", raising=False)
    monkeypatch.setattr(helpers, "ROOT", tmp_path)
    monkeypatch.setattr(helpers, "ACTIVE_PROFILE_FILE", tmp_path / "deployments" / "active-profile.json")
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    monkeypatch.setattr(helpers, "SCRIPT_DIR", legacy)
    monkeypatch.setattr(helpers, "CONFIG_FILE", legacy / "config.yaml")
    monkeypatch.setattr(helpers, "STATE_FILE", legacy / "state.json")
    helpers.CONFIG_FILE.write_text("workspace_name: old\n", encoding="utf-8")
    helpers.STATE_FILE.write_text('{"workspace_id":"old-workspace"}', encoding="utf-8")


@pytest.fixture
def profile(tmp_path, monkeypatch):
    folder = tmp_path / "deployments" / "new-tenant"
    folder.mkdir(parents=True)
    cache = tmp_path / "isolated-cache"
    cache.mkdir()
    cfg = {
        "tenant_id": "new-tenant",
        "az_subscription": "new-subscription",
        "workspace_name": "New workspace",
        "fabric_api_base": "https://api.fabric.microsoft.com/v1",
        "lakehouse_name": "Lakehouse",
        "semantic_model_name": "Model",
        "report_name": "Report",
        "ontology_name": "Ontology",
        "data_agent_name": "Agent",
        "deployment": {"expected_account": "operator@example.invalid",
                       "azure_config_dir": str(cache)},
    }
    (folder / "config.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")
    monkeypatch.setenv(helpers.PROFILE_ENV, str(folder))
    return folder, cfg


def token_for(cfg, **overrides):
    claims = {"tid": cfg["tenant_id"], "upn": cfg["deployment"]["expected_account"]}
    claims.update(overrides)
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"e30.{payload}.signature"


def test_no_profile_keeps_original_paths_and_state():
    assert helpers.profile_dir() is None
    assert helpers.load_config()["workspace_name"] == "old"
    assert helpers.load_state() == {"workspace_id": "old-workspace"}
    assert helpers.raw_dir() == helpers.ROOT / "data" / "raw"
    assert helpers.output_path(Path("old-output"), "runs") == Path("old-output")


def test_explicit_profile_never_reads_or_writes_legacy(profile):
    folder, cfg = profile
    before = helpers.CONFIG_FILE.read_bytes(), helpers.STATE_FILE.read_bytes()
    assert helpers.load_config() == cfg
    assert helpers.load_state() == {}
    assert helpers.raw_dir() == folder / "raw"
    assert helpers.output_path(Path("old"), "runs", "result.json") == folder / "artifacts" / "runs" / "result.json"
    helpers.save_state({"workspace_id": "new-workspace"})
    assert helpers.load_state()["workspace_id"] == "new-workspace"
    assert helpers.load_state()["_deployment_context"]["tenant_id"] == "new-tenant"
    assert before == (helpers.CONFIG_FILE.read_bytes(), helpers.STATE_FILE.read_bytes())


def test_active_pointer_makes_the_new_profile_default(profile, monkeypatch):
    folder, _ = profile
    monkeypatch.delenv(helpers.PROFILE_ENV)
    helpers.ACTIVE_PROFILE_FILE.write_text('{"profile":"new-tenant"}', encoding="utf-8")
    assert helpers.profile_dir() == folder
    assert helpers.config_path() == folder / "config.yaml"


def test_explicit_profile_wins_over_invalid_active_pointer(profile):
    folder, _ = profile
    helpers.ACTIVE_PROFILE_FILE.write_text("invalid", encoding="utf-8")
    assert helpers.profile_dir() == folder


@pytest.mark.parametrize("pointer", ['{"profile":"../legacy"}', '{"profile":null}', "[]"])
def test_invalid_pointer_does_not_fall_back(pointer):
    helpers.ACTIVE_PROFILE_FILE.parent.mkdir()
    helpers.ACTIVE_PROFILE_FILE.write_text(pointer, encoding="utf-8")
    with pytest.raises(RuntimeError, match="one named"):
        helpers.load_config()


def test_missing_selected_profile_does_not_fall_back(tmp_path, monkeypatch):
    monkeypatch.setenv(helpers.PROFILE_ENV, str(tmp_path / "missing"))
    with pytest.raises(RuntimeError, match="separate config.yaml"):
        helpers.load_config()


def test_original_source_directory_cannot_be_used_as_a_profile(monkeypatch):
    monkeypatch.setenv(helpers.PROFILE_ENV, str(helpers.SCRIPT_DIR))
    with pytest.raises(RuntimeError, match="separate config"):
        helpers.load_config()


def test_empty_explicit_selector_is_not_a_request_for_the_old_tenant(monkeypatch):
    monkeypatch.setenv(helpers.PROFILE_ENV, "")
    with pytest.raises(RuntimeError, match="empty"):
        helpers.profile_dir()


def test_unstamped_old_state_is_refused(profile):
    folder, _ = profile
    (folder / "state.json").write_text('{"workspace_id":"old-workspace"}', encoding="utf-8")
    with pytest.raises(RuntimeError, match="do not copy old"):
        helpers.load_state()


def test_mismatched_context_is_refused_on_read_and_write(profile):
    folder, _ = profile
    helpers.save_state({"workspace_id": "new-workspace"})
    state = helpers.load_state()
    state["_deployment_context"]["tenant_id"] = "old-tenant"
    with pytest.raises(RuntimeError, match="different deployment"):
        helpers.save_state(state)
    (folder / "state.json").write_text(json.dumps(state), encoding="utf-8")
    with pytest.raises(RuntimeError, match="does not belong"):
        helpers.load_state()


def test_profile_artifacts_cannot_escape(profile):
    with pytest.raises(ValueError, match="inside"):
        helpers.output_path(Path("old"), "..", "other")


def account_json(cfg):
    return json.dumps({"tenantId": cfg["tenant_id"], "id": cfg["az_subscription"],
                       "user": {"name": cfg["deployment"]["expected_account"]}})


def test_profile_checks_identity_without_switching_accounts(profile, monkeypatch):
    _, cfg = profile
    run = Mock(return_value=SimpleNamespace(stdout=account_json(cfg)))
    monkeypatch.setattr(helpers.subprocess, "run", run)
    helpers.ensure_tenant(cfg, quiet=True)
    assert run.call_args.args[0] == ["az", "account", "show", "--output", "json"]
    assert Path(helpers.os.environ["AZURE_CONFIG_DIR"]) == Path(cfg["deployment"]["azure_config_dir"])


def test_wrong_existing_cache_is_not_overwritten(profile, tmp_path, monkeypatch):
    _, cfg = profile
    other = tmp_path / "other-cache"
    other.mkdir()
    monkeypatch.setenv("AZURE_CONFIG_DIR", str(other))
    run = Mock()
    monkeypatch.setattr(helpers.subprocess, "run", run)
    with pytest.raises(RuntimeError, match="differs"):
        helpers.ensure_tenant(cfg)
    assert helpers.os.environ["AZURE_CONFIG_DIR"] == str(other)
    run.assert_not_called()


def test_identity_mismatch_fails_before_resource_access(profile, monkeypatch):
    _, cfg = profile
    run = Mock(return_value=SimpleNamespace(stdout=json.dumps(
        {"tenantId": "wrong", "id": cfg["az_subscription"],
         "user": {"name": cfg["deployment"]["expected_account"]}})))
    monkeypatch.setattr(helpers.subprocess, "run", run)
    with pytest.raises(RuntimeError, match="no account was switched"):
        helpers.ensure_tenant(cfg)
    assert run.call_count == 1


@pytest.mark.parametrize("claims", [{"tid": "other"}, {"upn": "someone@example.invalid"}])
def test_token_must_match_both_tenant_and_account(profile, claims):
    _, cfg = profile
    with pytest.raises(RuntimeError, match="does not belong"):
        helpers.validate_token_identity(token_for(cfg, **claims))


def test_valid_token_identity_is_accepted(profile):
    _, cfg = profile
    helpers.validate_token_identity(token_for(cfg))


@pytest.mark.parametrize("token", ["opaque", "x.not-json.x", "x.W10.x"])
def test_uninspectable_token_is_not_silently_trusted(profile, token):
    with pytest.raises(RuntimeError):
        helpers.validate_token_identity(token)


def test_sdk_factory_uses_explicit_cli_and_checks_issued_tokens(profile, monkeypatch):
    import azure.identity
    _, cfg = profile
    cli = Mock()
    cli.get_token.return_value = SimpleNamespace(token=token_for(cfg))
    constructor = Mock(return_value=cli)
    default = Mock(side_effect=AssertionError("must not use a credential chain"))
    monkeypatch.setattr(azure.identity, "AzureCliCredential", constructor)
    monkeypatch.setattr(azure.identity, "DefaultAzureCredential", default)
    guard = Mock()
    monkeypatch.setattr(helpers, "ensure_tenant", guard)
    credential = helpers.get_sdk_credential(process_timeout=45)
    guard.assert_not_called()
    assert constructor.call_args.kwargs == {
        "tenant_id": cfg["tenant_id"], "process_timeout": 45}
    assert credential.get_token("https://ai.azure.com/.default").token == token_for(cfg)
    guard.assert_called_once_with(quiet=True)
    cli.get_token.return_value = SimpleNamespace(token=token_for(cfg, tid="wrong"))
    with pytest.raises(RuntimeError, match="does not belong"):
        credential.get_token("https://ai.azure.com/.default")
    credential.close()
    cli.close.assert_called_once()


def test_sdk_factory_preserves_legacy_credentials(monkeypatch):
    import azure.identity
    default = Mock()
    monkeypatch.setattr(azure.identity, "DefaultAzureCredential", default)
    assert helpers.get_sdk_credential(process_timeout=33) is default.return_value
    default.assert_called_once_with(process_timeout=33)


def test_real_sdk_does_not_pass_conflicting_tenant_and_subscription_flags(profile, monkeypatch):
    import azure.identity._credentials.azure_cli as azure_cli
    _, cfg = profile
    command = Mock(return_value=json.dumps({
        "accessToken": token_for(cfg), "expires_on": 4102444800,
    }))
    monkeypatch.setattr(azure_cli, "_run_command", command)
    monkeypatch.setattr(helpers, "ensure_tenant", Mock())
    credential = helpers.get_sdk_credential()
    token = credential.get_token("https://ai.azure.com/.default")
    assert token.token == token_for(cfg)
    args = command.call_args.args[0]
    assert "--tenant" in args
    assert args[args.index("--tenant") + 1] == cfg["tenant_id"]
    assert "--subscription" not in args


def test_recorded_workspace_is_checked_against_live_metadata(profile, monkeypatch):
    _, cfg = profile
    helpers.save_state({"workspace_id": "recorded-workspace"})
    response = Mock()
    response.json.return_value = {"displayName": "Someone else's workspace"}
    monkeypatch.setattr(helpers, "get_resource_token", lambda resource: token_for(cfg))
    monkeypatch.setattr(helpers.requests, "get", Mock(return_value=response))
    with pytest.raises(RuntimeError, match="workspace does not match"):
        helpers.get_fabric_token()


@pytest.mark.parametrize("compact", [False, True])
def test_ontology_managed_graph_names_accept_the_actual_service_suffix(profile, monkeypatch, compact):
    _, cfg = profile
    ontology = "00000000-0000-0000-0000-000000000000"
    helpers.save_state({"workspace_id": "workspace", "ontology_id": ontology, "graph_model_id": "graph"})
    suffix = ontology.replace("-", "") if compact else ontology
    values = [{"displayName": cfg["workspace_name"]},
              {"type": "Ontology", "displayName": cfg["ontology_name"]},
              {"type": "GraphModel", "displayName": cfg["ontology_name"] + "_graph_" + suffix}]
    responses = []
    for value in values:
        response = Mock()
        response.json.return_value = value
        responses.append(response)
    monkeypatch.setattr(helpers.requests, "get", Mock(side_effect=responses))
    helpers._validate_fabric_ownership(token_for(cfg))


def test_orchestrator_does_not_pass_its_arguments_to_children(monkeypatch):
    import deploy_all
    run = Mock()
    monkeypatch.setattr(deploy_all.subprocess, "run", run)
    monkeypatch.setattr(sys, "argv", ["deploy_all.py", "data_agent", "--no-warmup"])
    deploy_all.run_steps(["data_agent"])
    command = run.call_args.args[0]
    assert len(command) == 2
    assert command[0] == sys.executable
    assert Path(command[1]).name == "deploy_data_agent.py"
    assert run.call_args.kwargs["check"] is True


def test_notebook_binding_roundtrips_the_existing_builder():
    import deploy_setup_notebook as notebook
    source = notebook.build_notebook_py("workspace", "lakehouse", "Lakehouse", [], 65, "CAMP_007")
    assert notebook.notebook_binding(source) == {
        "default_lakehouse": "lakehouse", "default_lakehouse_name": "Lakehouse",
        "default_lakehouse_workspace_id": "workspace"}
    assert "n = spark.table(t).count()" in source


@pytest.mark.parametrize("shape", ["py", "dependencies", "trident"])
def test_binding_readback_accepts_native_and_jupyter_definitions(profile, monkeypatch, shape):
    import deploy_setup_notebook as notebook
    _, cfg = profile
    state = {"workspace_id": "workspace", "lakehouse_id": "lakehouse"}
    binding = {"default_lakehouse": "lakehouse",
               "default_lakehouse_name": cfg["lakehouse_name"],
               "default_lakehouse_workspace_id": "workspace"}
    if shape == "py":
        source = notebook.build_notebook_py("workspace", "lakehouse", cfg["lakehouse_name"], [], 65, "CAMP_007")
        path = "notebook-content.py"
    else:
        source = json.dumps({"metadata": {shape: {"lakehouse": binding}}})
        path = "notebook-content.ipynb"
    response = Mock(status_code=200)
    response.json.return_value = {"definition": {"parts": [{
        "path": path, "payload": base64.b64encode(source.encode()).decode()}]}}
    monkeypatch.setattr(notebook.requests, "post", lambda *args, **kwargs: response)
    notebook.verify_notebook_binding(cfg, state, "notebook", "token")


def test_profile_notebook_reuses_identity_without_deletion(profile, monkeypatch):
    import deploy_setup_notebook as notebook
    _, cfg = profile
    state = {"workspace_id": "workspace", "lakehouse_id": "lakehouse", "notebook_setup_id": "notebook"}
    monkeypatch.setattr(notebook, "find_item", lambda *args: {"id": "notebook"})
    verify, push = Mock(), Mock()
    monkeypatch.setattr(notebook, "verify_notebook_binding", verify)
    monkeypatch.setattr(notebook, "push_notebook", push)
    monkeypatch.setattr(notebook, "recreate_notebook", Mock(side_effect=AssertionError("no DELETE")))
    assert notebook.ensure_setup_notebook(cfg, state, "source", "token") == "notebook"
    assert verify.call_count == 2
    push.assert_called_once_with("workspace", "notebook", "source", "token")


def test_unowned_notebook_is_not_updated_or_deleted(profile, monkeypatch):
    import deploy_setup_notebook as notebook
    _, cfg = profile
    monkeypatch.setattr(notebook, "find_item", lambda *args: {"id": "untracked"})
    write = Mock(side_effect=AssertionError("must not write"))
    monkeypatch.setattr(notebook, "push_notebook", write)
    monkeypatch.setattr(notebook, "recreate_notebook", write)
    with pytest.raises(RuntimeError, match="not owned"):
        notebook.ensure_setup_notebook(cfg, {"workspace_id": "workspace"}, "source", "token")
    write.assert_not_called()


def test_new_notebook_id_is_saved_before_followup_calls(profile, monkeypatch):
    import deploy_setup_notebook as notebook
    _, cfg = profile
    calls = []
    monkeypatch.setattr(notebook, "find_item", Mock(side_effect=helpers.ItemNotFoundError("not found")))
    monkeypatch.setattr(notebook, "create_notebook", lambda *args: calls.append("create") or "new-id")
    monkeypatch.setattr(notebook, "save_state", lambda state: calls.append(("save", state["notebook_setup_id"])))
    monkeypatch.setattr(notebook, "verify_notebook_binding", lambda *args: calls.append("verify"))
    result = notebook.ensure_setup_notebook(cfg, {"workspace_id": "workspace"}, "source", "token")
    assert result == "new-id"
    assert calls == ["create", ("save", "new-id"), "verify"]


@pytest.mark.parametrize("statuses, stage", [([403], "create"), ([201, 400], "append"), ([201, 202, 500], "flush")])
def test_each_onelake_write_stage_must_succeed(statuses, stage):
    import deploy_lakehouse
    responses = [SimpleNamespace(status=status, read=lambda: b"failure") for status in statuses]
    connection = Mock()
    connection.getresponse.side_effect = responses
    with pytest.raises(RuntimeError, match=f"OneLake {stage} failed"):
        deploy_lakehouse._put(connection, {}, "/workspace/lakehouse/Files/input.csv", b"data")
    assert connection.request.call_count == len(statuses)


def test_reframe_queue_failure_cannot_be_reported_as_success(monkeypatch):
    import deploy_semantic_model as model
    monkeypatch.setattr(model, "get_powerbi_token", lambda: "token")
    response = SimpleNamespace(status_code=403, text="permission denied")
    monkeypatch.setattr(model.requests, "post", lambda *args, **kwargs: response)
    with pytest.raises(RuntimeError, match="could not be queued"):
        model.reframe_direct_lake("workspace", "model")


def test_created_model_identity_survives_a_failed_first_reframe(monkeypatch):
    import deploy_semantic_model as model
    cfg = {"fabric_api_base": "https://api.fabric.microsoft.com/v1",
           "semantic_model_name": "Model"}
    monkeypatch.setattr(model, "API_BASE", None)
    monkeypatch.setattr(model, "load_config", lambda: cfg)
    monkeypatch.setattr(model, "load_state", lambda: {"workspace_id": "workspace"})
    monkeypatch.setattr(model, "ensure_tenant", lambda cfg: None)
    monkeypatch.setattr(model, "get_fabric_token", lambda: "token")
    monkeypatch.setattr(model, "build_model_bim", lambda *args: {
        "model": {"tables": [], "relationships": []}})
    monkeypatch.setattr(model, "find_item", Mock(side_effect=helpers.ItemNotFoundError("missing")))
    response = Mock(status_code=201)
    response.json.return_value = {"id": "new-model"}
    monkeypatch.setattr(model.requests, "post", Mock(return_value=response))
    monkeypatch.setattr(model, "verify_deployment", Mock())
    monkeypatch.setattr(model, "reframe_direct_lake",
                        Mock(side_effect=RuntimeError("source not ready")))
    saved = Mock()
    monkeypatch.setattr(model, "save_state", saved)
    with pytest.raises(RuntimeError, match="source not ready"):
        model.main()
    saved.assert_called_once_with({"workspace_id": "workspace", "semantic_model_id": "new-model"})


def test_graph_refresh_failure_does_not_save_a_successful_receipt(monkeypatch):
    import deploy_graph as graph
    cfg = {"fabric_api_base": "https://api.fabric.microsoft.com/v1", "ontology_name": "Ontology"}
    monkeypatch.setattr(graph, "load_config", lambda: cfg)
    monkeypatch.setattr(graph, "load_state", lambda: {"workspace_id": "workspace", "lakehouse_id": "lakehouse"})
    monkeypatch.setattr(graph, "ensure_tenant", lambda cfg: None)
    monkeypatch.setattr(graph, "get_fabric_token", lambda: "token")
    monkeypatch.setattr(graph, "find_graph_model", lambda *args: ("graph", "Ontology_graph"))
    monkeypatch.setattr(graph, "table_locations", lambda *args: {})
    monkeypatch.setattr(graph, "build_definition", lambda *args: (
        {"nodeTypes": [], "edgeTypes": []}, {"dataSources": []}, {}, {}))
    monkeypatch.setattr(graph.time, "sleep", lambda *args: None)
    monkeypatch.setattr(graph.requests, "post", Mock(side_effect=[
        SimpleNamespace(status_code=200, headers={}),
        SimpleNamespace(status_code=202, headers={"Location": "https://api.fabric.microsoft.com/job"}),
    ]))
    response = Mock()
    response.json.return_value = {"status": "Failed", "failureReason": "invalid source"}
    monkeypatch.setattr(graph.requests, "get", lambda *args, **kwargs: response)
    saved = Mock()
    monkeypatch.setattr(helpers, "save_state", saved)
    with pytest.raises(RuntimeError, match="RefreshGraph Failed"):
        graph.main()
    saved.assert_not_called()


def test_deduplicated_graph_refresh_waits_for_the_overlapping_job(monkeypatch):
    import deploy_graph as graph
    duplicate = {"id": "duplicate", "status": "Deduped",
                 "startTimeUtc": "2026-01-01T10:00:10Z"}
    active = {"id": "actual", "jobType": "Refresh", "status": "InProgress",
              "startTimeUtc": "2026-01-01T10:00:00Z", "endTimeUtc": None}
    old = {"id": "old", "jobType": "Refresh", "status": "Completed",
           "startTimeUtc": "2026-01-01T09:00:00Z", "endTimeUtc": "2026-01-01T09:01:00Z"}
    finished = dict(active, status="Completed", endTimeUtc="2026-01-01T10:03:00Z")
    responses = []
    for value in (duplicate, {"value": [duplicate, active, old]}, finished):
        response = Mock()
        response.json.return_value = value
        responses.append(response)
    get = Mock(side_effect=responses)
    monkeypatch.setattr(graph.requests, "get", get)
    monkeypatch.setattr(graph.time, "sleep", lambda seconds: None)
    result = graph.wait_for_graph_refresh("https://api.fabric.microsoft.com/v1", "ws", "graph", {}, "duplicate-url")
    assert result["id"] == "actual" and result["status"] == "Completed"
    assert get.call_args.args[0].endswith("/jobs/instances/actual")


def test_a_deduplicated_refresh_is_not_itself_success(monkeypatch):
    import deploy_graph as graph
    response = Mock()
    response.json.return_value = {"id": "duplicate", "status": "Deduped"}
    monkeypatch.setattr(graph.requests, "get", lambda *args, **kwargs: response)
    monkeypatch.setattr(graph.time, "sleep", lambda seconds: None)
    with pytest.raises(RuntimeError, match="no start time"):
        graph.wait_for_graph_refresh("api", "ws", "graph", {}, "url")


def test_a2a_connection_uses_checked_requests_and_reads_back_the_binding(monkeypatch):
    import deploy_supervisor_agent as supervisor
    target = "https://example.services.ai.azure.com/api/projects/demo/agents/Agent/endpoint/protocols/a2a"
    token = Mock(return_value="test-token")
    monkeypatch.setattr(supervisor, "get_resource_token", token)
    monkeypatch.setattr(supervisor, "_az", Mock(side_effect=AssertionError("no az rest")))
    response = Mock()
    response.json.return_value = supervisor.arm_a2a_connection_body(target)
    put, get = Mock(return_value=response), Mock(return_value=response)
    monkeypatch.setattr(supervisor.requests, "put", put)
    monkeypatch.setattr(supervisor.requests, "get", get)
    result = supervisor.ensure_a2a_connection("sub", "rg", "account", "project", "connection", target)
    token.assert_called_once_with("https://management.azure.com")
    assert put.call_args.kwargs["json"] == supervisor.arm_a2a_connection_body(target)
    assert get.call_args.args[0] == put.call_args.args[0]
    assert result == supervisor.connection_arm_id("sub", "rg", "account", "project", "connection")


def test_captures_require_a_real_matching_profile_deployment():
    from capture_frozen_answers import capture_context
    cfg = {"tenant_id": "tenant"}
    fnd = {"project_endpoint": "https://example.services.ai.azure.com/api/projects/demo",
           "supervisor_agent_name": "Supervisor", "model_deployment": "model"}
    with pytest.raises(RuntimeError, match="missing state"):
        capture_context(cfg, {}, fnd)
    state = {"workspace_id": "workspace", "semantic_model_id": "model", "data_agent_id": "agent",
             "foundry_supervisor_agent_name": "different"}
    with pytest.raises(RuntimeError, match="supervisor does not match"):
        capture_context(cfg, state, fnd)
    state["foundry_supervisor_agent_name"] = "Supervisor"
    result = capture_context(cfg, state, fnd)
    assert result["workspaceId"] == "workspace"
    assert result["agentName"] == "Supervisor"
    assert "account" not in result


def test_taskflow_check_uses_the_profile_artifact_and_refuses_missing_output(profile, monkeypatch, capsys):
    import importlib.util
    script = Path(__file__).resolve().parents[1] / "src" / "build_taskflow.py"
    spec = importlib.util.spec_from_file_location("_profile_taskflow_test", script)
    taskflow = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(taskflow)
    folder, _ = profile
    assert taskflow.OUT == folder / "artifacts" / "taskflow" / "marketing_taskflow.json"
    monkeypatch.setattr(taskflow, "build", lambda cfg: {"tasks": [], "edges": []})
    monkeypatch.setattr(sys, "argv", ["build_taskflow.py", "--check"])
    with pytest.raises(SystemExit, match="MISSING"):
        taskflow.main()
    taskflow.OUT.parent.mkdir(parents=True)
    expected = json.dumps({"tasks": [], "edges": []}, indent=2) + "\n"
    taskflow.OUT.write_text(expected, encoding="utf-8")
    taskflow.main()
    assert "selected config" in capsys.readouterr().out
    taskflow.OUT.write_text("{}", encoding="utf-8")
    with pytest.raises(SystemExit, match="STALE"):
        taskflow.main()
