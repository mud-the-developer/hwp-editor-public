"""Owned session orchestration: fake COM/OS metadata, no Hwp or process start."""

from contextlib import contextmanager
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import hwp_native_owner as owner
import hwp_process_identity as process
from test_com_cache import cached_com_modules
from hwp_security import automation_cache


@contextmanager
def fake_owned_session(known_paths, event=None):
    """Old IO/approval fixtures only; refuse real COM and claim no ownership."""
    notify = event if event is not None else lambda stage: None
    with automation_cache():
        import pythoncom
        import win32com.client

        if getattr(win32com.client, "__file__", None) is not None:
            raise AssertionError("Synthetic session cannot use real COM")
        notify("com_import_done")
        pythoncom.CoInitialize()
        app = reference = None
        try:
            notify("dispatch_begin")
            app = win32com.client.DispatchEx("HWPFrame.HwpObject")
            app.XHwpWindows.Item(0).Visible = False
            notify("dispatch_done")
            reference = owner.NativeReference(app)
            reference.ownership = {"synthetic_only": True, "qualification": False}
            yield reference
        finally:
            try:
                if app is not None:
                    try:
                        notify("clear_begin")
                        app.Clear(1)
                        notify("clear_done")
                    finally:
                        notify("quit_begin")
                        app.Quit()
                        notify("quit_done")
            finally:
                if reference is not None:
                    reference.target = None
                app = None
                pythoncom.CoUninitialize()
                notify("com_uninitialized")


class OwnedSessionTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.input = self.root / "OWNED-COPY.hwp"
        self.window = SimpleNamespace(WindowHandle=9001, Visible=True)
        self.doc = SimpleNamespace(FullName="", Path="", Modified=False)
        self.app = SimpleNamespace(
            XHwpWindows=SimpleNamespace(Count=1, Item=lambda _: self.window),
            XHwpDocuments=SimpleNamespace(Count=1, Item=lambda _: self.doc),
            GetTextFile=Mock(return_value=None),
            Open=Mock(),
            Clear=Mock(side_effect=self.clear),
            Quit=Mock(),
        )
        self.com = SimpleNamespace(CoInitialize=Mock(), CoUninitialize=Mock())
        self.client = SimpleNamespace(DispatchEx=Mock(return_value=self.app))
        self.held = Mock(spec=process.ProcessIdentity)
        self.held.snapshot.return_value = {
            "pid": 7001,
            "creation_filetime": 150,
            "image": str(self.root / "hwp.exe"),
        }
        self.held.wait_for_exit.return_value = True
        self.metadata = SimpleNamespace(
            system_filetime=Mock(return_value=100),
            bind_window_process=Mock(return_value=self.held),
            window_process_id=Mock(return_value=7001),
        )
        modules = cached_com_modules(self.com, self.client, self.root)
        modules["hwp_process_identity"] = self.metadata
        for patcher in (
            patch.dict(sys.modules, modules),
            patch.object(
                owner, "_registered_image", return_value=self.root / "hwp.exe"
            ),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def clear(self, mode):
        self.assertEqual(mode, 1)
        self.doc.FullName = self.doc.Path = ""
        self.doc.Modified = False

    def named_copy(self):
        self.doc.FullName = str(self.input)
        self.doc.Path = str(self.root)
        self.doc.Modified = True

    def test_fresh_blank_known_copy_releases_reference_before_exact_exit_wait(self):
        references = []
        self.held.wait_for_exit.side_effect = lambda: (
            self.assertIsNone(references[0].target) or True
        )
        with owner.owned_session([self.input]) as app:
            references.append(app)
            proof = app.ownership
            self.assertEqual(proof["status"], "owned_blank_verified")
            self.assertFalse(self.window.Visible)
            self.named_copy()
        self.assertEqual(proof["status"], "owned_exit_confirmed")
        self.assertTrue(proof["exit_confirmed"])
        self.app.Clear.assert_called_once_with(1)
        self.app.Quit.assert_called_once()
        self.com.CoUninitialize.assert_called_once()
        self.held.wait_for_exit.assert_called_once()
        self.held.close.assert_called_once()
        self.app.Open.assert_not_called()
        self.metadata.bind_window_process.assert_called_once_with(
            9001, 100, self.root / "hwp.exe"
        )
        with self.assertRaises(ValueError):
            references[0].GetTextFile("TEXT", "")

    def test_mutable_diagnostic_proof_cannot_redirect_cleanup_binding(self):
        with owner.owned_session([self.input]) as reference:
            proof = reference.ownership
            proof.update(pid=9999, window_handle=8888, exit_confirmed=True)
            self.named_copy()
        self.assertEqual(proof["pid"], 7001)
        self.assertEqual(proof["window_handle"], 9001)
        self.assertEqual(proof["status"], "owned_exit_confirmed")
        self.app.Clear.assert_called_once()
        self.app.Quit.assert_called_once()
        self.metadata.bind_window_process.assert_called_once()

    def test_existing_or_unverified_process_is_never_cleared_quit_or_retried(self):
        self.metadata.bind_window_process.side_effect = RuntimeError(
            "SYNTHETIC preexisting process"
        )
        with self.assertRaisesRegex(RuntimeError, "unconfirmed"):
            with owner.owned_session([self.input]):
                self.fail("Unowned process entered caller")
        self.app.Clear.assert_not_called()
        self.app.Quit.assert_not_called()
        self.client.DispatchEx.assert_called_once()
        self.held.wait_for_exit.assert_not_called()

    def test_multiple_windows_are_not_bound_or_cleaned(self):
        self.app.XHwpWindows.Count = 2
        with self.assertRaises(RuntimeError) as failure:
            with owner.owned_session([self.input]):
                self.fail("Unknown windows entered caller")
        self.assertIn("Ownership unconfirmed", str(failure.exception))
        self.assertIn(
            "initial failure: RuntimeError: Expected one owned Hwp window",
            str(failure.exception),
        )
        self.metadata.bind_window_process.assert_not_called()
        self.app.Clear.assert_not_called()
        self.app.Quit.assert_not_called()
        self.com.CoUninitialize.assert_called_once()
        self.client.DispatchEx.assert_called_once()

    def test_nonblank_none_identity_or_modified_metadata_never_enters_caller(self):
        for kind in ("text", "name", "modified", "count"):
            with self.subTest(kind=kind):
                self.doc.FullName, self.doc.Modified = "", False
                self.app.XHwpDocuments.Count = 1
                self.app.GetTextFile.return_value = None
                self.held.close.reset_mock()
                if kind == "text":
                    self.app.GetTextFile.return_value = "UNKNOWN USER DATA"
                elif kind == "name":
                    self.doc.FullName = str(self.root / "EXISTING.hwp")
                elif kind == "modified":
                    self.doc.Modified = True
                else:
                    self.app.XHwpDocuments.Count = 2
                with self.assertRaises(RuntimeError):
                    with owner.owned_session([self.input]):
                        self.fail("Unknown document entered caller")
                self.app.Clear.assert_not_called()
                self.app.Quit.assert_not_called()
                self.held.close.assert_called_once()
        self.held.wait_for_exit.assert_not_called()

    def test_unknown_document_at_cleanup_gets_no_clear_or_quit(self):
        with self.assertRaisesRegex(RuntimeError, "Unknown document"):
            with owner.owned_session([self.input]):
                self.doc.FullName = str(self.root / "USER-OTHER.hwp")
        self.app.Clear.assert_not_called()
        self.app.Quit.assert_not_called()
        self.held.wait_for_exit.assert_not_called()
        self.held.close.assert_called_once()

    def test_multiple_documents_at_cleanup_are_not_closed(self):
        with self.assertRaises(RuntimeError):
            with owner.owned_session([self.input]):
                self.app.XHwpDocuments.Count = 2
        self.app.Clear.assert_not_called()
        self.app.Quit.assert_not_called()

    def test_window_pid_drift_at_cleanup_has_no_cleanup_or_retarget(self):
        self.metadata.window_process_id.side_effect = [7001, 7002]
        with self.assertRaisesRegex(RuntimeError, "changed"):
            with owner.owned_session([self.input]):
                pass
        self.app.Clear.assert_not_called()
        self.app.Quit.assert_not_called()
        self.metadata.bind_window_process.assert_called_once()
        self.held.close.assert_called_once()

    def test_body_failure_still_cleans_only_known_owned_copy(self):
        original = ValueError("SYNTHETIC BODY")
        with self.assertRaisesRegex(ValueError, "SYNTHETIC BODY") as failure:
            with owner.owned_session([self.input]):
                self.named_copy()
                raise original
        self.assertIs(failure.exception, original)
        self.app.Clear.assert_called_once()
        self.app.Quit.assert_called_once()
        self.held.wait_for_exit.assert_called_once()

    def test_clear_failure_still_attempts_owned_quit_and_exit_confirmation(self):
        self.app.Clear.side_effect = RuntimeError("SYNTHETIC CLEAR")
        proof = {}
        with self.assertRaisesRegex(RuntimeError, "CLEAR") as failure:
            with owner.owned_session([self.input]) as reference:
                proof = reference.ownership
                proof["primary_error"] = "FORGED DIAGNOSTIC, NOT AUTHORITY"
        self.assertIsNone(proof["primary_error"])
        self.assertNotIn("initial failure:", str(failure.exception))
        self.app.Quit.assert_called_once()
        self.held.wait_for_exit.assert_called_once()
        self.held.close.assert_called_once()

    def test_bound_but_nonpristine_blank_retains_initial_error_without_cleanup(self):
        self.doc.Modified = True
        with self.assertRaises(RuntimeError) as failure:
            with owner.owned_session([self.input]):
                self.fail("Nonpristine blank entered caller")
        self.assertIn("initial failure:", str(failure.exception))
        self.assertIn("pristine unsaved blank", str(failure.exception))
        self.app.Clear.assert_not_called()
        self.app.Quit.assert_not_called()
        self.held.wait_for_exit.assert_not_called()
        self.held.close.assert_called_once()
        self.metadata.bind_window_process.assert_called_once()

    def test_body_and_cleanup_errors_both_survive_in_bounded_diagnostics(self):
        self.app.Clear.side_effect = RuntimeError("SYNTHETIC CLEAR")
        proof = {}
        with self.assertRaises(RuntimeError) as failure:
            with owner.owned_session([self.input]) as reference:
                proof = reference.ownership
                self.named_copy()
                raise ValueError("SYNTHETIC BODY")
        self.assertIn("SYNTHETIC CLEAR", str(failure.exception))
        self.assertIn(
            "initial failure: ValueError: SYNTHETIC BODY", str(failure.exception)
        )
        self.assertEqual(proof["status"], "cleanup_unconfirmed")
        self.assertEqual(proof["primary_error"], "ValueError: SYNTHETIC BODY")
        self.app.Quit.assert_called_once()
        self.held.wait_for_exit.assert_called_once()
        self.held.close.assert_called_once()
        self.metadata.bind_window_process.assert_called_once()

    def test_long_primary_error_is_only_text_and_cannot_authorize_unknown_document(
        self,
    ):
        proof = {}
        with self.assertRaises(RuntimeError) as failure:
            with owner.owned_session([self.input]) as reference:
                proof = reference.ownership
                self.doc.FullName = str(self.root / "UNKNOWN.hwp")
                raise ValueError("SYNTHETIC ROOT " + "X" * 20000)
        self.assertIs(type(proof["primary_error"]), str)
        self.assertLessEqual(len(proof["primary_error"]), 500)
        self.assertLess(len(str(failure.exception)), 1200)
        self.assertIn("Unknown document", str(failure.exception))
        self.app.Clear.assert_not_called()
        self.app.Quit.assert_not_called()
        self.held.wait_for_exit.assert_not_called()
        self.held.close.assert_called_once()
        self.metadata.bind_window_process.assert_called_once()

    def test_quit_failure_does_not_claim_or_force_exit(self):
        self.app.Quit.side_effect = RuntimeError("SYNTHETIC QUIT")
        with self.assertRaisesRegex(RuntimeError, "QUIT"):
            with owner.owned_session([self.input]):
                pass
        self.app.Quit.assert_called_once()
        self.held.wait_for_exit.assert_not_called()
        self.held.close.assert_called_once()

    def test_timeout_is_blocking_without_kill_retry_or_reopen(self):
        self.held.wait_for_exit.side_effect = RuntimeError("SYNTHETIC exit unconfirmed")
        with self.assertRaisesRegex(RuntimeError, "unconfirmed"):
            with owner.owned_session([self.input]):
                pass
        self.held.wait_for_exit.assert_called_once()
        self.held.close.assert_called_once()
        self.metadata.bind_window_process.assert_called_once()

    def test_failed_handle_release_is_not_success(self):
        self.held.close.side_effect = RuntimeError("SYNTHETIC close")
        with self.assertRaisesRegex(RuntimeError, "handle release"):
            with owner.owned_session([self.input]):
                pass
        self.held.close.assert_called_once()

    def test_trace_error_cannot_skip_clear_quit_or_uninitialize(self):
        def event(stage):
            if stage == "clear_begin":
                raise ValueError("SYNTHETIC trace")

        with self.assertRaisesRegex(RuntimeError, "trace"):
            with owner.owned_session([self.input], event):
                pass
        self.app.Clear.assert_called_once()
        self.app.Quit.assert_called_once()
        self.com.CoUninitialize.assert_called_once()
        self.held.wait_for_exit.assert_called_once()

    def test_missing_paths_or_data_callback_stop_before_com(self):
        for paths, event in (([], None), ([self.input], "JSON IS NOT A CALLBACK")):
            with self.subTest(paths=paths), self.assertRaises(ValueError):
                with owner.owned_session(paths, event):
                    pass
        self.client.DispatchEx.assert_not_called()
        self.com.CoInitialize.assert_not_called()


if __name__ == "__main__":
    unittest.main()
