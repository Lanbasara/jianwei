"""Deterministic tests for QA classification, selection, and CI safety."""
from __future__ import annotations

import copy
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "qa"))

import policy_check  # noqa: E402
import run as qa_run  # noqa: E402


def write_entry(root: Path, relative: str = "qa/test_registered.py") -> str:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# deterministic fixture\n", encoding="utf-8")
    return relative


def suite(files: list[str], **changes: object) -> dict[str, object]:
    value: dict[str, object] = {
        "id": "policy-meta",
        "kind": "deterministic",
        "execution": "ci",
        "gates": ["pull-request"],
        "network": False,
        "credentials": "none",
        "runner": "policy-meta",
        "files": files,
    }
    value.update(changes)
    return value


def registry(*suites: dict[str, object]) -> dict[str, object]:
    return {"version": 1, "suites": list(suites)}


def capabilities_for(entry: str) -> dict[str, dict[str, object]]:
    capabilities = copy.deepcopy(policy_check.RUNNER_CAPABILITIES)
    capabilities["policy-meta"]["files"] = (entry,)
    return capabilities


def parse_workflow(text: str) -> dict[str, object]:
    """Parse the small, deliberately restricted YAML subset used by ci.yml."""
    tokens: list[tuple[int, str]] = []
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        if "\t" in raw:
            raise AssertionError("workflow may not contain tabs")
        content = raw.split(" #", 1)[0].rstrip()
        indent = len(content) - len(content.lstrip(" "))
        if indent % 2:
            raise AssertionError("workflow indentation must use two spaces")
        tokens.append((indent, content.strip()))

    def scalar(value: str) -> object:
        if value.startswith('"'):
            return json.loads(value)
        if value == "true":
            return True
        if value == "false":
            return False
        if re.fullmatch(r"[0-9]+", value):
            return int(value)
        return value

    def block(index: int, indent: int) -> tuple[object, int]:
        if index >= len(tokens) or tokens[index][0] != indent:
            raise AssertionError("invalid workflow nesting")
        is_list = tokens[index][1].startswith("- ")
        container: object = [] if is_list else {}
        while index < len(tokens) and tokens[index][0] == indent:
            line = tokens[index][1]
            if is_list:
                if not line.startswith("- "):
                    raise AssertionError("mixed workflow sequence and mapping")
                item_text = line[2:]
                if ":" in item_text:
                    key, value = item_text.split(":", 1)
                    item: dict[str, object] = {key: scalar(value.strip()) if value.strip() else {}}
                    index += 1
                    if index < len(tokens) and tokens[index][0] > indent:
                        child, index = block(index, indent + 2)
                        if not isinstance(child, dict):
                            raise AssertionError("step continuation must be a mapping")
                        item.update(child)
                    assert isinstance(container, list)
                    container.append(item)
                else:
                    assert isinstance(container, list)
                    container.append(scalar(item_text))
                    index += 1
            else:
                if line.startswith("- ") or ":" not in line:
                    raise AssertionError("invalid workflow mapping")
                key, value = line.split(":", 1)
                if key in container:
                    raise AssertionError(f"duplicate workflow key: {key}")
                index += 1
                if value.strip():
                    assert isinstance(container, dict)
                    container[key] = scalar(value.strip())
                elif index < len(tokens) and tokens[index][0] > indent:
                    child, index = block(index, indent + 2)
                    assert isinstance(container, dict)
                    container[key] = child
                else:
                    assert isinstance(container, dict)
                    container[key] = {}
        return container, index

    parsed, end = block(0, 0)
    if end != len(tokens) or not isinstance(parsed, dict):
        raise AssertionError("workflow did not parse as one mapping")
    return parsed


def validate_release_workflow(workflow: dict[str, object], text: str) -> None:
    assert "pull_request_target" not in text
    assert "secrets." not in text
    assert set(workflow) == {"name", "on", "permissions", "concurrency", "jobs"}
    assert workflow["name"] == "Source Release"
    assert workflow["on"] == {
        "push": {"tags": ["v*.*.*"]}, "workflow_dispatch": {},
    }
    assert workflow["permissions"] == {"contents": "read"}
    assert workflow["concurrency"] == {
        "group": "source-release-${{ github.ref }}", "cancel-in-progress": False,
    }
    jobs = workflow["jobs"]
    assert set(jobs) == {"qa", "build", "publish"}
    qa = jobs["qa"]
    build = jobs["build"]
    publish = jobs["publish"]
    assert set(qa) == {"runs-on", "timeout-minutes", "permissions", "steps"}
    assert set(build) == {"runs-on", "timeout-minutes", "permissions", "steps"}
    assert set(publish) == {
        "needs", "if", "runs-on", "timeout-minutes", "environment", "permissions", "steps",
    }
    assert qa["runs-on"] == build["runs-on"] == publish["runs-on"] == "ubuntu-latest"
    assert qa["timeout-minutes"] == 30
    assert build["timeout-minutes"] == 15
    assert publish["timeout-minutes"] == 10
    assert qa["permissions"] == {"contents": "read"}
    assert build["permissions"] == {"contents": "read"}
    assert publish["permissions"] == {
        "contents": "write", "id-token": "write", "attestations": "write",
    }
    assert publish["environment"] == "source-release"
    assert publish["needs"] == ["qa", "build"]
    assert publish["if"] == (
        "${{ github.event_name == 'push' && github.ref_type == 'tag' && "
        "vars.JIANWEI_RELEASE_TAG == github.ref_name }}"
    )
    qa_steps = qa["steps"]
    build_steps = build["steps"]
    publish_steps = publish["steps"]
    assert qa_steps == [
        {
            "name": "Check out source",
            "uses": "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1",
            "with": {"persist-credentials": False},
        },
        {
            "name": "Set up Python",
            "uses": "actions/setup-python@6d4fd22b20465ccc0f7354476588e8df7c9ca0e0",
            "with": {
                "python-version": "3.12", "cache": "pip",
                "cache-dependency-path": "tanyuan/requirements.txt",
            },
        },
        {"name": "Install pinned test dependencies",
         "run": "python -m pip install -r tanyuan/requirements.txt"},
        {"name": "Validate QA classification", "run": "python qa/policy_check.py"},
        {"name": "Run release gate", "run": "python qa/run.py --gate release"},
    ]
    assert build_steps == [
        {
            "name": "Check out source history",
            "uses": "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1",
            "with": {"fetch-depth": 0, "persist-credentials": False},
        },
        {
            "name": "Check out Marketplace main as data",
            "uses": "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1",
            "with": {
                "repository": "Lanbasara/jianwei-marketplace", "ref": "main",
                "path": ".release-input/marketplace", "fetch-depth": 1,
                "persist-credentials": False,
            },
        },
        {"name": "Prepare ignored artifact parent", "run": "mkdir dist"},
        {
            "name": "Build deterministic release artifacts",
            "run": "python3 scripts/build_jianwei_release.py --github-context --required-ancestor origin/main --release-repo . --marketplace-repo .release-input/marketplace --out dist/release",
        },
        {
            "name": "Verify artifact checksums", "run": "sha256sum -c SHA256SUMS",
            "working-directory": "dist/release",
        },
        {
            "name": "Upload release and Marketplace candidates",
            "uses": "actions/upload-artifact@cf430e030ddbb5b0abf93d22962f4752f3646cd9",
            "with": {
                "name": "jianwei-source-release-${{ github.sha }}", "path": "dist/release",
                "if-no-files-found": "error", "include-hidden-files": True,
                "retention-days": 30,
            },
        },
    ]
    assert publish_steps == [
        {
            "name": "Download verified build artifact",
            "uses": "actions/download-artifact@9000827ccba6bdab643e8b6fd33ac0654aef8333",
            "with": {
                "name": "jianwei-source-release-${{ github.sha }}", "path": "dist/release",
            },
        },
        {
            "name": "Verify downloaded checksums", "run": "sha256sum -c SHA256SUMS",
            "working-directory": "dist/release",
        },
        {
            "name": "Attest product and candidate provenance",
            "uses": "actions/attest@1e69f48acb82d1966a394da916b4c1698aa569d6",
            "with": {
                "subject-path": (
                    "dist/release/jianwei-plugin.zip, "
                    "dist/release/jianwei-marketplace-candidate.zip, "
                    "dist/release/release.json"
                ),
            },
        },
        {
            "name": "Publish GitHub Release",
            "run": "gh release create \"$GITHUB_REF_NAME\" dist/release/jianwei-plugin.zip dist/release/release.json dist/release/SHA256SUMS --verify-tag --generate-notes --title \"$GITHUB_REF_NAME\"",
            "env": {"GH_TOKEN": "${{ github.token }}"},
        },
    ]
    all_uses = [
        step["uses"] for step in [*qa_steps, *build_steps, *publish_steps] if "uses" in step
    ]
    allowed_actions = {
        "actions/checkout", "actions/setup-python", "actions/upload-artifact",
        "actions/download-artifact", "actions/attest",
    }
    for use in all_uses:
        action, sha = use.split("@", 1)
        assert action in allowed_actions
        assert re.fullmatch(r"[0-9a-f]{40}", sha)
    release_step = publish_steps[-1]
    assert "jianwei-marketplace-candidate.zip" not in release_step["run"]
    assert "--clobber" not in release_step["run"]
    assert "gh release edit" not in release_step["run"]
    assert "gh release upload" not in release_step["run"]
    assert not any("pip" in step.get("run", "") for step in build_steps)
    assert not any("qa/" in step.get("run", "") for step in build_steps)
    assert all("upload-artifact" not in step.get("uses", "") for step in qa_steps)
    assert all("checkout" not in step.get("uses", "") for step in publish_steps)


class PolicyValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.entry = write_entry(self.root)
        self.capabilities = capabilities_for(self.entry)

    def tearDown(self):
        self.temp.cleanup()

    def assert_invalid(self, data: dict[str, object]) -> None:
        with self.assertRaises(policy_check.PolicyError):
            policy_check.validate_registry_data(
                data, self.root, scan=False, capabilities=self.capabilities,
            )

    def test_actual_registry_is_complete_and_offline_live_do_not_overlap(self):
        suites = policy_check.load_registry()
        offline = next(item for item in suites if item["id"] == "tanyuan-offline")
        live = next(item for item in suites if item["id"] == "tanyuan-live")
        self.assertFalse(set(offline["files"]).intersection(live["files"]))
        self.assertGreaterEqual(len(policy_check.scan_entry_points(ROOT)), 18)
        self.assertEqual(policy_check.RUNNERS, set(qa_run.RUNNERS))
        for item in suites:
            self.assertEqual(
                tuple(item["files"]), policy_check.RUNNER_CAPABILITIES[item["runner"]]["files"],
            )
        pull_request = qa_run.select_gate(suites, "pull-request")
        self.assertNotIn("tanyuan-live", {item["runner"] for item in pull_request})
        release_gate = qa_run.select_gate(suites, "release")
        self.assertNotIn("tanyuan-live", {item["runner"] for item in release_gate})
        self.assertNotIn("evidence-local", {item["runner"] for item in release_gate})

    def test_offline_and_live_file_manifests_cannot_be_swapped(self):
        data = policy_check.read_json(policy_check.REGISTRY)
        offline = next(item for item in data["suites"] if item["id"] == "tanyuan-offline")
        live = next(item for item in data["suites"] if item["id"] == "tanyuan-live")
        offline["files"], live["files"] = live["files"], offline["files"]
        with self.assertRaises(policy_check.PolicyError):
            policy_check.validate_registry_data(data, ROOT, scan=False)

    def test_tanyuan_runner_modules_are_derived_from_capability_manifest(self):
        manifest = policy_check.RUNNER_CAPABILITIES["tanyuan-offline"]["files"]
        expected = [qa_run.python_module(path) for path in manifest]
        with patch.object(qa_run, "run_command") as command:
            qa_run.run_tanyuan_offline({})
        argv, passed_env = command.call_args.args
        self.assertEqual(argv, [sys.executable, "-m", "unittest", "-v", *expected])
        self.assertEqual(passed_env, {})

    def test_marketplace_runner_is_derived_from_its_capability_manifest(self):
        manifest = policy_check.RUNNER_CAPABILITIES["marketplace-contract"]["files"]
        test_file = next(path for path in manifest if path.startswith("qa/jianwei/"))
        with patch.object(qa_run, "run_command") as command:
            qa_run.run_marketplace_contract({})
        argv, passed_env = command.call_args.args
        self.assertEqual(
            argv,
            [sys.executable, "-m", "unittest", "-v", qa_run.python_module(test_file)],
        )
        self.assertEqual(passed_env, {})

    def test_release_runner_is_derived_from_its_capability_manifest(self):
        manifest = policy_check.RUNNER_CAPABILITIES["release-artifact"]["files"]
        test_file = next(path for path in manifest if path.startswith("qa/jianwei/"))
        with patch.object(qa_run, "run_command") as command:
            qa_run.run_release_artifact({})
        argv, passed_env = command.call_args.args
        self.assertEqual(
            argv,
            [sys.executable, "-m", "unittest", "-v", qa_run.python_module(test_file)],
        )
        self.assertEqual(passed_env, {})

    def test_ai_cannot_be_ci_or_gated(self):
        self.assert_invalid(registry(suite([self.entry], kind="ai-eval")))
        self.assert_invalid(registry(suite([self.entry], kind="ai-eval", execution="local",
                                                   gates=["pull-request"], credentials="external-local")))

    def test_correct_ai_classification_is_accepted(self):
        data = registry(suite([self.entry], kind="ai-eval", execution="local", gates=[],
                              credentials="external-local"))
        self.assertEqual(policy_check.validate_registry_data(
            data, self.root, scan=False, capabilities=self.capabilities,
        )[0]["kind"],
                         "ai-eval")

    def test_ci_cannot_use_network_or_credentials(self):
        self.assert_invalid(registry(suite([self.entry], network=True)))
        self.assert_invalid(registry(suite([self.entry], credentials="external-local")))
        self.assert_invalid(registry(suite([self.entry], execution="manual", gates=[],
                                                   network=True)))

    def test_unknown_values_and_fields_are_rejected(self):
        for field, value in (("kind", "mystery"), ("execution", "remote"),
                             ("credentials", "secret"), ("gates", ["nightly"]),
                             ("runner", "shell-command"), ("kind", ["deterministic"])):
            with self.subTest(field=field):
                self.assert_invalid(registry(suite([self.entry], **{field: value})))
        extra = suite([self.entry])
        extra["command"] = "echo unsafe"
        self.assert_invalid(registry(extra))

    def test_duplicate_ids_and_file_overlap_are_rejected(self):
        duplicate = copy.deepcopy(suite([self.entry]))
        self.assert_invalid(registry(suite([self.entry]), duplicate))

        second = copy.deepcopy(suite([self.entry]))
        second["id"] = "judge-selftest"
        second["runner"] = "judge-selftest"
        self.assert_invalid(registry(suite([self.entry]), second))

    def test_runner_identity_cannot_be_swapped_between_offline_and_live(self):
        offline = suite([self.entry], id="tanyuan-offline", runner="tanyuan-live")
        self.assert_invalid(registry(offline))
        live = suite([self.entry], id="tanyuan-live", runner="tanyuan-offline",
                     kind="live", execution="manual", gates=[], network=True,
                     credentials="external-local")
        self.assert_invalid(registry(live))

    def test_unregistered_entry_point_is_rejected(self):
        write_entry(self.root, "qa/test_orphan.py")
        with self.assertRaises(policy_check.PolicyError):
            policy_check.validate_registry_data(
                registry(suite([self.entry])), self.root, scan=True,
                capabilities=self.capabilities,
            )

    def test_unregistered_eval_and_score_names_cannot_bypass_scan(self):
        for name in (
                "qa/new/eval_model.py", "qa/new/score_model.py",
                "qa/new/eval_model.sh", "qa/new/score_model.js",
        ):
            with self.subTest(name=name):
                with tempfile.TemporaryDirectory() as folder:
                    root = Path(folder)
                    entry = write_entry(root)
                    write_entry(root, name)
                    with self.assertRaises(policy_check.PolicyError):
                        policy_check.validate_registry_data(
                            registry(suite([entry])), root, scan=True,
                            capabilities=capabilities_for(entry),
                        )

    def test_executable_unknown_extension_cannot_bypass_scan(self):
        script = write_entry(self.root, "qa/new/model_check.tool")
        (self.root / script).chmod(0o700)
        with self.assertRaises(policy_check.PolicyError):
            policy_check.validate_registry_data(
                registry(suite([self.entry])), self.root, scan=True,
                capabilities=self.capabilities,
            )

    def test_traversal_absolute_and_glob_paths_are_rejected(self):
        for value in ("../outside.py", "/absolute/test.py", "qa/test_*.py"):
            with self.subTest(value=value):
                self.assert_invalid(registry(suite([value])))


class SelectionTests(unittest.TestCase):
    def test_gate_selects_only_ci_and_never_local_or_manual(self):
        suites = [
            {"id": "ci", "execution": "ci", "gates": ["pull-request"],
             "network": False, "credentials": "none"},
            {"id": "local", "execution": "local", "gates": [],
             "network": False, "credentials": "external-local"},
            {"id": "manual", "execution": "manual", "gates": [],
             "network": True, "credentials": "external-local"},
        ]
        self.assertEqual([item["id"] for item in qa_run.select_gate(suites, "pull-request")],
                         ["ci"])

    def test_release_gate_selects_only_offline_ci(self):
        suites = policy_check.load_registry()
        selected = qa_run.select_gate(suites, "release")
        self.assertIn("release-artifact", {item["id"] for item in selected})
        self.assertTrue(all(item["execution"] == "ci" for item in selected))
        self.assertTrue(all(not item["network"] for item in selected))
        self.assertTrue(all(item["credentials"] == "none" for item in selected))

    def test_gate_environment_is_minimal_and_isolates_home_credentials(self):
        with tempfile.TemporaryDirectory() as source_folder:
            source_home = Path(source_folder)
            (source_home / ".netrc").write_text("machine example.invalid password secret\n",
                                                 encoding="utf-8")
            source = {
                "PATH": "/bin", "LANG": "C.UTF-8", "HOME": str(source_home),
                "PASSWORD": "secret", "SSH_AUTH_SOCK": "/private/agent",
                "GOOGLE_APPLICATION_CREDENTIALS": "/private/google.json",
                "NETRC": str(source_home / ".netrc"), "UNKNOWN_VARIABLE": "secret",
                "OPENAI_API_KEY": "secret", "JIANWEI_QA_HOME": "/private/evidence",
            }
            with qa_run.gate_environment(source) as clean:
                isolated_root = Path(clean["HOME"]).parent
                self.assertEqual(clean["PATH"], "/bin")
                self.assertEqual(clean["LANG"], "C.UTF-8")
                self.assertEqual(clean["PYTHONDONTWRITEBYTECODE"], "1")
                self.assertEqual(clean["PYTHONPATH"], str(qa_run.NO_NETWORK_PATH))
                self.assertNotEqual(Path(clean["HOME"]), source_home)
                self.assertFalse((Path(clean["HOME"]) / ".netrc").exists())
                for name in (
                        "PASSWORD", "SSH_AUTH_SOCK", "GOOGLE_APPLICATION_CREDENTIALS",
                        "NETRC", "UNKNOWN_VARIABLE", "OPENAI_API_KEY", "JIANWEI_QA_HOME",
                ):
                    self.assertNotIn(name, clean)
                child = subprocess.run(
                    [sys.executable, "-c", (
                        "import os,pathlib;"
                        "assert not (pathlib.Path.home()/'.netrc').exists();"
                        "assert all(k not in os.environ for k in "
                        "('PASSWORD','SSH_AUTH_SOCK','GOOGLE_APPLICATION_CREDENTIALS',"
                        "'NETRC','UNKNOWN_VARIABLE','OPENAI_API_KEY','JIANWEI_QA_HOME'))"
                    )],
                    cwd=ROOT, env=clean, check=False, shell=False,
                    capture_output=True, text=True, timeout=30,
                )
                self.assertEqual(child.returncode, 0, child.stderr)
            self.assertFalse(isolated_root.exists())

    def test_volta_locator_is_visible_only_to_fixed_node_child(self):
        with qa_run.gate_environment({
                "PATH": "/tools/bin", "VOLTA_HOME": "/caller/.volta",
        }) as gate_env:
            self.assertNotIn("VOLTA_HOME", gate_env)
            base = dict(gate_env)
        with patch.object(qa_run.shutil, "which", return_value="/tools/.volta/bin/node"):
            child = qa_run.node_child_environment(base)
        self.assertNotIn("VOLTA_HOME", base)
        self.assertEqual(child["VOLTA_HOME"], "/tools/.volta")

    def test_credentials_none_suite_scrubs_secrets_before_runner(self):
        captured: list[dict[str, str]] = []
        item = {"id": "jianwei-unit", "runner": "jianwei-unit", "execution": "ci",
                "kind": "build-contract", "network": False, "credentials": "none"}
        with qa_run.suite_environment(
                item, {"PATH": "/bin", "OPENAI_API_KEY": "secret"},
        ) as env, patch.dict(qa_run.RUNNERS, {"jianwei-unit": captured.append}, clear=False):
            qa_run.execute_suite(item, env, False)
        self.assertEqual(len(captured), 1)
        self.assertNotIn("OPENAI_API_KEY", captured[0])
        self.assertEqual(captured[0]["PYTHONPATH"], str(qa_run.NO_NETWORK_PATH))

    def test_external_local_offline_suite_keeps_credentials_but_forces_guard(self):
        item = {"network": False, "credentials": "external-local"}
        with qa_run.suite_environment(item, {
                "OPENAI_API_KEY": "local-secret",
                "JIANWEI_QA_HOME": "/local/evidence",
                "PYTHONPATH": "/untrusted/caller/path",
        }) as env:
            self.assertEqual(env["OPENAI_API_KEY"], "local-secret")
            self.assertEqual(env["JIANWEI_QA_HOME"], "/local/evidence")
            self.assertEqual(env["PYTHONPATH"], str(qa_run.NO_NETWORK_PATH))

    def test_external_local_network_suite_keeps_credentials_without_offline_guard(self):
        item = {"network": True, "credentials": "external-local"}
        with qa_run.suite_environment(item, {
                "OPENAI_API_KEY": "local-secret", "PYTHONPATH": "/caller/path",
        }) as env:
            self.assertEqual(env["OPENAI_API_KEY"], "local-secret")
            self.assertEqual(env["PYTHONPATH"], "/caller/path")

    def test_python_offline_guard_blocks_ip_and_preserves_local_operations(self):
        program = r'''
import pathlib
import socket

left, right = socket.socketpair()
left.sendall(b"ok")
assert right.recv(2) == b"ok"
left.close()
right.close()
assert pathlib.Path("qa/suites.json").is_file()

attempts = [
    lambda: socket.getaddrinfo("localhost", 80),
    lambda: socket.create_connection(("127.0.0.1", 9), timeout=0.01),
    lambda: socket.socket(socket.AF_INET, socket.SOCK_STREAM).connect_ex(("127.0.0.1", 9)),
]
for attempt in attempts:
    try:
        attempt()
    except PermissionError:
        pass
    else:
        raise SystemExit("network operation was not blocked")
'''
        with qa_run.gate_environment({"PATH": str(Path(sys.executable).parent)}) as env:
            result = subprocess.run(
                [sys.executable, "-c", program], cwd=ROOT, env=env,
                check=False, shell=False, capture_output=True, text=True, timeout=30,
            )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_live_requires_explicit_network_authorization_without_running(self):
        live = {"network": True}
        with self.assertRaises(qa_run.RunError):
            qa_run.ensure_network_authorized(live, False)
        qa_run.ensure_network_authorized(live, True)
        with self.assertRaises(qa_run.RunError):
            qa_run.ensure_network_authorized({"network": False}, True)


class WorkflowTests(unittest.TestCase):
    def test_workflow_structure_is_strictly_offline_and_pinned(self):
        text = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        self.assertNotIn("pull_request_target", text)
        self.assertNotIn("secrets.", text)
        self.assertNotIn("--allow-network", text)
        workflow = parse_workflow(text)
        self.assertEqual(set(workflow), {"name", "on", "permissions", "concurrency", "jobs"})
        self.assertEqual(workflow["name"], "Offline QA")
        self.assertEqual(workflow["on"], {
            "pull_request": {}, "push": {"branches": ["main"]},
        })
        self.assertEqual(workflow["permissions"], {"contents": "read"})
        self.assertEqual(workflow["concurrency"], {
            "group": "offline-qa-${{ github.workflow }}-${{ github.ref }}",
            "cancel-in-progress": True,
        })
        self.assertEqual(set(workflow["jobs"]), {"qa"})
        job = workflow["jobs"]["qa"]
        self.assertEqual(set(job), {
            "runs-on", "timeout-minutes", "permissions", "strategy", "steps",
        })
        self.assertEqual(job["permissions"], {"contents": "read"})
        self.assertEqual(job["runs-on"], "ubuntu-latest")
        self.assertEqual(job["timeout-minutes"], 30)
        self.assertEqual(job["strategy"], {
            "fail-fast": False,
            "matrix": {"python-version": ["3.10", "3.12"]},
        })
        steps = job["steps"]
        self.assertEqual(steps, [
            {
                "name": "Check out source",
                "uses": "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1",
                "with": {"persist-credentials": False},
            },
            {
                "name": "Set up Python",
                "uses": "actions/setup-python@6d4fd22b20465ccc0f7354476588e8df7c9ca0e0",
                "with": {
                    "python-version": "${{ matrix.python-version }}",
                    "cache": "pip",
                    "cache-dependency-path": "tanyuan/requirements.txt",
                },
            },
            {"name": "Install pinned test dependencies",
             "run": "python -m pip install -r tanyuan/requirements.txt"},
            {"name": "Validate QA classification", "run": "python qa/policy_check.py"},
            {"name": "Run pull-request gate", "run": "python qa/run.py --gate pull-request"},
        ])
        uses = [step["uses"] for step in steps if "uses" in step]
        self.assertEqual(uses, [
            "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1",
            "actions/setup-python@6d4fd22b20465ccc0f7354476588e8df7c9ca0e0",
        ])
        for use in uses:
            action, sha = use.split("@", 1)
            self.assertIn(action, {"actions/checkout", "actions/setup-python"})
            self.assertRegex(sha, r"\A[0-9a-f]{40}\Z")
        self.assertEqual([step["run"] for step in steps if "run" in step], [
            "python -m pip install -r tanyuan/requirements.txt",
            "python qa/policy_check.py",
            "python qa/run.py --gate pull-request",
        ])
        self.assertIn("@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1", text)
        self.assertIn("@6d4fd22b20465ccc0f7354476588e8df7c9ca0e0 # v7.1.0", text)

    def test_release_workflow_is_tag_guarded_pinned_and_least_privilege(self):
        text = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
        validate_release_workflow(parse_workflow(text), text)

    def test_release_workflow_security_mutations_are_rejected(self):
        text = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
        workflow = parse_workflow(text)
        mutations = []
        broad_qa = copy.deepcopy(workflow)
        broad_qa["jobs"]["qa"]["permissions"] = {"contents": "write"}
        mutations.append(broad_qa)
        dispatch_publish = copy.deepcopy(workflow)
        dispatch_publish["jobs"]["publish"]["if"] = "${{ github.event_name == 'workflow_dispatch' }}"
        mutations.append(dispatch_publish)
        unpinned = copy.deepcopy(workflow)
        unpinned["jobs"]["build"]["steps"][0]["uses"] = "actions/checkout@main"
        mutations.append(unpinned)
        leaked_marketplace = copy.deepcopy(workflow)
        leaked_marketplace["jobs"]["publish"]["steps"][-1]["run"] += (
            " dist/release/jianwei-marketplace-candidate.zip"
        )
        mutations.append(leaked_marketplace)
        ignored_gate_failure = copy.deepcopy(workflow)
        release_gate = next(
            step for step in ignored_gate_failure["jobs"]["qa"]["steps"]
            if step["name"] == "Run release gate"
        )
        release_gate["continue-on-error"] = True
        mutations.append(ignored_gate_failure)
        skipped_checksum = copy.deepcopy(workflow)
        checksum = next(
            step for step in skipped_checksum["jobs"]["build"]["steps"]
            if step["name"] == "Verify artifact checksums"
        )
        checksum["if"] = False
        mutations.append(skipped_checksum)
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                with self.assertRaises(AssertionError):
                    validate_release_workflow(mutation, text)

    def test_only_fixed_build_runner_has_a_node_capability(self):
        node_capabilities = {
            name for name, capability in policy_check.RUNNER_CAPABILITIES.items()
            if capability["runtime"] == "fixed-python-node"
        }
        self.assertEqual(node_capabilities, {"build-contract"})


if __name__ == "__main__":
    unittest.main()
