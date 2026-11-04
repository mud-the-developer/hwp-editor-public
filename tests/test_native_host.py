"""Fresh unsigned native-host boundaries; no WPF, model, Hancom or authority."""

import hashlib
import http.client
from io import BytesIO
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import PropertyMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import hwp_native_host as native
import hwp_report_project as projects
from hwp_synthetic_report import write_inputs


class NativeHostTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.base = self.root / "projects"
        profile, _ = write_inputs(self.root / "unsigned")
        self.source = Path(profile["jobs"][0]["source"])
        self.original = self.source.read_bytes()
        self.host = native.NativeHost("a" * 64, base=self.base)
        self.addCleanup(self.host.close_after_ui_exit)
        for target in ("subprocess.Popen", "os.startfile"):
            denied = patch(target, side_effect=AssertionError("No model/Native/process launch"))
            denied.start()
            self.addCleanup(denied.stop)

    def connect(self):
        return self.host.dispatch("hello", {"protocol": native.PROTOCOL, "nonce": "a" * 64})

    def create(self):
        self.connect()
        return self.host.dispatch("new_report", {"source": str(self.source)})

    def workspace_source(self):
        folder = self.root / "work"
        folder.mkdir()
        source = folder / "원본 양식.hwpx"
        source.write_bytes(self.original)
        self.host.dispatch("open_workspace", {"root": str(folder)})
        return folder, source

    def test_explicit_material_capture_before_report_or_omp_preserves_input_and_reads_edits(self):
        self.connect()
        source = self.root / "참고 자료.txt"
        source.write_bytes("실제 선택 자료\r\n두 번째 줄".encode("utf-8"))
        before = source.read_bytes()
        first = self.host.dispatch("prepare_material", {"source": str(source)})
        self.assertEqual(first["text"], "실제 선택 자료\n두 번째 줄")
        self.assertEqual(first["source_sha256"], hashlib.sha256(before).hexdigest())
        self.assertEqual(first["sha256"], hashlib.sha256(first["text"].encode("utf-8")).hexdigest())
        self.assertEqual(source.read_bytes(), before)
        source.write_text("직접 변경한 자료", encoding="utf-8")
        second = self.host.dispatch("prepare_material", {"source": str(source)})
        self.assertEqual(second["text"], "직접 변경한 자료")
        hwpx = self.host.dispatch("prepare_material", {"source": str(self.source)})
        self.assertEqual(hwpx["source_kind"], "hwpx_text")
        self.assertIn("REPORT BODY BEFORE", hwpx["text"])
        self.assertEqual(self.source.read_bytes(), self.original)
        self.assertIsNone(self.host.omp)
        self.assertIsNone(self.host.session.info)
        self.assertFalse(self.base.exists())

    def test_explicit_material_capture_rejects_oversize_binary_and_linked_input(self):
        import os

        self.connect()
        large = self.root / "large.txt"
        large.write_bytes(b"x" * (512 * 1024 + 1))
        binary = self.root / "binary.txt"
        binary.write_bytes(b"\xff\xfe")
        for source in (large, binary, self.root, Path("relative.txt")):
            with self.subTest(source=source), self.assertRaises((ValueError, OSError)):
                self.host.dispatch("prepare_material", {"source": str(source)})
        original = self.root / "source.txt"
        original.write_bytes(b"SYNTHETIC KEEP")
        linked = self.root / "linked.txt"
        os.link(original, linked)
        with self.assertRaises(ValueError):
            self.host.dispatch("prepare_material", {"source": str(linked)})
        self.assertEqual(original.read_bytes(), b"SYNTHETIC KEEP")

    def test_workspace_selection_and_listing_are_readonly_and_leave_omp_unchanged(self):
        from types import SimpleNamespace

        self.connect()
        self.assertEqual(self.host.dispatch("list_workspace", {})["root"], "")
        folder = self.root / "empty"
        folder.mkdir()
        launch = {"launch_id": "SYNTHETIC ROOTLESS"}
        self.host.omp = SimpleNamespace(launch=launch)
        try:
            with patch.object(projects, "new_document", side_effect=AssertionError("No capture")):
                listing = self.host.dispatch("open_workspace", {"root": str(folder)})
                self.assertEqual(self.host.dispatch("list_workspace", {}), listing)
            self.assertEqual(listing["documents"], [])
            self.assertEqual(listing["projects"], [])
            self.assertIs(self.host.omp.launch, launch)
            self.assertIsNone(self.host.session.info)
            self.assertFalse(self.host.session.running)
            self.assertEqual(list(folder.iterdir()), [])
            self.assertFalse(self.base.exists())
        finally:
            self.host.omp = None

    def test_workspace_template_capture_reuses_exact_live_project(self):
        self.connect()
        folder, source = self.workspace_source()
        first = self.host.dispatch("open_workspace_document", {"source": str(source)})
        project_base = folder / ".hwpreview" / "Projects"
        self.assertEqual(Path(first["root"]).parent, project_base)
        self.assertFalse(self.base.exists())
        self.assertEqual((Path(first["root"]) / "source.hwpx").read_bytes(), self.original)
        self.assertEqual(source.read_bytes(), self.original)
        server = self.host.session._server
        with patch.object(projects, "new_document", side_effect=AssertionError("No duplicate capture")):
            second = self.host.dispatch("open_workspace_document", {"source": str(source)})
            self.assertEqual(self.host.dispatch("new_report", {"source": str(source)}), first)
        self.assertEqual(second, first)
        self.assertIs(self.host.session._server, server)
        self.assertEqual(len(list(project_base.iterdir())), 1)
        listing = self.host.dispatch("list_workspace", {})
        self.assertEqual(len(listing["documents"]), 1)
        self.assertEqual(listing["documents"][0]["project_root"], first["root"])
        self.assertEqual(listing["projects"][0]["root"], first["root"])
        self.assertFalse(list(project_base.rglob("*permission.json")))

    def test_explicit_picker_accepts_external_original_but_tree_intake_stays_confined(self):
        self.connect()
        folder, _ = self.workspace_source()
        with self.assertRaises(ValueError):
            self.host.dispatch("open_workspace_document", {"source": str(self.source)})
        self.assertFalse((folder / ".hwpreview").exists())
        first = self.host.dispatch("new_report", {"source": str(self.source)})
        base = folder / ".hwpreview" / "Projects"
        self.assertEqual(Path(first["root"]).parent, base)
        self.assertFalse(self.base.exists())
        self.assertEqual(self.source.read_bytes(), self.original)
        self.assertFalse((folder / self.source.name).exists())
        self.assertEqual((Path(first["root"]) / "source.hwpx").read_bytes(), self.original)
        with patch.object(projects, "new_document", side_effect=AssertionError("No duplicate external intake")):
            self.assertEqual(self.host.dispatch("new_report", {"source": str(self.source)}), first)
        self.assertEqual(len(list(base.iterdir())), 1)
        listing = self.host.dispatch("list_workspace", {})
        self.assertFalse(any(row["path"] == str(self.source) for row in listing["documents"]))
        self.assertTrue(any(row["root"] == first["root"] for row in listing["projects"]))
        with self.assertRaises(ValueError):
            self.host.dispatch("open_workspace_document", {"source": str(self.source)})
        for source in (Path(first["root"]) / "source.hwpx", self.root / "wrong.hwtx"):
            with self.subTest(source=source), self.assertRaises(ValueError):
                self.host.dispatch("new_report", {"source": str(source)})

    def test_changed_or_ambiguous_source_record_never_creates_replacement(self):
        self.connect()
        folder, source = self.workspace_source()
        first = self.host.dispatch("open_workspace_document", {"source": str(source)})
        base = folder / ".hwpreview" / "Projects"
        before = set(base.iterdir())
        source.write_bytes(self.original + b"SYNTHETIC DRIFT")
        with (
            patch.object(projects, "new_document", side_effect=AssertionError("No replacement")),
            self.assertRaises(ValueError),
        ):
            self.host.dispatch("open_workspace_document", {"source": str(source)})
        self.assertEqual(set(base.iterdir()), before)
        self.assertEqual(self.host.descriptor(), first)
        source.write_bytes(self.original)
        projects.new_document(source, base=base)
        before = set(base.iterdir())
        with (
            patch.object(projects, "new_document", side_effect=AssertionError("No ambiguity fallback")),
            self.assertRaisesRegex(ValueError, "여러"),
        ):
            self.host.dispatch("open_workspace_document", {"source": str(source)})
        self.assertEqual(set(base.iterdir()), before)
        self.assertTrue(self.host.dispatch("list_workspace", {})["issues"])

    def test_changed_source_identity_cannot_be_mistaken_for_uncaptured_source(self):
        self.connect()
        folder, source = self.workspace_source()
        first = self.host.dispatch("open_workspace_document", {"source": str(source)})
        marker_path = Path(first["root"]) / projects.PROJECT_FILE
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
        marker["source"] = str(folder / "missing.hwpx")
        marker_path.write_text(json.dumps(marker), encoding="utf-8")
        before = set(marker_path.parent.parent.iterdir())
        with (
            patch.object(projects, "new_document", side_effect=AssertionError("No replacement")),
            patch.object(projects, "new_blank_document", side_effect=AssertionError("No recreated identity")),
        ):
            for method, params in (
                ("open_workspace_document", {"source": str(source)}),
                ("new_blank_report", {"destination": str(folder / "blank.hwpx")}),
            ):
                with self.subTest(method=method), self.assertRaises(ValueError):
                    self.host.dispatch(method, params)
        self.assertEqual(set(marker_path.parent.parent.iterdir()), before)
        self.assertFalse((folder / "blank.hwpx").exists())

    def test_malformed_preserved_record_blocks_source_and_blank_capture(self):
        self.connect()
        folder, source = self.workspace_source()
        broken = folder / ".hwpreview" / "Projects" / "stopped"
        broken.mkdir(parents=True)
        (broken / "report-project.json").write_text("{", encoding="utf-8")
        with (
            patch.object(projects, "new_document", side_effect=AssertionError("No replacement")),
            patch.object(projects, "new_blank_document", side_effect=AssertionError("No blank")),
        ):
            for method, params in (
                ("open_workspace_document", {"source": str(source)}),
                ("new_blank_report", {"destination": str(folder / "new.hwpx")}),
            ):
                with self.subTest(method=method), self.assertRaises(ValueError):
                    self.host.dispatch(method, params)
        self.assertTrue(self.host.dispatch("list_workspace", {})["issues"])
        self.assertFalse((folder / "new.hwpx").exists())

    def test_blank_creates_editable_document_and_refuses_overwrite_escape_or_metadata(self):
        self.connect()
        folder, source = self.workspace_source()
        info = projects.new_document(source, base=folder / ".hwpreview" / "Projects")
        destination = folder / "새 빈 문서.hwpx"
        result = self.host.dispatch("new_blank_report", {"destination": str(destination)})
        created = projects.inspect(Path(result["root"]))
        self.assertTrue(created.document_first)
        self.assertTrue(created.gate_passed, created.issues)
        self.assertEqual(created.root.parent, folder / ".hwpreview" / "Projects")
        self.assertEqual(projects.source(created.root)["data"], destination.read_bytes())
        self.assertEqual([(row["text"], row["selectable"]) for row in projects.sections(created.root)],
                         [("", False), ("", True), ("", False)])
        status, state = self.request(result)
        self.assertEqual(status, 200)
        self.assertTrue(state["gate_passed"])
        self.assertEqual(state["versions"], [])
        self.assertFalse(list(created.root.rglob("*permission.json")))
        before = {path: path.read_bytes() for path in folder.rglob("*") if path.is_file()}
        for path in (
            source, destination, self.root / "outside.hwpx", folder / "wrong.hwtx",
            folder / ".hwpreview" / "new.hwpx", info.root / "new.hwpx",
            folder / "missing" / "new.hwpx",
        ):
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.host.dispatch("new_blank_report", {"destination": str(path)})
        self.assertEqual({path: path.read_bytes() for path in folder.rglob("*") if path.is_file()}, before)
        source.unlink()
        with self.assertRaises(ValueError):
            self.host.dispatch("new_blank_report", {"destination": str(source)})
        self.assertFalse(source.exists())

    def test_workspace_invalid_or_uncertain_switch_preserves_old_usable_session(self):
        self.connect()
        folder, source = self.workspace_source()
        descriptor = self.host.dispatch("open_workspace_document", {"source": str(source)})
        server = self.host.session._server
        with self.assertRaises(ValueError):
            self.host.dispatch("open_workspace", {"root": str(self.root / "missing")})
        self.assertEqual(self.host.workspace_root, folder)
        self.assertIs(self.host.session._server, server)
        self.assertEqual(self.request(descriptor)[0], 200)
        incoming = self.root / "incoming"
        incoming.mkdir()
        try:
            with (
                patch.object(self.host.session, "stop", side_effect=RuntimeError("SYNTHETIC UNCERTAIN STOP")),
                self.assertRaises(RuntimeError),
            ):
                self.host.dispatch("open_workspace", {"root": str(incoming)})
            self.assertEqual(self.host.workspace_root, folder)
            self.assertIs(self.host.session._server, server)
            self.assertTrue(self.host.ownership_error)
            self.assertEqual(self.request(descriptor)[0], 200)
            with self.assertRaises(RuntimeError):
                self.host.dispatch("open_workspace_document", {"source": str(source)})
        finally:
            self.host.ownership_error = ""
        self.host.dispatch("open_workspace", {"root": str(incoming)})
        self.assertIsNone(self.host.session.info)
        self.assertFalse(self.host.session.running)

    def test_workspace_rootless_first_intake_keeps_launch_and_later_handoff_is_blocked(self):
        from types import SimpleNamespace

        self.connect()
        launch = {"launch_id": "SYNTHETIC ROOTLESS"}
        self.host.omp = SimpleNamespace(launch=launch)
        try:
            folder, source = self.workspace_source()
            descriptor = self.host.dispatch("open_workspace_document", {"source": str(source)})
            self.assertIs(self.host.omp.launch, launch)
            for method, params in (
                ("open_workspace", {"root": str(folder)}),
                ("open_workspace_document", {"source": str(source)}),
                ("new_blank_report", {"destination": str(folder / "new.hwpx")}),
                ("open_report", {"root": descriptor["root"]}),
            ):
                with self.subTest(method=method), self.assertRaises(ValueError):
                    self.host.dispatch(method, params)
        finally:
            self.host.omp = None

    def test_workspace_busy_partial_runtime_and_rootless_guards_precede_side_effects(self):
        self.connect()
        folder, source = self.workspace_source()
        operations = (
            ("open_workspace", {"root": str(folder)}),
            ("open_workspace_document", {"source": str(source)}),
            ("new_blank_report", {"destination": str(folder / "new.hwpx")}),
        )
        for attribute in ("running", "stopping"):
            with patch.object(type(self.host.session), attribute, new_callable=PropertyMock, return_value=True):
                for method, params in operations:
                    with self.subTest(attribute=attribute, method=method), self.assertRaises(ValueError):
                        self.host.dispatch(method, params)
        for attribute in ("_server", "pending_session"):
            target = self.host.session if attribute == "_server" else self.host
            with patch.object(target, attribute, object()):
                for method, params in operations:
                    with self.subTest(attribute=attribute, method=method), self.assertRaises(ValueError):
                        self.host.dispatch(method, params)
        with patch.object(projects.harness.runtime, "identity", return_value={"changed": True}):
            for method, params in operations:
                with self.subTest(method=method), self.assertRaises(ValueError):
                    self.host.dispatch(method, params)
        self.assertFalse((folder / ".hwpreview").exists())
        self.host.dispatch("open_workspace_document", {"source": str(source)})
        control = self.host.session._server.report_control
        with patch.object(control, "require_idle", side_effect=ValueError("SYNTHETIC BUSY")):
            for method, params in operations:
                with self.subTest(method=method), self.assertRaises(ValueError):
                    self.host.dispatch(method, params)
        self.assertFalse((folder / "new.hwpx").exists())

    def test_workspace_operations_require_selection_and_closed_parameters(self):
        self.connect()
        for method, params in (
            ("open_workspace_document", {"source": str(self.source)}),
            ("new_blank_report", {"destination": str(self.root / "new.hwpx")}),
            ("open_workspace", {"root": str(self.root), "share": True}),
            ("list_workspace", {"root": str(self.root)}),
        ):
            with self.subTest(method=method), self.assertRaises(ValueError):
                self.host.dispatch(method, params)
        self.assertFalse(self.base.exists())

    def request(self, descriptor, route="/api/report-state", *, method="GET", payload=None, headers=None):
        port = int(descriptor["origin"].rsplit(":", 1)[1])
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        request_headers = {"X-Review-Token": descriptor["token"]}
        if headers is not None:
            request_headers.update(headers)
        try:
            connection.request(method, route, None if payload is None else json.dumps(payload), request_headers)
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def test_wrong_channel_cannot_create_or_manufacture_authority(self):
        for method, params in (
            ("new_report", {"source": str(self.source)}),
            ("hello", {"protocol": native.PROTOCOL, "nonce": "b" * 64}),
        ):
            with self.subTest(method=method), self.assertRaises(ValueError):
                self.host.dispatch(method, params)
        self.assertFalse(self.base.exists())
        self.connect()
        for method, params in (
            ("approve", {"path": str(self.root / "approved-plan.json")}),
            ("shell", {"command": "anything"}),
            ("new_report", {"source": str(self.source), "approved": True}),
            ("new_report", {"source": "relative.hwpx"}),
        ):
            with self.subTest(method=method), self.assertRaises(ValueError):
                self.host.dispatch(method, params)
        self.assertFalse(self.base.exists())
        self.assertEqual(self.source.read_bytes(), self.original)

    def test_new_project_uses_existing_guarded_http_without_generation(self):
        descriptor = self.create()
        code, state = self.request(descriptor)
        self.assertEqual(code, 200)
        self.assertEqual(state["versions"], [])
        self.assertEqual(state["requests"], [])
        self.assertFalse(state["active"])
        root = Path(descriptor["root"])
        self.assertEqual((root / "source.hwpx").read_bytes(), self.original)
        self.assertFalse(list(root.rglob("*permission.json")))
        self.assertFalse(list(root.rglob("acceptance.json")))
        self.assertFalse(list(root.rglob("approved-plan.json")))
        code, _ = self.request(descriptor, headers={"X-Review-Token": "b" * 64})
        self.assertEqual(code, 403)
        code, _ = self.request(
            descriptor, "/api/report-start", method="POST", payload={},
            headers={"Content-Type": "application/json", "If-Match": state["revision"]},
        )
        self.assertEqual(code, 403, "Pipe capability must not bypass the existing Origin gate")
        self.assertEqual(self.source.read_bytes(), self.original)

    def test_invalid_project_switch_keeps_current_exact_session_usable(self):
        descriptor = self.create()
        self.assertEqual(self.request(descriptor)[0], 200)
        names = set(self.base.iterdir())
        invalid = self.root / "invalid.hwpx"
        invalid.write_bytes(b"SYNTHETIC INVALID ZIP")
        for method, params in (
            ("open_report", {"root": str(self.root / "unsigned")}),
            ("new_report", {"source": str(invalid)}),
        ):
            incoming = json.dumps({"id": "invalid", "method": method, "params": params}).encode() + b"\n"
            outgoing = BytesIO()
            native.serve(self.host, BytesIO(incoming), outgoing)
            self.assertIs(json.loads(outgoing.getvalue())["ok"], False)
            self.assertEqual(self.host.descriptor(), descriptor)
            self.assertEqual(self.request(descriptor)[0], 200)
            self.assertEqual(set(self.base.iterdir()), names)

    def test_busy_model_prevents_close_and_new_project_without_new_side_effects(self):
        descriptor = self.create()
        control = self.host.session._server.report_control
        release = threading.Event()
        worker = threading.Thread(target=release.wait, args=(10,))
        worker.start()
        control.thread = worker
        names = set(self.base.iterdir())
        try:
            for method, params in (
                ("shutdown", {}), ("close_report", {}),
                ("new_report", {"source": str(self.source)}),
                ("open_report", {"root": descriptor["root"]}),
            ):
                with self.subTest(method=method), self.assertRaises(ValueError):
                    self.host.dispatch(method, params)
            self.assertFalse(self.host.closed)
            self.assertEqual(set(self.base.iterdir()), names)
            self.assertEqual(self.request(descriptor)[0], 200)
        finally:
            release.set()
            worker.join(5)
        self.assertEqual(self.host.dispatch("shutdown", {}), {"closed": True})
        self.assertFalse(self.host.session.running)

    def test_shutdown_closes_only_owned_session_and_readonly_reopen_preserves_bytes(self):
        descriptor = self.create()
        other = native.NativeHost("c" * 64, base=self.base)
        self.addCleanup(other.close_after_ui_exit)
        other.dispatch("hello", {"protocol": native.PROTOCOL, "nonce": "c" * 64})
        second = other.dispatch("open_report", {"root": descriptor["root"]})
        before = {str(path.relative_to(descriptor["root"])): hashlib.sha256(path.read_bytes()).hexdigest()
                  for path in Path(descriptor["root"]).rglob("*") if path.is_file()}
        self.host.dispatch("shutdown", {})
        self.assertEqual(self.request(second)[0], 200)
        other.dispatch("shutdown", {})
        after = {str(path.relative_to(descriptor["root"])): hashlib.sha256(path.read_bytes()).hexdigest()
                 for path in Path(descriptor["root"]).rglob("*") if path.is_file()}
        self.assertEqual(before, after)
        info = projects.inspect(Path(descriptor["root"]))
        self.assertTrue(info.gate_passed, info.issues)
        self.assertEqual(info.versions, ())

    def test_inherited_channel_rejects_duplicate_keys_and_oversize_before_dispatch(self):
        for raw in (
            b'{"id":"one","id":"two","method":"hello","params":{}}\n',
            b" " * native.REQUEST_LIMIT + b"\n",
            b'{"id":"one","method":"hello","params":{}}',
        ):
            with self.subTest(size=len(raw)), self.assertRaises(ValueError):
                native.serve(self.host, BytesIO(raw), BytesIO())
        self.assertFalse(self.host.connected)
        self.assertFalse(self.base.exists())

    def test_confirmed_ui_exit_during_reply_stops_its_server_without_hiding_error(self):
        class ExitedWriter(BytesIO):
            calls = 0

            def write(self, value):
                self.calls += 1
                if self.calls == 2:
                    raise BrokenPipeError("SYNTHETIC UI EXIT")
                return super().write(value)

        messages = [
            {"id": "one", "method": "hello", "params": {"protocol": native.PROTOCOL, "nonce": "a" * 64}},
            {"id": "two", "method": "new_report", "params": {"source": str(self.source)}},
        ]
        from types import SimpleNamespace

        child = SimpleNamespace(
            stdin=ExitedWriter(),
            stdout=BytesIO(b"".join(json.dumps(item).encode() + b"\n" for item in messages)),
            stderr=BytesIO(), poll=lambda: 0, wait=lambda **kwargs: 0,
        )
        with (
            patch("hwp_native_payload.locate_payload", return_value=self.root),
            patch("hwp_native_payload.locate_dotnet", return_value=self.root / "dotnet.exe"),
            patch.object(native, "NativeHost", return_value=self.host),
            patch.object(native.secrets, "token_hex", return_value="a" * 64),
            patch.object(native.subprocess, "Popen", return_value=child),
            self.assertRaisesRegex(BrokenPipeError, "SYNTHETIC UI EXIT"),
        ):
            native.run(base=self.base)
        self.assertFalse(self.host.session.running)
        self.assertTrue(self.host.closed)

    def test_changed_runtime_cannot_admit_new_project_from_existing_ui(self):
        self.connect()
        changed = dict(projects.harness.runtime.identity())
        changed["app_version"] = "SYNTHETIC CHANGED RUNTIME"
        with (
            patch.object(projects.harness.runtime, "identity", return_value=changed),
            self.assertRaises(ValueError),
        ):
            self.host.dispatch("new_report", {"source": str(self.source)})
        self.assertFalse(self.base.exists(), "An old UI must not bind a new project to changed code")

    def test_native_ui_environment_excludes_unrecorded_managed_startup_inputs(self):
        overrides = {
            "DOTNET_STARTUP_HOOKS": "UNRECORDED-HOOK.dll",
            "DOTNET_ADDITIONAL_DEPS": "UNRECORDED.deps.json",
            "DOTNET_SHARED_STORE": "UNRECORDED-STORE",
            "DOTNET_ROLL_FORWARD": "Major",
            "CORECLR_ENABLE_PROFILING": "1",
            "CORECLR_PROFILER_PATH": "UNRECORDED-PROFILER.dll",
            "COR_ENABLE_PROFILING": "1",
            "COMPlus_ReadyToRun": "0",
        }
        with patch.dict(native.os.environ, overrides):
            environment = native.ui_environment()
        self.assertTrue({key.upper() for key in overrides}.isdisjoint(key.upper() for key in environment))

    def test_missing_desktop_runtime_stops_before_ui_process_or_project_creation(self):
        with (
            patch("hwp_native_payload.locate_payload", return_value=self.root),
            patch("hwp_native_payload.locate_dotnet", side_effect=FileNotFoundError("SYNTHETIC MISSING RUNTIME")),
            self.assertRaises(ValueError),
        ):
            native.run(base=self.base)
        self.assertFalse(self.base.exists())
        self.assertFalse(self.host.session.running)


if __name__ == "__main__":
    unittest.main()
