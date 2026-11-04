"""Regression reproductions on OUR synthetic Tk/HTTP objects only. No Native."""

import http.client
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch
from typing import cast

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import hwp_desktop as d
import hwp_review as r
from test_desktop import DesktopWidgetTests, fixture, snapshot


class DebugWidgetTests(DesktopWidgetTests):
    # Use the synthetic setup, not the inherited test methods (see load_tests).
    def test_failed_window_launch_retains_the_requested_contract_url(self):
        self.app.path.set(str(self.info.path))
        self.app.info = self.info
        session = Mock()
        session.running = True
        session.stopping = False
        session.info = self.info
        session.origin = "http://127.0.0.1:8765"
        session.url.side_effect = lambda mode: d.review_url(session.origin, mode)
        self.app.session = session
        self.app.mode.set(d.MODES["native"][0])
        with (
            patch.object(d, "inspect_workspace", return_value=self.info),
            patch.object(
                d, "open_window", side_effect=OSError("Synthetic Edge failure")
            ),
        ):
            self.app.launch(None)
            self.wait()
        self.assertEqual(self.app.address.get(), d.review_url(session.origin, "native"))
        self.assertIsNotNone(self.app.info)
        session.stop.assert_not_called()

    def test_source_failure_with_live_server_revokes_ready_state_but_preserves_server(
        self,
    ):
        self.app.path.set(str(self.info.path))
        self.app.info = self.info
        session = Mock()
        session.running = True
        session.stopping = False
        session.info = self.info
        session.origin = "http://127.0.0.1:8765"
        self.app.session = session
        with (
            patch.object(
                d,
                "inspect_workspace",
                side_effect=ValueError("Synthetic changed source"),
            ),
            patch.object(d, "open_window") as window,
        ):
            self.app.launch(None)
            self.wait()
            window.assert_not_called()
        self.assertIsNone(self.app.info)
        self.assertEqual(str(self.app.open_button.cget("state")), "disabled")
        self.assertEqual(str(self.app.stop_button.cget("state")), "normal")
        session.stop.assert_not_called()

    def test_busy_repeat_check_does_not_discard_the_current_state(self):
        self.app.path.set(str(self.info.path))
        self.app.info = self.info
        self.app.busy = True
        with patch.object(d, "inspect_workspace") as inspect:
            self.app.check_workspace()
            inspect.assert_not_called()
        self.assertIs(self.app.info, self.info)
        self.app.busy = False

    def test_successful_explicit_recheck_clears_obsolete_error(self):
        self.app.path.set(str(self.info.path))
        self.app.last_error = "OBSOLETE synthetic error"
        with patch.object(d, "inspect_workspace", return_value=self.info):
            self.app.check_workspace()
            self.wait()
        self.assertEqual(self.app.last_error, "")

    def test_tk_callback_error_without_stderr_is_visible_and_does_not_write_files(self):
        before = set(Path(tempfile.gettempdir()).glob("HWPReview-*.log"))
        with patch.object(sys, "stderr", None):
            self.root.report_callback_exception(
                ValueError, ValueError("Synthetic clipboard/UI failure"), None
            )
        self.assertIn("Synthetic clipboard/UI failure", self.app.last_error)
        self.assertIn("오류", self.app.status.get())
        self.assertEqual(
            set(Path(tempfile.gettempdir()).glob("HWPReview-*.log")), before
        )

    def test_missing_help_asset_produces_readable_error_without_crashing_callback(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(d, "ROOT", Path(temp)):
            self.app.show_help()
        self.assertIn("안내", self.app.last_error)
        self.assertIn("오류", self.app.status.get())

    def test_license_window_never_opens_or_changes_documents(self):
        before = snapshot(self.info.path)
        with (
            patch.object(self.app, "show_text"),
            patch.object(d, "open_window") as launch,
        ):
            self.app.show_license()
            launch.assert_not_called()
        self.assertEqual(snapshot(self.info.path), before)

    def test_mousewheel_over_mode_picker_does_not_change_the_contract(self):
        self.app.mode.set(d.MODES["native"][0])
        self.app.mode_picker.event_generate("<MouseWheel>", delta=-120)
        self.root.update_idletasks()
        self.assertEqual(self.app.mode.get(), d.MODES["native"][0])

    def test_pending_shutdown_locks_launch_and_keeps_manual_stop_available(self):
        self.app.path.set(str(self.info.path))
        self.app.info = self.info
        session = Mock()
        session.running = False
        session.stopping = True
        self.app.session = session
        self.app.update_controls()
        self.assertEqual(str(self.app.open_button.cget("state")), "disabled")
        self.assertEqual(str(self.app.check.cget("state")), "disabled")
        self.assertEqual(str(self.app.stop_button.cget("state")), "normal")


class ShutdownDebugTests(unittest.TestCase):
    def test_thread_start_failure_closes_only_the_allocated_server_and_preserves_cause(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "SYNTHETIC-thread-capacity-only"
            fixture(path)
            server = r.create_review_server(path)
            session = d.ReviewSession()
            with (
                patch.object(r, "create_review_server", return_value=server),
                patch.object(
                    d.threading.Thread,
                    "start",
                    side_effect=RuntimeError("Synthetic thread capacity"),
                ),
            ):
                with self.assertRaisesRegex(RuntimeError, "Synthetic thread capacity"):
                    session.start(path)
            self.assertEqual(server.fileno(), -1)
            self.assertIsNone(session._server)

    def test_disconnected_transport_is_tolerated_but_other_handler_errors_are_visible(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "SYNTHETIC-disconnect-only"
            fixture(path)
            server = r.create_review_server(path)
            self.addCleanup(server.server_close)
            handler_type = cast(
                type[r.BaseHTTPRequestHandler], server.RequestHandlerClass
            )
            handler = handler_type.__new__(handler_type)
            with patch.object(
                r.BaseHTTPRequestHandler,
                "handle",
                side_effect=ConnectionAbortedError("Synthetic lost client"),
            ):
                handler.handle()
            self.assertTrue(handler.close_connection)
            with patch.object(
                r.BaseHTTPRequestHandler,
                "handle",
                side_effect=RuntimeError("Synthetic unexpected handler bug"),
            ):
                with self.assertRaisesRegex(RuntimeError, "unexpected"):
                    handler.handle()
            self.assertFalse((path / "approved-plan.json").exists())

    def test_pending_http_reader_makes_stop_bounded_and_remains_owned_until_explicit_finish(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "SYNTHETIC bounded-stop-only"
            fixture(path)
            before = snapshot(path)
            one, other = d.ReviewSession(), d.ReviewSession()
            one.start(path)
            other.start(path)
            first_server, second_server = one._server, other._server
            if first_server is None or second_server is None:
                raise AssertionError("Synthetic servers did not start")
            handler_type = cast(
                type[r.BaseHTTPRequestHandler], first_server.RequestHandlerClass
            )
            client = socket.socket()
            client.settimeout(5)
            accepted = threading.Event()
            # A partial header is enough to keep the SERIAL owned HTTP handler
            # waiting. Close ONLY this synthetic client after the timeout probe.
            original = handler_type.handle_one_request

            def handler(instance):
                accepted.set()
                return original(instance)

            try:
                with patch.object(handler_type, "handle_one_request", handler):
                    # Install observation BEFORE connecting; accept may otherwise
                    # enter the original method before the patch and miss the event.
                    client.connect(("127.0.0.1", first_server.server_port))
                    client.sendall(b"GET /api/state HTTP/1.1\r\nHost: 127.0.0.1:")
                    self.assertTrue(accepted.wait(2))
                    started = time.monotonic()
                    with self.assertRaisesRegex(RuntimeError, "끝나지"):
                        one.stop(timeout=0.05)
                    self.assertLess(time.monotonic() - started, 1)
                self.assertTrue(one.stopping)
                self.assertIsNotNone(one._server)
                self.assertTrue(other.running)
                with self.assertRaises(RuntimeError):
                    one.start(path)
                shutdown = one._shutdown_thread
                with self.assertRaises(RuntimeError):
                    one.stop(timeout=0.05)
                self.assertIs(one._shutdown_thread, shutdown)
                client.close()
                one.stop(timeout=3)
                self.assertFalse(one.running)
                self.assertFalse(one.stopping)
                self.assertIsNone(one._server)
                connection = http.client.HTTPConnection(
                    "127.0.0.1", second_server.server_port, timeout=5
                )
                try:
                    connection.request("GET", "/api/state")
                    response = connection.getresponse()
                    self.assertEqual(response.status, 200)
                    response.read()
                finally:
                    connection.close()
                self.assertEqual(snapshot(path), before)
            finally:
                client.close()
                one.stop()
                other.stop()


def load_tests(loader, tests, pattern):
    # Reuse setup helpers without duplicating the original four widget tests.
    suite = unittest.TestSuite()
    for name in DebugWidgetTests.__dict__:
        if name.startswith("test_"):
            suite.addTest(DebugWidgetTests(name))
    suite.addTests(loader.loadTestsFromTestCase(ShutdownDebugTests))
    return suite


if __name__ == "__main__":
    unittest.main()
