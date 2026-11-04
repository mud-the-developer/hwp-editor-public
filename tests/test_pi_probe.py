"""Pi SDK probe contract tests. Fake SDK/process only; never a model or real profile."""

import importlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
p = importlib.import_module("hwp_pi_probe")


def reply(request):
    return {
        "type": "sdk_probe_result",
        "id": request["id"],
        "nonce": request["nonce"],
        "success": True,
        "stage": "passed",
        "sdk_version": "1.2.0",
        "node_version": "24.0.0",
        "tools": [],
        "message_count": 0,
        "streaming": False,
        "session_file": None,
        "attempts": {"network": 0, "process": 0, "model": 0},
        "authenticated": False,
        "connected": False,
    }


class FakeProcess:
    def __init__(self, transform=lambda data: data, code=0, hang=False):
        read_fd, self.write_fd = os.pipe()
        self.stdout = os.fdopen(read_fd, "rb")
        self.stderr = io.BytesIO()
        self.returncode = None
        self.terminated = False
        self.hang = hang
        self.code = code
        parent = self

        class Sink(io.BytesIO):
            def close(self):
                if self.closed:
                    return
                request = json.loads(self.getvalue().split(b"\n")[0])
                data = transform(reply(request))
                blob = (
                    data
                    if isinstance(data, bytes)
                    else json.dumps(data, ensure_ascii=False).encode() + b"\n"
                )
                try:
                    os.write(parent.write_fd, blob)
                finally:
                    try:
                        os.close(parent.write_fd)
                    except OSError:
                        pass
                if not parent.hang:
                    parent.returncode = parent.code
                super().close()

        self.stdin = Sink()

    def poll(self):
        return self.returncode

    def wait(self, timeout: float = 1):
        if self.returncode is None:
            raise subprocess.TimeoutExpired("SYNTHETIC OWNED NODE", timeout)
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = -15
        try:
            os.close(self.write_fd)
        except OSError:
            pass

    def kill(self):
        self.terminate()


class ProbeTests(unittest.TestCase):
    def setUp(self):
        self.runtime = {
            "cli": "SYNTHETIC/pi.cmd",
            "node": "SYNTHETIC/node.exe",
            "helper": str(ROOT / "scripts/pi_probe.mjs"),
            "sdk_entry": "SYNTHETIC/index.mjs",
            "sdk_version": "1.2.0",
            "sha256": "a" * 64,
        }
        patcher = patch.object(p, "resolve_runtime", return_value=self.runtime)
        patcher.start()
        self.addCleanup(patcher.stop)
        denial = patch.object(
            p.subprocess,
            "Popen",
            side_effect=AssertionError("No implicit process launch"),
        )
        denial.start()
        self.addCleanup(denial.stop)

    def test_codec_binds_id_nonce_exit_independent_and_unicode_is_not_a_line_boundary(
        self,
    ):
        request = {"type": "sdk_probe", "id": "p-" + "a" * 16, "nonce": "b" * 32}
        data = reply(request)
        data["node_version"] = "24\u2028separator\u2029test"
        self.assertTrue(
            p.decode_response(
                json.dumps(data, ensure_ascii=False).encode() + b"\n", request
            )["transport_verified"]
        )
        bad = [
            b"not JSON\n",
            json.dumps(data).encode(),
            json.dumps(data).encode() + b"\n{}\n",
        ]
        for field, value in (
            ("id", "wrong"),
            ("nonce", "wrong"),
            ("success", False),
            ("sdk_version", "2.0.0"),
            ("tools", ["bash"]),
            ("message_count", True),
            ("streaming", True),
            ("session_file", "REAL SESSION"),
            ("authenticated", True),
            ("connected", True),
            ("attempts", {"network": 0, "process": 1, "model": 0}),
        ):
            bad.append(json.dumps({**data, field: value}).encode() + b"\n")
        bad.append(json.dumps({**data, "prompt": "DO NOT SEND"}).encode() + b"\n")
        for blob in bad:
            with self.subTest(blob_length=len(blob)), self.assertRaises(p.ProbeFailure):
                p.decode_response(blob, request)

    def test_parent_uses_only_fixed_request_owned_sandbox_and_allowlisted_environment(
        self,
    ):
        process = FakeProcess()
        with (
            patch.object(p.subprocess, "Popen", return_value=process) as launch,
            patch.dict(
                os.environ,
                {
                    "OPENAI_API_KEY": "DO-NOT-PASS",
                    "NODE_OPTIONS": "DO-NOT-PASS",
                    "PI_MANAGED_INSTALL_ROOT": "DO-NOT-PASS",
                },
            ),
        ):
            result = p.run_probe(self.runtime, threading.Event())
        args, kwargs = launch.call_args
        self.assertEqual(
            args[0],
            [self.runtime["node"], self.runtime["helper"], self.runtime["sdk_entry"]],
        )
        self.assertNotIn("shell", kwargs)
        self.assertEqual(kwargs["stdin"], subprocess.PIPE)
        self.assertNotIn("OPENAI_API_KEY", kwargs["env"])
        self.assertNotIn("NODE_OPTIONS", kwargs["env"])
        self.assertNotIn("PI_MANAGED_INSTALL_ROOT", kwargs["env"])
        self.assertEqual(kwargs["env"]["HOME"], str(kwargs["cwd"]))
        self.assertEqual(
            kwargs["env"]["PI_CODING_AGENT_DIR"], str(kwargs["cwd"] / "profile")
        )
        self.assertFalse(Path(kwargs["cwd"]).exists())
        self.assertTrue(result["process_exited"])
        self.assertFalse(result["authenticated"])
        self.assertFalse(result["model_called"])
        self.assertFalse(result["document_shared"])

    def test_probe_version_must_match_the_prepared_runtime_not_just_the_allowlist(self):
        self.runtime["sdk_version"] = "1.3.0"
        process = FakeProcess(transform=lambda data: {**data, "sdk_version": "1.3.0"})
        with patch.object(p.subprocess, "Popen", return_value=process):
            self.assertEqual(
                p.run_probe(self.runtime, threading.Event())["sdk_version"], "1.3.0"
            )
        process = FakeProcess()
        with patch.object(p.subprocess, "Popen", return_value=process):
            with self.assertRaises(p.ProbeFailure) as failure:
                p.run_probe(self.runtime, threading.Event())
        self.assertEqual(failure.exception.stage, "runtime_version")

    def test_changed_runtime_or_pre_cancel_never_launches(self):
        with (
            patch.object(
                p, "resolve_runtime", return_value={**self.runtime, "sha256": "changed"}
            ),
            self.assertRaises(p.ProbeFailure),
        ):
            p.run_probe(self.runtime, threading.Event())
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(p.ProbeFailure):
            p.run_probe(self.runtime, cancel)
        p.subprocess.Popen.assert_not_called()

    def test_success_reply_does_not_override_nonzero_process_exit(self):
        process = FakeProcess(code=7)
        with (
            patch.object(p.subprocess, "Popen", return_value=process),
            self.assertRaises(p.ProbeFailure) as failure,
        ):
            p.run_probe(self.runtime, threading.Event())
        self.assertEqual(failure.exception.stage, "process_exit")

    def test_timeout_stops_only_the_owned_probe_and_no_retry(self):
        process = FakeProcess(hang=True)
        with (
            patch.object(p.subprocess, "Popen", return_value=process) as launch,
            patch.object(p, "TIMEOUT", 0.01),
            self.assertRaises(p.ProbeFailure) as failure,
        ):
            p.run_probe(self.runtime, threading.Event())
        self.assertEqual(failure.exception.stage, "timeout")
        self.assertTrue(process.terminated)
        launch.assert_called_once()

    def test_output_is_bounded_and_unbound_stderr_is_not_a_success_reply(self):
        process = FakeProcess(transform=lambda _: b"X" * (p.OUTPUT_LIMIT + 4096))
        with (
            patch.object(p.subprocess, "Popen", return_value=process),
            self.assertRaises(p.ProbeFailure),
        ):
            p.run_probe(self.runtime, threading.Event())
        process = FakeProcess(transform=lambda _: b"")
        process.stderr = io.BytesIO(b"PRIVATE RAW DIAGNOSTIC")
        with (
            patch.object(p.subprocess, "Popen", return_value=process),
            self.assertRaises(p.ProbeFailure) as failure,
        ):
            p.run_probe(self.runtime, threading.Event())
        self.assertNotIn("PRIVATE", str(failure.exception))

    def test_prepare_does_not_execute_and_execution_needs_exact_receipt_and_fresh_discovery(
        self,
    ):
        discovery = Mock(
            snapshot=Mock(
                return_value={
                    "status": "finished",
                    "generation": 1,
                    "agents": [
                        {"id": "pi", "status": "detected", "path": self.runtime["cli"]}
                    ],
                }
            )
        )
        check = p.PiProbe(discovery)
        self.addCleanup(check.close)
        with patch.object(p, "run_probe") as run:
            check.prepare()
            if check.thread is not None:
                check.thread.join(5)
            state = check.snapshot()
            self.assertEqual(state["status"], "prepared")
            run.assert_not_called()
            exact = {
                "probe_id": state["probe_id"],
                "runtime_sha256": state["runtime"]["sha256"],
                "execute_confirmed": True,
            }
            for payload in (
                {},
                {**exact, "probe_id": "wrong"},
                {**exact, "runtime_sha256": "wrong"},
                {**exact, "execute_confirmed": 1},
                {**exact, "prompt": "BLOCK"},
            ):
                with self.assertRaises(ValueError):
                    check.start(payload)
            discovery.snapshot.return_value["generation"] = 2
            with self.assertRaises(ValueError):
                check.start(exact)
            run.assert_not_called()
            discovery.snapshot.return_value["generation"] = 1
        with patch.object(
            p,
            "run_probe",
            return_value={"transport_verified": True, "model_called": False},
        ) as run:
            check.start(exact)
            if check.thread is not None:
                check.thread.join(5)
            self.assertEqual(check.snapshot()["status"], "passed")
            run.assert_called_once()
            with self.assertRaises(ValueError):
                check.start(exact)
        p.subprocess.Popen.assert_not_called()


@unittest.skipUnless(os.name == "nt", "Managed Windows Pi layout only; no CLI fallback")
class ResolverTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        agent = self.root / "agent"
        self.cli = agent / "bin/pi.cmd"
        self.cli.parent.mkdir(parents=True)
        self.cli.write_text(
            '@ECHO off\nnode "%~dp0pi-launcher.js" %*\n', encoding="utf-8"
        )
        (self.cli.parent / "pi-launcher.js").write_text(
            "SYNTHETIC DO NOT RUN", encoding="utf-8"
        )
        self.current = agent / "install/current-version"
        self.current.parent.mkdir(parents=True)
        self.current.write_text("1.2.0", encoding="utf-8")
        self.package = (
            agent
            / "install/releases/1.2.0/node_modules/@earendil-works/pi-coding-agent"
        )
        (self.package / "dist").mkdir(parents=True)
        self.metadata = self.package / "package.json"
        self.metadata.write_text(
            json.dumps(
                {
                    "name": "@earendil-works/pi-coding-agent",
                    "version": "1.2.0",
                    "main": "./dist/index.js",
                }
            ),
            encoding="utf-8",
        )
        (self.package / "dist/index.js").write_text(
            "SYNTHETIC SDK DO NOT RUN", encoding="utf-8"
        )
        self.node = self.root / "node-bin/node.exe"
        self.node.parent.mkdir()
        self.node.write_bytes(b"MZ SYNTHETIC DO NOT RUN")
        (agent / "auth.json").write_text("PRIVATE AUTH DO NOT READ", encoding="utf-8")
        env = patch.dict(os.environ, {"PATH": str(self.node.parent)})
        env.start()
        self.addCleanup(env.stop)
        deny = patch.object(
            p.subprocess, "Popen", side_effect=AssertionError("Resolver cannot launch")
        )
        deny.start()
        self.addCleanup(deny.stop)

    def test_resolver_reads_only_installation_metadata_and_binds_node_sdk_helper_hashes(
        self,
    ):
        original = Path.open

        def guard(path, *args, **kwargs):
            if path.name == "auth.json":
                raise AssertionError("Owner credentials forbidden")
            return original(path, *args, **kwargs)

        with patch.object(Path, "open", guard):
            runtime = p.resolve_runtime(self.cli)
        self.assertEqual(runtime["sdk_version"], "1.2.0")
        self.assertEqual(runtime["node"], str(self.node.resolve()))
        self.assertEqual(len(runtime["files_sha256"]), 7)
        self.assertEqual(runtime["sha256"], p.resolve_runtime(self.cli)["sha256"])
        self.node.write_bytes(b"MZ CHANGED")
        self.assertNotEqual(runtime["sha256"], p.resolve_runtime(self.cli)["sha256"])
        p.subprocess.Popen.assert_not_called()

    def test_13_node_and_literal_bun_shims_bind_metadata_without_executing_or_auth(
        self,
    ):
        package = (
            self.cli.parent.parent
            / "install/releases/1.3.0/node_modules/@earendil-works/pi-coding-agent"
        )
        shutil.copytree(self.package, package)
        metadata = json.loads((package / "package.json").read_text(encoding="utf-8"))
        metadata["version"] = "1.3.0"
        (package / "package.json").write_text(json.dumps(metadata), encoding="utf-8")
        self.current.write_text("1.3.0", encoding="utf-8")
        original = Path.open

        def guard(path, *args, **kwargs):
            if path.name == "auth.json":
                raise AssertionError("Owner credentials forbidden")
            return original(path, *args, **kwargs)

        for shim in (
            '@ECHO off\nnode "%~dp0pi-launcher.js" %*\n',
            '@ECHO off\nbun --smol --no-env-file --no-install --config=NUL "%~dp0pi-launcher.js" %*\n',
        ):
            with self.subTest(shim=shim), patch.object(Path, "open", guard):
                self.cli.write_text(shim, encoding="utf-8")
                runtime = p.resolve_runtime(self.cli)
                self.assertEqual(runtime["sdk_version"], "1.3.0")
                self.assertEqual(
                    runtime["sdk_entry"], str((package / "dist/index.js").resolve())
                )
                self.assertEqual(runtime["node"], str(self.node.resolve()))
                self.assertEqual(len(runtime["files_sha256"]), 7)
        p.subprocess.Popen.assert_not_called()

    def test_14_hardened_bun_install_binds_selected_release_without_profile_or_cli(self):
        package = (
            self.cli.parent.parent
            / "install/releases/1.4.0/node_modules/@earendil-works/pi-coding-agent"
        )
        shutil.copytree(self.package, package)
        metadata = json.loads((package / "package.json").read_text(encoding="utf-8"))
        metadata["version"] = "1.4.0"
        (package / "package.json").write_text(json.dumps(metadata), encoding="utf-8")
        self.current.write_text("1.4.0", encoding="utf-8")
        shim = r'''@ECHO off
SETLOCAL DisableDelayedExpansion
SET "ERRORLEVEL="
SET "BUN_EXE=%USERPROFILE%\.bun\bin\bun.exe"
IF NOT EXIST "%BUN_EXE%" (
  ECHO Pi requires Bun at "%BUN_EXE%". 1>&2
  EXIT /B 1
)
"%BUN_EXE%" --smol --no-env-file --no-install --config=NUL "%~dp0pi-launcher.js" %*
ENDLOCAL & EXIT /B %ERRORLEVEL%'''
        self.cli.write_text(shim, encoding="utf-8")
        original = Path.open

        def guard(path, *args, **kwargs):
            if path.name in ("auth.json", "settings.json", "models.json"):
                raise AssertionError("User profile access is not installation metadata")
            return original(path, *args, **kwargs)

        with patch.object(Path, "open", guard):
            runtime = p.resolve_runtime(self.cli)
            self.assertEqual(runtime["sdk_version"], "1.4.0")
            self.assertEqual(
                runtime["sdk_entry"], str((package / "dist/index.js").resolve())
            )
            for changed in (
                shim.replace("--no-env-file ", ""),
                shim + "\nRUN SOMETHING ELSE",
            ):
                self.cli.write_text(changed, encoding="utf-8")
                with self.assertRaises(p.ProbeFailure) as failure:
                    p.resolve_runtime(self.cli)
                self.assertEqual(failure.exception.stage, "unsupported_layout")
            self.cli.write_text(shim, encoding="utf-8")
            metadata["version"] = "1.3.0"
            (package / "package.json").write_text(json.dumps(metadata), encoding="utf-8")
            with self.assertRaises(p.ProbeFailure) as failure:
                p.resolve_runtime(self.cli)
            self.assertEqual(failure.exception.stage, "unsupported_version")
        p.subprocess.Popen.assert_not_called()

    def test_13_selected_release_cannot_fall_back_to_existing_12_or_mismatched_metadata(
        self,
    ):
        self.current.write_text("1.3.0", encoding="utf-8")
        with self.assertRaises(FileNotFoundError):
            p.resolve_runtime(self.cli)
        package = (
            self.cli.parent.parent
            / "install/releases/1.3.0/node_modules/@earendil-works/pi-coding-agent"
        )
        shutil.copytree(self.package, package)
        with self.assertRaises(p.ProbeFailure) as failure:
            p.resolve_runtime(self.cli)
        self.assertEqual(failure.exception.stage, "unsupported_version")
        p.subprocess.Popen.assert_not_called()

    def test_metadata_symlinks_are_rejected_before_their_content_is_read(self):
        original_link = Path.is_symlink
        original_open = Path.open
        for blocked in (self.current, self.metadata):

            def link(path):
                return path == blocked or original_link(path)

            def guarded_open(path, *args, **kwargs):
                if path == blocked:
                    raise AssertionError("Metadata symlink content must not be read")
                return original_open(path, *args, **kwargs)

            with (
                self.subTest(path=blocked.name),
                patch.object(Path, "is_symlink", link),
                patch.object(Path, "open", guarded_open),
            ):
                with self.assertRaises(p.ProbeFailure) as failure:
                    p.resolve_runtime(self.cli)
                self.assertEqual(failure.exception.stage, "runtime_binding")
        p.subprocess.Popen.assert_not_called()

    def test_bun_shim_must_match_the_exact_metadata_only_launcher_form(self):
        self.cli.write_text(
            '@ECHO off\nbun "%~dp0pi-launcher.js" %*\n', encoding="utf-8"
        )
        with self.assertRaises(p.ProbeFailure) as failure:
            p.resolve_runtime(self.cli)
        self.assertEqual(failure.exception.stage, "unsupported_layout")
        p.subprocess.Popen.assert_not_called()

    def test_unsupported_wrapper_or_version_never_falls_back_to_cli(self):
        self.current.write_text("9.9.9", encoding="utf-8")
        with self.assertRaises(p.ProbeFailure):
            p.resolve_runtime(self.cli)
        self.current.write_text("1.2.0", encoding="utf-8")
        self.cli.write_text("RUN SOMETHING ELSE", encoding="utf-8")
        with self.assertRaises(p.ProbeFailure):
            p.resolve_runtime(self.cli)
        p.subprocess.Popen.assert_not_called()


FAKE_SDK = """
export const VERSION='1.2.0';
export const ModelRuntime={create:async(options)=>{if(options.allowModelNetwork!==false||options.refreshOnCreate!==false||options.modelsPath!==null)throw Error('not isolated');GUARD;return {}}};
export const SessionManager={inMemory:(cwd)=>({getSessionFile:()=>undefined})};
export const SettingsManager={inMemory:(settings)=>{if(settings.retry.enabled||settings.compaction.enabled)throw Error('bad settings');return settings}};
export const createExtensionRuntime=()=>({});
export async function createAgentSession(options){if(options.noTools!=='all'||options.tools.length||options.customTools.length||options.resourceLoader.getAgentsFiles().agentsFiles.length||options.resourceLoader.getExtensions().extensions.length)throw Error('tools/resources enabled');return {session:{messages:[],isStreaming:false,getActiveToolNames:()=>[],dispose:()=>{}}}}
"""


class HelperTests(unittest.TestCase):
    def test_fake_13_probe_reports_actual_version_and_blocks_new_deferred_model_api(
        self,
    ):
        self._probe_deferred_boundary("1.3.0")

    def test_fake_14_probe_keeps_exact_version_and_deferred_model_denial(self):
        self._probe_deferred_boundary("1.4.0")

    def _probe_deferred_boundary(self, version):
        node = shutil.which("node")
        if node is None:
            self.skipTest("No Node; no automatic install")
        request = {"type": "sdk_probe", "id": "p-" + "a" * 16, "nonce": "b" * 32}
        with tempfile.TemporaryDirectory(prefix="HWP-FAKE-Pi13-Probe-") as temp:
            root = Path(temp)
            sdk = root / "fake-sdk.mjs"
            source = FAKE_SDK.replace("VERSION='1.2.0'", f"VERSION='{version}'").replace(
                "GUARD", ""
            )
            for denied in (False, True):
                code = (
                    source.replace(
                        "createAgentSession(options){",
                        "createAgentSession(options){await options.modelRuntime.cancelDeferred('SYNTHETIC');",
                    )
                    if denied
                    else source
                )
                sdk.write_text(code, encoding="utf-8")
                result = subprocess.run(
                    [node, str(ROOT / "scripts/pi_probe.mjs"), str(sdk)],
                    input=json.dumps(request).encode() + b"\n",
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    cwd=root,
                    env=p.child_environment(root, node),
                    timeout=30,
                )
                data = json.loads(result.stdout)
                self.assertEqual(data["sdk_version"], version)
                if denied:
                    self.assertNotEqual(result.returncode, 0)
                    self.assertFalse(data["success"])
                    self.assertEqual(data["attempts"]["model"], 1)
                else:
                    self.assertEqual(result.returncode, 0)
                    self.assertEqual(
                        p.decode_response(result.stdout, request)["sdk_version"],
                        version,
                    )

    def test_real_node_helper_with_fake_sdk_blocks_model_network_and_process_attempts(
        self,
    ):
        node = shutil.which("node")
        if node is None:
            self.skipTest("Node is not installed; no automatic installation")
        helper = ROOT / "scripts/pi_probe.mjs"
        request = {"type": "sdk_probe", "id": "p-" + "a" * 16, "nonce": "b" * 32}
        with tempfile.TemporaryDirectory(prefix="HWP-SYNTHETIC-Pi-") as temp:
            root = Path(temp)
            for guard in (
                "",
                "await fetch('https://example.invalid')",
                "(await import('node:child_process')).spawn('DO-NOT-RUN')",
                "setTimeout(()=>{try{fetch('https://example.invalid')}catch{}},10)",
            ):
                with self.subTest(guard=guard):
                    sdk = root / "fake-sdk.mjs"
                    sdk.write_text(FAKE_SDK.replace("GUARD", guard), encoding="utf-8")
                    result = subprocess.run(
                        [node, str(helper), str(sdk)],
                        input=json.dumps(request).encode() + b"\n",
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        cwd=root,
                        env=p.child_environment(root, node),
                        timeout=30,
                    )
                    data = json.loads(result.stdout)
                    if not guard:
                        self.assertEqual(result.returncode, 0)
                        self.assertTrue(
                            p.decode_response(result.stdout, request)[
                                "transport_verified"
                            ]
                        )
                    else:
                        self.assertNotEqual(result.returncode, 0)
                        self.assertFalse(data["success"])
                        self.assertGreater(sum(data["attempts"].values()), 0)
            sdk.write_text(
                "throw Error('MUST NOT IMPORT FOR PROMPT')", encoding="utf-8"
            )
            result = subprocess.run(
                [node, str(helper), str(sdk)],
                input=b'{"type":"prompt","message":"DO NOT SEND"}\n',
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=root,
                env=p.child_environment(root, node),
                timeout=30,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(json.loads(result.stdout)["stage"], "request")


if __name__ == "__main__":
    unittest.main()
