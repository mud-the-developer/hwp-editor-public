"""Desktop checks use TEMPORARY synthetic workspaces. No Hancom or real approval.

Core + real HTTP integration run headlessly. Tk widget tests explicitly report
skip when a display is unavailable; a skip is NOT visual/native integration proof.
"""
from pathlib import Path
import http.client
import json
import sys
import tempfile
import time
import tkinter as tk
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import hwp_desktop as d
import hwp_review as r


def fixture(path):
    path.mkdir(parents=True, exist_ok=True)
    (path / "source-fixture.hwp").write_bytes(b"SYNTHETIC ONLY; not a real HWP")
    with zipfile.ZipFile(path / "baseline.hwpx", "w") as package:
        package.writestr("mimetype", "application/hwp+zip")
        package.writestr("Contents/section0.xml", '<sec xmlns="http://www.hancom.co.kr/hwpml/2011/section"/>')
    with r.fitz.open() as pdf:
        page = pdf.new_page(width=300, height=400)
        page.insert_text((20, 40), "DESKTOP SYNTHETIC FIXTURE ONLY")
        pdf.save(path / "baseline.pdf")
        (path / "pages").mkdir()
        page.get_pixmap(dpi=100).save(path / "pages/page-001.png")
    manifest = {
        "workspace_id": "desktop-synthetic-only", "source": str(path / "source-fixture.hwp"),
        "source_audit": {"sha256": r.file_hash(path / "source-fixture.hwp")},
        "baseline_hwpx_sha256": r.file_hash(path / "baseline.hwpx"),
        "baseline_pdf_sha256": r.file_hash(path / "baseline.pdf"), "roundtrip_passed": True,
        "anchors": [], "protected_pages": [], "objects": {"tbl": 0, "pic": 0},
        "pages": [{"page": 1, "width": 300, "height": 400}],
    }
    r.write_json(path / "manifest.json", manifest)
    r.write_json(path / "comments.json", {"workspace_id": manifest["workspace_id"], "comments": []})
    return manifest


def snapshot(path):
    return {str(file.relative_to(path)): file.read_bytes() for file in path.rglob("*") if file.is_file()}


class DesktopTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "한글 공백 작업 폴더"
        self.manifest = fixture(self.path)
        self.native = patch.object(r, "native_export", side_effect=AssertionError("NO Native allowed"))
        self.native.start()
        self.addCleanup(self.native.stop)

    def test_inspection_is_read_only_and_accepts_unicode_paths(self):
        before = snapshot(self.path)
        info = d.inspect_workspace(self.path)
        self.assertEqual(info.path, self.path.resolve())
        self.assertEqual((info.pages, info.tables, info.images, info.protected_pages), (1, 0, 0, 0))
        self.assertFalse(info.has_fill_input)
        self.assertFalse(info.locked)
        self.assertEqual(snapshot(self.path), before)

    def test_missing_folder_or_manifest_is_not_created(self):
        path = self.path / "does-not-exist"
        with self.assertRaises(OSError):d.inspect_workspace(path)
        self.assertFalse(path.exists())
        with self.assertRaises(ValueError):d.inspect_workspace(self.path / "pages")

    def test_each_changed_source_or_baseline_is_blocked_without_repair(self):
        for name in ("source-fixture.hwp", "baseline.hwpx", "baseline.pdf"):
            with self.subTest(name=name):
                path = self.path / name
                old = path.read_bytes()
                path.write_bytes(old + b"SYNTHETIC alteration")
                before = snapshot(self.path)
                with self.assertRaises(ValueError):d.inspect_workspace(self.path)
                self.assertEqual(snapshot(self.path), before)
                path.write_bytes(old)

    def test_missing_preview_is_blocked(self):
        (self.path / "pages/page-001.png").unlink()
        with self.assertRaisesRegex(ValueError, "미리보기"):d.inspect_workspace(self.path)

    def test_page_sequence_and_foreign_comments_are_blocked(self):
        self.manifest["pages"][0]["page"] = True
        r.write_json(self.path / "manifest.json", self.manifest)
        with self.assertRaises(ValueError):d.inspect_workspace(self.path)
        self.manifest["pages"][0]["page"] = 1
        r.write_json(self.path / "manifest.json", self.manifest)
        r.write_json(self.path / "comments.json", {"workspace_id": "foreign", "comments": []})
        with self.assertRaises(ValueError):d.inspect_workspace(self.path)

    def test_invalid_apply_lock_is_visible_and_never_cleared(self):
        lock = self.path / r.APPLY_LOCK
        lock.write_bytes(b"INVALID SYNTHETIC LOCK")
        before = snapshot(self.path)
        self.assertTrue(d.inspect_workspace(self.path).locked)
        session = d.ReviewSession()
        self.addCleanup(session.stop)
        session.start(self.path)
        self.assertTrue(session.info.locked)
        session.stop()
        self.assertEqual(snapshot(self.path), before)

    def test_fill_presence_is_not_claimed_as_validated_data(self):
        (self.path / "fill-input.json").write_bytes(b"INVALID SYNTHETIC INPUT")
        self.assertTrue(d.inspect_workspace(self.path).has_fill_input)
        self.assertEqual((self.path / "fill-input.json").read_bytes(), b"INVALID SYNTHETIC INPUT")

    def test_all_mode_urls_have_only_exact_local_allowlisted_routes(self):
        for mode in d.MODES:
            url = d.review_url("http://127.0.0.1:8765", mode)
            self.assertTrue(url.startswith("http://127.0.0.1:8765/"))
        self.assertEqual(d.review_url("http://127.0.0.1:8765", "fill"), "http://127.0.0.1:8765/fill")
        with self.assertRaises(ValueError):d.review_url("http://127.0.0.1:8765", "../apply")

    def test_external_credentials_paths_and_invalid_ports_are_rejected(self):
        for origin in ("https://127.0.0.1:8765", "http://localhost:8765", "http://evil.invalid:8765",
                       "http://127.0.0.1:0", "http://127.0.0.1:65536", "http://127.0.0.1:8765/",
                       "http://user@127.0.0.1:8765", "http://127.0.0.1:8765\n"):
            with self.subTest(origin=origin), self.assertRaises(ValueError):d.review_url(origin, "memo")
        with patch.object(d.webbrowser, "open") as browser:
            with self.assertRaises(ValueError):d.open_window("http://evil.invalid/")
            with self.assertRaises(ValueError):d.open_window("http://127.0.0.1:8765/api/approve")
            browser.assert_not_called()

    def test_edge_uses_literal_argv_without_shell_and_is_not_terminated(self):
        edge = self.path / "SYNTHETIC Edge.exe"
        edge.touch()
        with patch.object(d.subprocess, "Popen") as spawn, patch.object(d.webbrowser, "open") as browser:
            url = d.review_url("http://127.0.0.1:8765", "native")
            d.open_window(url, edge)
            self.assertEqual(spawn.call_args.args[0], [str(edge), "--no-first-run", "--app=" + url])
            self.assertIs(spawn.call_args.kwargs["shell"], False)
            spawn.return_value.terminate.assert_not_called()
            browser.assert_not_called()

    def test_edge_failure_does_not_silently_switch_to_browser(self):
        edge = self.path / "SYNTHETIC Edge.exe"
        edge.touch()
        with patch.object(d.subprocess, "Popen", side_effect=OSError("Synthetic browser failure")), patch.object(d.webbrowser, "open") as browser:
            with self.assertRaises(OSError):d.open_window("http://127.0.0.1:8765/", edge)
            browser.assert_not_called()

    def test_explicit_default_browser_failure_is_reported(self):
        with patch.object(d.webbrowser, "open", return_value=False):
            with self.assertRaisesRegex(RuntimeError, "브라우저"):d.open_window("http://127.0.0.1:8765/")

    def test_owned_server_auto_port_readiness_and_no_writes(self):
        before = snapshot(self.path)
        session = d.ReviewSession()
        self.addCleanup(session.stop)
        info = session.start(self.path)
        self.assertTrue(session.running)
        self.assertEqual(info.workspace_id, self.manifest["workspace_id"])
        connection = http.client.HTTPConnection("127.0.0.1", session._server.server_port, timeout=5)
        self.addCleanup(connection.close)
        connection.request("GET", "/api/state")
        response = connection.getresponse()
        self.assertEqual(response.status, 200)
        self.assertEqual(json.loads(response.read())["manifest"]["workspace_id"], info.workspace_id)
        with self.assertRaises(ValueError):session.url("fill")
        self.assertIn("proposal=native", session.url("native"))
        session.stop()
        self.assertFalse(session.running)
        self.assertEqual(snapshot(self.path), before)
        session.stop()  # Idempotent, not a PID/process recovery.

    def test_managed_server_preserves_wrong_host_and_cross_origin_guards(self):
        session = d.ReviewSession()
        self.addCleanup(session.stop)
        session.start(self.path)
        for method, route, headers in (("GET", "/", {"Host": "evil.invalid"}),
                                       ("POST", "/api/approve", {"Origin": "https://evil.invalid"})) * 8:
            connection = http.client.HTTPConnection("127.0.0.1", session._server.server_port, timeout=5)
            try:
                connection.request(method, route, body=b"{}" if method == "POST" else None, headers=headers)
                response = connection.getresponse()
                self.assertEqual(response.status, 403)
                response.read()
            finally:connection.close()
        self.assertFalse((self.path / "approved-plan.json").exists())

    def test_second_start_is_rejected_and_stop_does_not_touch_other_server(self):
        one, other = d.ReviewSession(), d.ReviewSession()
        self.addCleanup(one.stop)
        self.addCleanup(other.stop)
        one.start(self.path)
        with self.assertRaises(RuntimeError):one.start(self.path)
        other.start(self.path)
        self.assertNotEqual(one.origin, other.origin)
        one.stop()
        self.assertTrue(other.running)
        connection = http.client.HTTPConnection("127.0.0.1", other._server.server_port, timeout=5)
        try:
            connection.request("GET", "/api/state")
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            response.read()
        finally:connection.close()

    def test_readiness_failure_closes_only_the_new_owned_server(self):
        session = d.ReviewSession()
        with patch.object(d.http.client, "HTTPConnection") as factory:
            response = factory.return_value.getresponse.return_value
            response.status = 200
            response.read.return_value = b'{"manifest":{"workspace_id":"foreign"}}'
            with self.assertRaisesRegex(RuntimeError, "다른 작업"):session.start(self.path)
        self.assertFalse(session.running)
        self.assertIsNone(session._server)

    def test_rejected_body_drain_is_bounded_by_size_and_total_time(self):
        server = r.create_review_server(self.path)
        self.addCleanup(server.server_close)
        handler = server.RequestHandlerClass.__new__(server.RequestHandlerClass)
        handler.connection = Mock()
        handler.connection.gettimeout.return_value = 15
        handler.rfile = Mock()
        for headers in ({"Content-Length": "1048577"}, {"Content-Length": "broken"},
                        {"Content-Length": "10", "Transfer-Encoding": "chunked"}):
            handler.headers = headers
            handler.discard_rejected_body()
        handler.rfile.read1.assert_not_called()
        handler.headers = {"Content-Length": "100000"}
        handler.rfile.read1.return_value = b"x"
        with patch.object(r.time, "monotonic", side_effect=[0, .1, .1, .2, .2, .6]):
            handler.discard_rejected_body()
        self.assertEqual(handler.rfile.read1.call_count, 2)
        self.assertEqual(handler.connection.settimeout.call_args.args, (15,))
        self.assertFalse((self.path / "approved-plan.json").exists())

    def test_pythonw_without_stderr_can_start_and_read_owned_server(self):
        session = d.ReviewSession()
        self.addCleanup(session.stop)
        with patch.object(sys, "stderr", None):
            session.start(self.path)
            self.assertTrue(session.running)
        session.stop()

    def test_stale_source_cannot_bind_a_managed_server(self):
        (self.path / "source-fixture.hwp").write_bytes(b"CHANGED SYNTHETIC SOURCE")
        session = d.ReviewSession()
        with patch.object(r, "create_review_server") as create:
            with self.assertRaises(ValueError):session.start(self.path)
            create.assert_not_called()
        self.assertFalse(session.running)


class DesktopWidgetTests(unittest.TestCase):
    def setUp(self):
        try:self.root = tk.Tk()
        except tk.TclError as error:self.skipTest("Tk display unavailable; not a visual pass: " + str(error))
        self.root.withdraw()
        self.addCleanup(self.destroy)
        self.edge = patch.object(d, "find_edge", return_value=None)
        self.edge.start()
        self.addCleanup(self.edge.stop)
        self.app = d.DesktopApp(self.root)
        self.gui_temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.gui_temp.cleanup)
        gui_path = Path(self.gui_temp.name) / "GUI-SYNTHETIC-PREPARED"
        fixture(gui_path)
        self.info = d.WorkspaceInfo(gui_path.resolve(), "GUI-SYNTHETIC", "SYNTHETIC GUI ONLY.hwp", 3, 2, 1, 1, False, False)

    def destroy(self):
        for identifier in self.root.tk.call("after", "info"):
            self.root.after_cancel(identifier)
        self.root.destroy()

    def wait(self):
        deadline = time.monotonic() + 5
        while self.app.busy and time.monotonic() < deadline:
            self.root.update()
            time.sleep(.01)
        self.assertFalse(self.app.busy, "Synthetic desktop task did not finish")

    def test_initial_native_widgets_require_explicit_folder_check(self):
        self.assertEqual(str(self.app.open_button.cget("state")), "disabled")
        self.assertEqual(str(self.app.check.cget("state")), "disabled")
        with patch.object(d, "inspect_workspace") as inspect:
            self.app.path.set(str(self.info.path))
            inspect.assert_not_called()
        self.assertEqual(str(self.app.open_button.cget("state")), "disabled")
        self.assertEqual(str(self.app.check.cget("state")), "normal")

    def test_background_check_modes_and_path_change_are_gated(self):
        self.app.path.set(str(self.info.path))
        with patch.object(d, "inspect_workspace", return_value=self.info) as inspect:
            self.app.check_workspace()
            self.assertTrue(self.app.busy)
            self.assertEqual(str(self.app.open_button.cget("state")), "disabled")
            self.wait()
            inspect.assert_called_once()
        self.assertIn("3쪽", self.app.summary.get())
        self.assertEqual(str(self.app.open_button.cget("state")), "normal")
        self.app.mode.set(d.MODES["fill"][0])
        self.app.update_controls()
        self.assertEqual(str(self.app.open_button.cget("state")), "disabled")
        self.app.mode.set(d.MODES["native"][0])
        self.app.update_controls()
        self.assertEqual(str(self.app.open_button.cget("state")), "normal")
        self.app.path.set(str(self.info.path / "new-folder"))
        self.assertIsNone(self.app.info)
        self.assertEqual(str(self.app.open_button.cget("state")), "disabled")

    def test_failed_check_never_retries_or_opens_window(self):
        self.app.path.set(str(self.info.path))
        with patch.object(d, "inspect_workspace", side_effect=ValueError("SYNTHETIC source mismatch")) as inspect, patch.object(d, "open_window") as open_window:
            self.app.check_workspace()
            self.wait()
            inspect.assert_called_once()
            open_window.assert_not_called()
        self.assertIn("SYNTHETIC source mismatch", self.app.last_error)
        self.assertIsNone(self.app.info)
        self.assertEqual(str(self.app.open_button.cget("state")), "disabled")

    def test_browser_error_keeps_owned_url_and_cancelled_close_does_not_stop(self):
        self.app.path.set(str(self.info.path))
        self.app.info = self.info
        self.app.session = SimpleNamespace(running=True, info=self.info, origin="http://127.0.0.1:8765", failure="", stop=lambda: self.fail("No stop on cancellation"), url=lambda mode:d.review_url("http://127.0.0.1:8765", mode))
        self.app.update_controls()
        with patch.object(d, "inspect_workspace", return_value=self.info), patch.object(d, "open_window", side_effect=OSError("SYNTHETIC window failed")):
            self.app.launch(None)
            self.wait()
        self.assertEqual(self.app.address.get(), "http://127.0.0.1:8765/")
        self.assertEqual(str(self.app.copy_button.cget("state")), "normal")
        with patch.object(d.messagebox, "askyesno", return_value=False):
            self.app.close()
        self.assertFalse(self.app.closing)
        self.assertTrue(self.app.session.running)


if __name__ == "__main__":
    unittest.main()
