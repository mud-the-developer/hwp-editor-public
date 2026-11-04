"""Fake kernel32 only; no process or COM creation in this unit suite."""

import ctypes
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import hwp_process_identity as process


class ProcessIdentityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.image = str(Path(temporary.name).resolve() / "SYNTHETIC-Hwp.exe")
        self.pid = 7001
        self.created = (123 << 32) | 456
        self.image_size = None
        self.api = SimpleNamespace(
            OpenProcess=Mock(return_value=77),
            GetProcessId=Mock(return_value=self.pid),
            GetProcessTimes=Mock(side_effect=self.times),
            QueryFullProcessImageNameW=Mock(side_effect=self.query_image),
            GetSystemTimeAsFileTime=Mock(side_effect=self.system_time),
            WaitForSingleObject=Mock(return_value=0),
            CloseHandle=Mock(return_value=True),
        )
        patcher = patch.object(process, "_kernel", return_value=self.api)
        self.factory = patcher.start()
        self.addCleanup(patcher.stop)

    def set_time(self, pointer, value):
        target = ctypes.cast(pointer, ctypes.POINTER(process._FileTime)).contents
        target.low, target.high = value & 0xFFFFFFFF, value >> 32

    def times(self, handle, created, exited, kernel, user):
        self.assertEqual(handle, 77)
        self.set_time(created, self.created)
        return 1

    def system_time(self, pointer):
        self.set_time(pointer, self.created + 100)

    def query_image(self, handle, flags, buffer, capacity):
        self.assertEqual((handle, flags), (77, 0))
        buffer.value = self.image
        length = self.image_size
        if length is None:
            length = len(self.image.encode("utf-16-le", "surrogatepass")) // 2
        ctypes.cast(capacity, ctypes.POINTER(ctypes.c_uint32)).contents.value = length
        return 1

    def test_only_query_and_synchronize_rights_and_one_exact_handle(self):
        with process.ProcessIdentity(self.pid) as held:
            observed = held.snapshot()
            self.assertEqual(
                observed,
                {
                    "pid": self.pid,
                    "creation_filetime": self.created,
                    "image": self.image,
                },
            )
            observed["pid"] = 9999
            self.assertEqual(held.snapshot()["pid"], self.pid)
            self.assertTrue(held.wait_for_exit())
        self.api.OpenProcess.assert_called_once_with(0x00101000, False, self.pid)
        self.api.WaitForSingleObject.assert_called_once_with(77, 5000)
        self.api.CloseHandle.assert_called_once_with(77)
        self.assertFalse(hasattr(held, "terminate"))

    def test_invalid_pid_never_opens_a_process(self):
        for pid in (True, False, 0, -1, 2**32, "7001", None):
            with self.subTest(pid=pid), self.assertRaises(ValueError):
                process.ProcessIdentity(pid)
        self.factory.assert_not_called()
        self.api.OpenProcess.assert_not_called()

    def test_failed_open_is_not_closed_or_retried(self):
        self.api.OpenProcess.return_value = None
        with self.assertRaisesRegex(RuntimeError, "handle unavailable"):
            process.ProcessIdentity(self.pid)
        self.api.OpenProcess.assert_called_once()
        self.api.CloseHandle.assert_not_called()

    def test_mismatched_or_untyped_handle_pid_closes_before_metadata(self):
        for value in (self.pid + 1, True):
            with self.subTest(value=value):
                self.api.GetProcessId.return_value = value
                self.api.CloseHandle.reset_mock()
                with self.assertRaisesRegex(RuntimeError, "identity differs"):
                    process.ProcessIdentity(1 if value is True else self.pid)
                self.api.CloseHandle.assert_called_once_with(77)
        self.api.GetProcessTimes.assert_not_called()

    def test_creation_query_failure_releases_handle(self):
        self.api.GetProcessTimes.return_value = 0
        self.api.GetProcessTimes.side_effect = None
        with self.assertRaisesRegex(RuntimeError, "creation-time metadata unavailable"):
            process.ProcessIdentity(self.pid)
        self.api.CloseHandle.assert_called_once_with(77)

    def test_zero_creation_time_is_not_assumed_fresh(self):
        self.created = 0
        with self.assertRaisesRegex(RuntimeError, "Invalid process creation"):
            process.ProcessIdentity(self.pid)
        self.api.CloseHandle.assert_called_once_with(77)

    def test_image_query_failure_releases_handle(self):
        self.api.QueryFullProcessImageNameW.side_effect = None
        self.api.QueryFullProcessImageNameW.return_value = 0
        with self.assertRaisesRegex(RuntimeError, "image metadata unavailable"):
            process.ProcessIdentity(self.pid)
        self.api.CloseHandle.assert_called_once_with(77)

    def test_empty_relative_or_truncated_image_is_rejected(self):
        good = self.image
        for image, size in (("", 0), ("relative.exe", None), (good, 1), (good, 32768)):
            with self.subTest(image=image, size=size):
                self.image, self.image_size = image, size
                self.api.CloseHandle.reset_mock()
                with self.assertRaisesRegex(RuntimeError, "image metadata"):
                    process.ProcessIdentity(self.pid)
                self.api.CloseHandle.assert_called_once_with(77)

    def test_non_bmp_image_uses_win32_utf16_units_not_python_length(self):
        self.image = str(Path(self.image).parent / "한글-😀-SYNTHETIC.exe")
        with process.ProcessIdentity(self.pid) as held:
            self.assertEqual(held.snapshot()["image"], self.image)

    def test_unconfirmed_waits_fail_without_reopen_or_termination(self):
        for value in (258, 0xFFFFFFFF, 128, True, False, None):
            with self.subTest(value=value):
                self.api.WaitForSingleObject.return_value = value
                self.api.OpenProcess.reset_mock()
                self.api.CloseHandle.reset_mock()
                with process.ProcessIdentity(self.pid) as held:
                    with self.assertRaises(RuntimeError):
                        held.wait_for_exit()
                self.api.OpenProcess.assert_called_once()
                self.api.CloseHandle.assert_called_once_with(77)

    def test_closed_handle_cannot_query_wait_or_close_again(self):
        held = process.ProcessIdentity(self.pid)
        held.close()
        held.close()
        for operation in (held.snapshot, held.wait_for_exit, held.__enter__):
            with self.assertRaisesRegex(RuntimeError, "already closed"):
                operation()
        self.api.CloseHandle.assert_called_once_with(77)
        self.api.WaitForSingleObject.assert_not_called()

    def test_close_failure_is_reported_and_not_retried(self):
        held = process.ProcessIdentity(self.pid)
        self.api.CloseHandle.return_value = False
        with self.assertRaisesRegex(RuntimeError, "release unconfirmed"):
            held.close()
        held.close()
        self.api.CloseHandle.assert_called_once()

    def test_system_time_and_filetime_layout_are_exact(self):
        self.assertEqual(ctypes.sizeof(process._FileTime), 8)
        self.assertEqual(process.system_filetime(), self.created + 100)
        self.api.GetSystemTimeAsFileTime.assert_called_once()

    def test_zero_system_reference_is_rejected(self):
        self.created = -100
        with self.assertRaisesRegex(RuntimeError, "reference unavailable"):
            process.system_filetime()
        self.api.OpenProcess.assert_not_called()


class WindowProcessBindingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.image = Path(temporary.name).resolve() / "SYNTHETIC-Hwp.exe"
        self.pid, self.hwnd = 7001, 9001
        self.held = Mock(spec=process.ProcessIdentity)
        self.held.snapshot.return_value = {
            "pid": self.pid,
            "creation_filetime": 200,
            "image": str(self.image),
        }

    def test_window_api_reads_pid_without_creating_or_closing_a_window(self):
        def query(hwnd, pointer):
            self.assertEqual(hwnd, self.hwnd)
            ctypes.cast(
                pointer, ctypes.POINTER(ctypes.c_uint32)
            ).contents.value = self.pid
            return 101

        api = SimpleNamespace(GetWindowThreadProcessId=Mock(side_effect=query))
        with patch.object(process, "_user", return_value=api):
            self.assertEqual(process.window_process_id(self.hwnd), self.pid)
        api.GetWindowThreadProcessId.assert_called_once()

    def test_invalid_or_stale_window_has_no_process_admission(self):
        with patch.object(process, "_user") as user:
            for value in (
                True,
                False,
                0,
                -1,
                1 << (ctypes.sizeof(ctypes.c_void_p) * 8),
                "HWND",
                None,
            ):
                with self.subTest(hwnd=value), self.assertRaises(ValueError):
                    process.window_process_id(value)
            user.assert_not_called()
        api = SimpleNamespace(GetWindowThreadProcessId=Mock(return_value=0))
        with patch.object(process, "_user", return_value=api):
            with self.assertRaisesRegex(RuntimeError, "unavailable"):
                process.window_process_id(self.hwnd)

    def test_binding_holds_exact_fresh_expected_image_and_rechecks_window(self):
        with (
            patch.object(
                process, "window_process_id", side_effect=[self.pid, self.pid]
            ) as window,
            patch.object(process, "ProcessIdentity", return_value=self.held) as opened,
            patch.object(process, "system_filetime", return_value=300),
        ):
            result = process.bind_window_process(self.hwnd, 100, self.image)
        self.assertIs(result, self.held)
        self.assertEqual(window.call_args_list[0].args, (self.hwnd,))
        self.assertEqual(window.call_count, 2)
        opened.assert_called_once_with(self.pid)
        self.held.close.assert_not_called()

    def test_preexisting_future_wrong_pid_or_image_never_becomes_owned(self):
        invalid = [
            {"creation_filetime": 99},
            {"creation_filetime": 301},
            {"creation_filetime": True},
            {"pid": self.pid + 1},
            {"pid": True},
            {"image": str(self.image.with_name("OTHER.exe"))},
            {"image": "relative.exe"},
        ]
        for delta in invalid:
            with self.subTest(delta=delta):
                self.held.snapshot.return_value = {
                    "pid": self.pid,
                    "creation_filetime": 200,
                    "image": str(self.image),
                    **delta,
                }
                self.held.close.reset_mock()
                with (
                    patch.object(
                        process, "window_process_id", return_value=self.pid
                    ) as window,
                    patch.object(process, "ProcessIdentity", return_value=self.held),
                    patch.object(process, "system_filetime", return_value=300),
                ):
                    with self.assertRaisesRegex(RuntimeError, "expected fresh process"):
                        process.bind_window_process(self.hwnd, 100, self.image)
                self.held.close.assert_called_once()
                window.assert_called_once_with(self.hwnd)

    def test_window_pid_drift_closes_original_handle_without_opening_new_pid(self):
        with (
            patch.object(
                process, "window_process_id", side_effect=[self.pid, self.pid + 1]
            ),
            patch.object(process, "ProcessIdentity", return_value=self.held) as opened,
            patch.object(process, "system_filetime", return_value=300),
        ):
            with self.assertRaisesRegex(RuntimeError, "changed"):
                process.bind_window_process(self.hwnd, 100, self.image)
        opened.assert_called_once_with(self.pid)
        self.held.close.assert_called_once()

    def test_bad_reference_or_relative_expected_image_stops_before_window_query(self):
        with patch.object(process, "window_process_id") as window:
            for reference in (True, 0, -1, None):
                with self.subTest(reference=reference), self.assertRaises(ValueError):
                    process.bind_window_process(self.hwnd, reference, self.image)
            with self.assertRaisesRegex(ValueError, "Absolute"):
                process.bind_window_process(self.hwnd, 100, "relative.exe")
        window.assert_not_called()

    def test_failed_handle_acquisition_does_not_requery_or_retry(self):
        with (
            patch.object(process, "window_process_id", return_value=self.pid) as window,
            patch.object(
                process,
                "ProcessIdentity",
                side_effect=RuntimeError("SYNTHETIC access denied"),
            ) as opened,
        ):
            with self.assertRaisesRegex(RuntimeError, "access denied"):
                process.bind_window_process(self.hwnd, 100, self.image)
        window.assert_called_once()
        opened.assert_called_once_with(self.pid)


if __name__ == "__main__":
    unittest.main()
