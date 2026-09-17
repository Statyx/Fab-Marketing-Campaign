"""Offline profile consumers: SDK calls and generator/log writes are intercepted."""
import ast
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

import helpers  # noqa: E402

CONSUMERS = (
    "deploy_foundry_agent", "deploy_voc_agent", "deploy_supervisor_agent",
    "foundry_supervision", "generate_voc_corpus", "verify_supervisor", "capture_frozen_answers",
)


def _load(name):
    # A fresh module snapshots the selected paths without changing other tests' imports.
    spec = importlib.util.spec_from_file_location(f"_profile_test_{name}", SRC / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fixture_file(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture
def context(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    profile = root / "deployments" / "replay"
    cfg = {
        "tenant_id": "test-tenant",
        "az_subscription": "test-subscription",
        "workspace_name": "ReplayWorkspace",
        "fabric_api_base": "https://api.fabric.microsoft.com/v1",
        "deployment": {"expected_account": "demo@example.invalid",
                       "azure_config_dir": str(tmp_path / "azure-cache")},
        "foundry": {
            "project_endpoint": "https://example.services.ai.azure.com/api/projects/demo",
            "model_deployment": "gpt-5.4",
            "agent_name": "Marketing-Churn-Front-Door",
            "binding": "fabric-iq",
            "fabric_iq_connection_name": "MarketingChurnAgent",
            "fabric_iq_target": "data_agent",
            "fabric_iq_endpoint": "connection",
            "require_approval": "never",
        },
    }
    legacy_config = _fixture_file(root / "src" / "config.yaml", json.dumps(cfg))
    legacy_state = _fixture_file(root / "src" / "state.json", '{"legacy": "untouched"}')
    _fixture_file(profile / "config.yaml", json.dumps(cfg))
    monkeypatch.setattr(helpers, "ROOT", root)
    monkeypatch.setattr(helpers, "SCRIPT_DIR", root / "src")
    monkeypatch.setattr(helpers, "CONFIG_FILE", legacy_config)
    monkeypatch.setattr(helpers, "STATE_FILE", legacy_state)
    monkeypatch.setattr(helpers, "ACTIVE_PROFILE_FILE", root / "deployments" / "active-profile.json")
    monkeypatch.setenv("FAB_MARKETING_PROFILE_DIR", str(profile))
    return SimpleNamespace(root=root, profile=profile, cfg=cfg,
                           legacy_config=legacy_config, legacy_state=legacy_state)


@pytest.fixture
def sdk(monkeypatch):
    modules = {name: ModuleType(name) for name in (
        "azure", "azure.ai", "azure.ai.projects", "azure.ai.projects.models",
    )}
    for name, module in modules.items():
        module.__path__ = []
        monkeypatch.setitem(sys.modules, name, module)
        parent, _, child = name.rpartition(".")
        if parent in modules:
            setattr(modules[parent], child, module)
    client = Mock()
    client.agents.create_version.return_value = SimpleNamespace(version="1")
    constructor = Mock(return_value=client)
    modules["azure.ai.projects"].AIProjectClient = constructor
    for name in ("PromptAgentDefinition", "FileSearchTool", "A2APreviewTool"):
        setattr(modules["azure.ai.projects.models"], name, Mock(name=name))
    return SimpleNamespace(client=client, constructor=constructor)


@pytest.mark.parametrize("name", CONSUMERS)
def test_consumers_do_not_bypass_or_reimplement_shared_profile_helpers(name):
    tree = ast.parse((SRC / f"{name}.py").read_text(encoding="utf-8-sig"))
    assert not [
        node for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == "azure.identity"
    ]
    assert not [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and (getattr(node.func, "id", None) or getattr(node.func, "attr", None))
        in {"DefaultAzureCredential", "AzureCliCredential"}
    ]
    assert not {
        node.name for node in tree.body if isinstance(node, ast.FunctionDef)
    } & {"profile_dir", "raw_dir", "output_path", "get_sdk_credential"}


@pytest.mark.parametrize("selected", [False, True])
def test_project_client_uses_shared_credential_factory(context, sdk, monkeypatch, selected):
    if not selected:
        monkeypatch.delenv("FAB_MARKETING_PROFILE_DIR")
    credential = object()
    factory = Mock(return_value=credential)
    monkeypatch.setattr(helpers, "get_sdk_credential", factory)
    front = _load("deploy_foundry_agent")

    assert front.project_client(context.cfg["foundry"]) is sdk.client
    factory.assert_called_once_with()
    sdk.constructor.assert_called_once_with(
        endpoint=context.cfg["foundry"]["project_endpoint"],
        credential=credential, allow_preview=True,
    )


class _FalseyCredential:
    def __bool__(self):
        return False


@pytest.mark.parametrize("credential", [object(), _FalseyCredential()])
def test_project_client_preserves_explicit_credential(context, sdk, monkeypatch, credential):
    factory = Mock(side_effect=AssertionError("explicit credentials must not be replaced"))
    monkeypatch.setattr(helpers, "get_sdk_credential", factory)
    front = _load("deploy_foundry_agent")
    front.project_client(context.cfg["foundry"], credential=credential)
    factory.assert_not_called()
    assert sdk.constructor.call_args.kwargs["credential"] is credential


def test_credential_failure_does_not_fall_back(context, sdk, monkeypatch):
    factory = Mock(side_effect=RuntimeError("profile identity mismatch"))
    monkeypatch.setattr(helpers, "get_sdk_credential", factory)
    front = _load("deploy_foundry_agent")
    with pytest.raises(RuntimeError, match="profile identity mismatch"):
        front.project_client(context.cfg["foundry"])
    sdk.constructor.assert_not_called()


@pytest.mark.parametrize("selection", ["legacy", "absolute", "relative", "pointer"])
def test_corpus_and_supervision_paths_follow_selection(context, monkeypatch, selection):
    if selection in ("legacy", "pointer"):
        monkeypatch.delenv("FAB_MARKETING_PROFILE_DIR")
    if selection == "legacy":
        monkeypatch.setattr(helpers, "ROOT", ROOT)
    if selection == "relative":
        monkeypatch.setenv("FAB_MARKETING_PROFILE_DIR", str(Path("deployments") / "replay"))
    if selection == "pointer":
        _fixture_file(helpers.ACTIVE_PROFILE_FILE, '{"profile": "replay"}')
    gen = _load("generate_voc_corpus")
    voc = _load("deploy_voc_agent")
    supervision = _load("foundry_supervision")
    capture = _load("capture_frozen_answers")

    expected_raw = (ROOT / "data" / "raw" if selection == "legacy"
                    else context.profile / "raw")
    assert gen.RAW == supervision.RAW == expected_raw
    assert gen.OUT == voc.CORPUS_DIR == expected_raw / "text" / "voice_of_customer"
    assert gen.MANIFEST == gen.OUT / "_manifest.json"
    assert supervision.OUT_DIR == (
        ROOT / "data" / "supervision" if selection == "legacy"
        else context.profile / "artifacts" / "supervision"
    )
    assert capture.ANSWERS == (
        ROOT / "app-v2" / "src" / "data" / "frozen-answers.generated.json"
        if selection == "legacy"
        else context.profile / "artifacts" / "captures" / "frozen_answers.json"
    )


@pytest.mark.parametrize("name", ["generate_voc_corpus", "deploy_voc_agent", "foundry_supervision"])
def test_invalid_profile_never_falls_back_to_original_paths(context, monkeypatch, name):
    monkeypatch.setenv("FAB_MARKETING_PROFILE_DIR", str(context.root / "missing"))
    with pytest.raises(RuntimeError, match="profile"):
        _load(name)


def test_missing_profile_corpus_and_data_do_not_read_the_original(context):
    voc = _load("deploy_voc_agent")
    supervision = _load("foundry_supervision")
    assert voc.corpus_files() == []
    with pytest.raises(SystemExit) as error:
        supervision.local_truth({})
    assert str(context.profile / "raw") in str(error.value)


def test_selected_corpus_reader_ignores_manifest_and_uses_profile_csv(context):
    corpus = context.profile / "raw" / "text" / "voice_of_customer"
    files = [_fixture_file(corpus / name, "fixture") for name in ("VOC_00002.md", "VOC_00001.md")]
    _fixture_file(corpus / "_manifest.json", "[]")
    _fixture_file(corpus / "unrelated.md", "fixture")
    _fixture_file(context.profile / "raw" / "marketing" / "marketing_sends.csv",
                  "campaign_id,customer_id\nCAMP_007,CUST_1\nCAMP_OTHER,CUST_2\n")
    voc = _load("deploy_voc_agent")
    gen = _load("generate_voc_corpus")
    assert voc.corpus_files() == sorted(files)
    assert gen.campaign_intensity() == {"CUST_1": 1}


def test_generator_writes_and_cleanup_are_confined_to_selected_corpus(context, monkeypatch):
    gen = _load("generate_voc_corpus")
    stale = _fixture_file(gen.OUT / "VOC_stale.md", "fixture")
    original = _fixture_file(context.root / "data" / "raw" / "text" /
                             "voice_of_customer" / "VOC_original.md", "original fixture")
    writes, deletes, directories = [], [], []
    monkeypatch.setattr(Path, "mkdir", lambda path, **kw: directories.append(path))
    monkeypatch.setattr(Path, "unlink", lambda path: deletes.append(path))
    monkeypatch.setattr(Path, "write_text", lambda path, text, **kw: writes.append((path, text)))
    record = {"id": "VOC_fixture", "customer_id": "CUST_fixture", "segment": "OTHER",
              "channel": "email", "kind": "email", "occurred_at": "2026-02-01",
              "text": "Fixture only."}

    gen.write([record])

    assert directories == [gen.OUT]
    assert deletes == [stale]
    assert [path for path, _ in writes] == [gen.OUT / "VOC_fixture.md", gen.MANIFEST]
    assert json.loads(writes[1][1]) == [record]
    assert original.read_text(encoding="utf-8") == "original fixture"
    assert not (gen.OUT / "VOC_fixture.md").exists()


@pytest.mark.parametrize("args", [["--recreate"], ["--check", "--recreate"]])
def test_profile_recreate_is_refused_before_clients_or_state_reads(context, monkeypatch, args):
    voc = _load("deploy_voc_agent")
    forbidden = Mock(side_effect=AssertionError("recreate must stop before this operation"))
    for name in ("load_config", "load_state", "preflight", "project_client"):
        monkeypatch.setattr(voc, name, forbidden)
    monkeypatch.setattr(sys, "argv", ["deploy_voc_agent.py", *args])
    with pytest.raises(SystemExit, match="--recreate.*selected deployment profile"):
        voc.main()
    forbidden.assert_not_called()


def _voc_run(context, sdk, monkeypatch, completed, args=()):
    voc = _load("deploy_voc_agent")
    credential = object()
    factory = Mock(return_value=credential)
    monkeypatch.setattr(voc, "get_sdk_credential", factory)
    client_factory = Mock(return_value=sdk.client)
    monkeypatch.setattr(voc, "project_client", client_factory)
    monkeypatch.setattr(voc, "preflight", Mock(return_value=True))
    monkeypatch.setattr(voc, "corpus_files", Mock(return_value=[Path("VOC_a.md"), Path("VOC_b.md")]))
    store = SimpleNamespace(id="vs_fixture", file_counts=SimpleNamespace(completed=completed))
    finder = Mock(return_value=store)
    upload = Mock(return_value=2)
    monkeypatch.setattr(voc, "find_vector_store", finder)
    monkeypatch.setattr(voc, "upload_corpus", upload)
    sdk.client.get_openai_client.return_value.vector_stores.create.return_value = store
    monkeypatch.setattr(sys, "argv", ["deploy_voc_agent.py", *args])
    return voc, factory, client_factory, credential, finder, upload


@pytest.mark.parametrize("completed", [0, 1])
def test_partial_profile_store_is_not_deleted_or_rebuilt(context, sdk, monkeypatch, completed):
    voc, _, _, _, _, upload = _voc_run(context, sdk, monkeypatch, completed)
    with pytest.raises(SystemExit, match="will not delete or rebuild"):
        voc.main()
    stores = sdk.client.get_openai_client.return_value.vector_stores
    stores.delete.assert_not_called()
    stores.create.assert_not_called()
    upload.assert_not_called()
    sdk.client.agents.create_version.assert_not_called()
    sdk.client.close.assert_called_once_with()
    assert not (context.profile / "state.json").exists()
    assert json.loads(context.legacy_state.read_text()) == {"legacy": "untouched"}


def test_voc_reuses_complete_store_with_shared_credential_and_profile_state(context, sdk, monkeypatch):
    before = context.legacy_config.read_bytes(), context.legacy_state.read_bytes()
    voc, factory, client_factory, credential, _, upload = _voc_run(
        context, sdk, monkeypatch, completed=2)
    assert voc.main() == 0
    factory.assert_called_once_with(process_timeout=voc.CLI_TIMEOUT_SECONDS)
    assert client_factory.call_args.args[1] is credential
    stores = sdk.client.get_openai_client.return_value.vector_stores
    stores.delete.assert_not_called()
    stores.create.assert_not_called()
    upload.assert_not_called()
    assert helpers.load_state()["foundry_voc_vector_store_id"] == "vs_fixture"
    assert (context.legacy_config.read_bytes(), context.legacy_state.read_bytes()) == before


@pytest.mark.parametrize("recreate", [False, True])
def test_legacy_store_rebuild_behaviour_is_preserved(context, sdk, monkeypatch, recreate):
    monkeypatch.delenv("FAB_MARKETING_PROFILE_DIR")
    voc, _, _, _, finder, upload = _voc_run(
        context, sdk, monkeypatch, completed=0, args=["--recreate"] if recreate else [])
    assert voc.main() == 0
    stores = sdk.client.get_openai_client.return_value.vector_stores
    stores.create.assert_called_once_with(name="voc-marketing-churn")
    upload.assert_called_once()
    if recreate:
        finder.assert_not_called()
        stores.delete.assert_not_called()
    else:
        stores.delete.assert_called_once_with(vector_store_id="vs_fixture")


@pytest.mark.parametrize("selected", [False, True])
def test_supervisor_arm_calls_guard_selected_profile_first(context, monkeypatch, selected):
    if not selected:
        monkeypatch.delenv("FAB_MARKETING_PROFILE_DIR")
    supervisor = _load("deploy_supervisor_agent")
    order = []
    monkeypatch.setattr(supervisor, "ensure_tenant", lambda: order.append("guard"))
    def run(*args, **kwargs):
        order.append("az")
        return SimpleNamespace(returncode=0, stdout="fixture", stderr="")
    monkeypatch.setattr(supervisor.subprocess, "run", run)
    assert supervisor._az(["resource", "list"]) == (0, "fixture")
    assert order == (["guard", "az"] if selected else ["az"])


def test_supervisor_arm_profile_mismatch_never_reaches_cli(context, monkeypatch):
    supervisor = _load("deploy_supervisor_agent")
    guard = Mock(side_effect=RuntimeError("profile account mismatch"))
    run = Mock(side_effect=AssertionError("CLI must not run after a failed guard"))
    monkeypatch.setattr(supervisor, "ensure_tenant", guard)
    monkeypatch.setattr(supervisor.subprocess, "run", run)
    with pytest.raises(RuntimeError, match="profile account mismatch"):
        supervisor._az(["rest", "--method", "put"])
    run.assert_not_called()


def test_supervisor_and_verification_share_the_profile_client(context, sdk, monkeypatch):
    import verify_supervisor

    supervisor = _load("deploy_supervisor_agent")
    factory = Mock(return_value=object())
    client_factory = Mock(return_value=sdk.client)
    monkeypatch.setattr(supervisor, "get_sdk_credential", factory)
    monkeypatch.setattr(supervisor, "project_client", client_factory)
    monkeypatch.setattr(supervisor, "preflight", Mock(return_value=True))
    monkeypatch.setattr(supervisor, "resolve_arm_scope", Mock(return_value=("sub", "rg")))
    monkeypatch.setattr(supervisor, "ensure_a2a_connection", Mock(return_value="connection"))
    monkeypatch.setattr(supervisor, "ensure_incoming_a2a", Mock(return_value="unchanged"))
    verify = Mock(return_value=0)
    monkeypatch.setattr(verify_supervisor, "run_verification", verify)
    monkeypatch.setattr(sys, "argv", ["deploy_supervisor_agent.py", "--verify"])
    before = context.legacy_config.read_bytes(), context.legacy_state.read_bytes()

    assert supervisor.main() == 0
    factory.assert_called_once_with(process_timeout=90)
    assert client_factory.call_args.args[1] is factory.return_value
    verify.assert_called_once_with(sdk.client, supervisor.supervisor_config(context.cfg))
    assert helpers.load_state()["foundry_supervisor_agent_name"] == "Marketing-Supervisor"
    assert (context.legacy_config.read_bytes(), context.legacy_state.read_bytes()) == before


@pytest.mark.parametrize("selected", [False, True])
def test_supervision_log_uses_selected_output_without_original_writes(
        context, sdk, monkeypatch, selected):
    if not selected:
        monkeypatch.delenv("FAB_MARKETING_PROFILE_DIR")
    factory = Mock(return_value=object())
    monkeypatch.setattr(helpers, "get_sdk_credential", factory)
    front = _load("deploy_foundry_agent")
    supervision = _load("foundry_supervision")
    monkeypatch.setattr(supervision, "project_client", front.project_client)
    monkeypatch.setattr(supervision, "app_insights_status", Mock(return_value="fixture"))
    monkeypatch.setattr(supervision, "local_truth", Mock(return_value={}))
    monkeypatch.setattr(supervision, "print_truth", Mock())
    monkeypatch.setattr(supervision, "print_report", Mock())
    monkeypatch.setattr(supervision, "_ask", Mock(return_value={
        "answer": "fixture", "error": None, "response_id": "fixture", "latency_s": 0,
    }))
    monkeypatch.setattr(supervision, "check_answer", Mock(return_value=("PASS", [])))
    monkeypatch.setattr(sys, "argv", ["foundry_supervision.py", "--only", "sup-001"])
    writes, directories = [], []
    monkeypatch.setattr(Path, "mkdir", lambda path, **kw: directories.append(path))
    monkeypatch.setattr(Path, "write_text", lambda path, text, **kw: writes.append((path, text)))
    before = context.legacy_config.read_bytes(), context.legacy_state.read_bytes()

    with pytest.raises(SystemExit) as result:
        supervision.main()

    assert result.value.code == 0
    factory.assert_called_once_with()
    assert sdk.constructor.call_args.kwargs["credential"] is factory.return_value
    assert supervision._ask.call_args.args[0] is sdk.client
    assert directories == [supervision.OUT_DIR]
    assert len(writes) == 1
    path, text = writes[0]
    assert path.parent == supervision.OUT_DIR
    assert path.name.startswith("run_") and path.suffix == ".json"
    assert json.loads(text)["agent_name"] == "Marketing-Churn-Front-Door"
    assert (context.legacy_config.read_bytes(), context.legacy_state.read_bytes()) == before
    assert not path.exists()
