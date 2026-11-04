"""Fixed owned Hwp lifecycle. No approval, policy change, process kill or retry."""

from contextlib import contextmanager
import gc
import os
from pathlib import Path
from typing import Any

from hwp_security import automation_cache, hwp_executable


class NativeReference:
    def __init__(self, app):
        self.target: Any = app
        self.ownership: dict[str, Any] = {}

    def __getattr__(self, name):
        if self.target is None:
            raise ValueError("Native instance lifecycle has ended")
        return getattr(self.target, name)


def _registered_image():
    import winreg

    return hwp_executable(winreg).resolve()


def _window_handle(app):
    windows = window = None
    try:
        windows = app.XHwpWindows
        if type(windows.Count) is not int or windows.Count != 1:
            raise RuntimeError("Expected one owned Hwp window")
        window = windows.Item(0)
        value = window.WindowHandle
        if type(value) is not int or value <= 0:
            raise RuntimeError("Typed Hwp window handle unavailable")
        return value
    finally:
        window = windows = None


def _document(app):
    documents = document = None
    try:
        documents = app.XHwpDocuments
        if type(documents.Count) is not int or documents.Count != 1:
            raise RuntimeError("Expected one owned Hwp document")
        document = documents.Item(0)
        name, path, modified = document.FullName, document.Path, document.Modified
        if (
            not isinstance(name, str)
            or not isinstance(path, str)
            or type(modified) not in (bool, int)
            or modified not in (0, 1)
        ):
            raise RuntimeError(
                "Hwp document identity/modification metadata unavailable"
            )
        return name, path, modified
    finally:
        document = documents = None


def _blank(app):
    before = _document(app)
    if before != ("", "", False):
        raise RuntimeError("Owned document is not a pristine unsaved blank")
    text = app.GetTextFile("TEXT", "")
    # None is calibrated only after independent fresh PID/window/image evidence.
    if text is not None and (not isinstance(text, str) or text.strip()):
        raise RuntimeError("Owned blank TEXT is not empty")
    if _document(app) != before:
        raise RuntimeError("Owned blank changed during observation")


def _known_document(app, paths):
    name, _, _ = _document(app)
    if not name:
        _blank(app)
    elif not os.path.isabs(name) or Path(name).resolve() not in paths:
        raise RuntimeError("Unknown document at cleanup; no Clear/Quit")


@contextmanager
def owned_session(known_paths, event=None):
    """Caller gates must already authorize the fixed input/copies/outputs.

    Paths limit cleanup only; they are not a grant to open/edit. The reference is
    invalidated before CoUninitialize and the original process-handle exit wait.
    """
    paths = frozenset(Path(path).resolve() for path in known_paths if path is not None)
    if not paths or event is not None and not callable(event):
        raise ValueError("Known caller-owned document paths and code callback required")
    notify = event if event is not None else lambda stage: None
    with automation_cache():
        import pythoncom
        import win32com.client
        import hwp_process_identity as process

        expected_image = _registered_image()
        notify("com_import_done")
        pythoncom.CoInitialize()
        app = reference = held = None
        verified = quit_returned = False
        cleanup_error = primary_error = None
        bound_pid = bound_window = started = None
        binding: dict[str, Any] = {}
        proof: dict[str, Any] = {
            "status": "ownership_unconfirmed",
            "exit_confirmed": False,
        }
        try:
            notify("dispatch_begin")
            started = process.system_filetime()
            app = win32com.client.DispatchEx("HWPFrame.HwpObject")
            hwnd = _window_handle(app)
            bound_window = hwnd
            proof.update(window_handle=hwnd, started_filetime=started)
            held = process.bind_window_process(hwnd, started, expected_image)
            binding = held.snapshot()
            bound_pid = binding["pid"]
            proof.update(binding, window_handle=hwnd, started_filetime=started)
            _blank(app)
            if (
                _window_handle(app) != hwnd
                or process.window_process_id(hwnd) != bound_pid
            ):
                raise RuntimeError("Owned window changed during blank verification")
            verified = True
            proof["status"] = "owned_blank_verified"
            app.XHwpWindows.Item(0).Visible = False
            notify("dispatch_done")
            reference = NativeReference(app)
            reference.ownership = proof
            yield reference
        except BaseException as error:
            primary_error = (type(error).__name__ + ": " + str(error))[:500]
            raise
        finally:
            try:
                if app is not None:
                    if not verified or held is None:
                        raise RuntimeError(
                            "Ownership unconfirmed; no Clear/Quit; owner inspection required"
                        )
                    proof.update(
                        binding,
                        window_handle=bound_window,
                        started_filetime=started,
                        exit_confirmed=False,
                    )
                    hwnd = _window_handle(app)
                    if (
                        hwnd != bound_window
                        or process.window_process_id(hwnd) != bound_pid
                    ):
                        raise RuntimeError("Owned window/PID changed; no Clear/Quit")
                    _known_document(app, paths)
                    try:
                        try:
                            notify("clear_begin")
                        finally:
                            app.Clear(1)
                        notify("clear_done")
                    finally:
                        try:
                            notify("quit_begin")
                        finally:
                            app.Quit()
                            quit_returned = True
                        notify("quit_done")
            except BaseException as error:
                cleanup_error = type(error).__name__ + ": " + str(error)[:500]
            finally:
                if reference is not None:
                    reference.target = None
                app = reference = None
                gc.collect()
                try:
                    pythoncom.CoUninitialize()
                    notify("com_uninitialized")
                except BaseException as error:
                    cleanup_error = (
                        cleanup_error
                        or type(error).__name__ + ": COM release/trace failed"
                    )
                if held is not None:
                    try:
                        if not quit_returned:
                            raise RuntimeError(
                                "Owned process cleanup unconfirmed; no termination"
                            )
                        if held.wait_for_exit() is not True:
                            raise RuntimeError("Process exit confirmation missing")
                        proof["exit_confirmed"] = True
                    except BaseException as error:
                        cleanup_error = (
                            cleanup_error
                            or type(error).__name__ + ": " + str(error)[:500]
                        )
                    finally:
                        try:
                            held.close()
                        except BaseException:
                            cleanup_error = (
                                cleanup_error or "Process handle release unconfirmed"
                            )
                if cleanup_error is not None:
                    proof.update(
                        status="cleanup_unconfirmed",
                        cleanup_error=cleanup_error,
                        primary_error=primary_error,
                    )
                    initial = (
                        f"; initial failure: {primary_error}" if primary_error else ""
                    )
                    raise RuntimeError(
                        f"Hwp owned cleanup unconfirmed (pid={bound_pid}, "
                        f"window={bound_window}): {cleanup_error}{initial}; no retry/termination"
                    )
                if quit_returned:
                    proof["status"] = "owned_exit_confirmed"
