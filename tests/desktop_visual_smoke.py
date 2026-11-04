"""Explicit Windows Tk-only visual smoke. Captures ONLY windows created here.

Synthetic fixtures, no Hancom launch/browser/real report/approval. Not native HWP
fidelity evidence. Uses PrintWindow on our own Tk HWND, NEVER desktop screenshots.
"""
import ctypes
from ctypes import wintypes as w
from pathlib import Path
import sys
import tempfile
import time
import tkinter as tk

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import fitz
import os
from unittest.mock import patch
import hwp_desktop as d
from test_desktop import fixture


class BitmapInfo(ctypes.Structure):
    _fields_ = [("size", w.DWORD), ("width", w.LONG), ("height", w.LONG),
                ("planes", w.WORD), ("bit_count", w.WORD), ("compression", w.DWORD),
                ("image_size", w.DWORD), ("x_ppm", w.LONG), ("y_ppm", w.LONG),
                ("used", w.DWORD), ("important", w.DWORD)]


def capture_owned_window(root, path):
    user, gdi = ctypes.WinDLL("user32", use_last_error=True), ctypes.WinDLL("gdi32", use_last_error=True)
    user.GetAncestor.argtypes, user.GetAncestor.restype = [w.HWND, w.UINT], w.HWND
    user.GetWindowRect.argtypes, user.GetWindowRect.restype = [w.HWND, ctypes.POINTER(w.RECT)], w.BOOL
    user.GetDC.argtypes, user.GetDC.restype = [w.HWND], w.HDC
    user.ReleaseDC.argtypes = [w.HWND, w.HDC]
    user.PrintWindow.argtypes, user.PrintWindow.restype = [w.HWND, w.HDC, w.UINT], w.BOOL
    gdi.CreateCompatibleDC.argtypes, gdi.CreateCompatibleDC.restype = [w.HDC], w.HDC
    gdi.CreateCompatibleBitmap.argtypes, gdi.CreateCompatibleBitmap.restype = [w.HDC, ctypes.c_int, ctypes.c_int], w.HBITMAP
    gdi.SelectObject.argtypes, gdi.SelectObject.restype = [w.HDC, w.HGDIOBJ], w.HGDIOBJ
    gdi.GetDIBits.argtypes, gdi.GetDIBits.restype = [w.HDC, w.HBITMAP, w.UINT, w.UINT, w.LPVOID, ctypes.POINTER(BitmapInfo), w.UINT], ctypes.c_int
    gdi.DeleteObject.argtypes = [w.HGDIOBJ]
    gdi.DeleteDC.argtypes = [w.HDC]
    # The only HWND source is the root CREATED BY THIS SCRIPT.
    hwnd = user.GetAncestor(root.winfo_id(), 2)
    rect = w.RECT()
    if not user.GetWindowRect(hwnd, ctypes.byref(rect)):
        raise ctypes.WinError(ctypes.get_last_error())
    width, height = rect.right - rect.left, rect.bottom - rect.top
    dc = user.GetDC(hwnd)
    memory = gdi.CreateCompatibleDC(dc)
    bitmap = gdi.CreateCompatibleBitmap(dc, width, height)
    old = gdi.SelectObject(memory, bitmap)
    try:
        if not user.PrintWindow(hwnd, memory, 2):
            raise RuntimeError("Owned Tk PrintWindow capture failed")
        gdi.SelectObject(memory, old)
        old = None
        header = BitmapInfo(ctypes.sizeof(BitmapInfo), width, -height, 1, 32, 0, 0, 0, 0, 0, 0)
        buffer = ctypes.create_string_buffer(width * height * 4)
        if gdi.GetDIBits(memory, bitmap, 0, height, buffer, ctypes.byref(header), 0) != height:
            raise RuntimeError("Owned Tk pixel read failed")
        bgra = buffer.raw
        rgb = bytearray(width * height * 3)
        rgb[0::3], rgb[1::3], rgb[2::3] = bgra[2::4], bgra[1::4], bgra[0::4]
        path.parent.mkdir(parents=True, exist_ok=True)
        fitz.Pixmap(fitz.csRGB, width, height, bytes(rgb), False).save(path)
    finally:
        if old:gdi.SelectObject(memory, old)
        gdi.DeleteObject(bitmap)
        gdi.DeleteDC(memory)
        user.ReleaseDC(hwnd, dc)


def main():
    output = Path(__file__).resolve().parents[1] / "work/ui-tests/desktop"
    with tempfile.TemporaryDirectory() as temp:
        workspace = Path(temp) / "SYNTHETIC ONLY desktop workspace"
        fixture(workspace)
        root = tk.Tk()
        app = d.DesktopApp(root)
        root.title("SYNTHETIC ONLY · HWP desktop visual smoke")
        try:
            root.update()
            capture_owned_window(root, output / "welcome.png")
            app.path.set(str(workspace))
            app.check_workspace()
            deadline = time.monotonic() + 10
            while app.busy and time.monotonic() < deadline:
                root.update()
                time.sleep(.02)
            if app.busy or app.info is None:raise RuntimeError("Synthetic workspace did not become ready")
            root.update()
            capture_owned_window(root, output / "ready.png")
            root.geometry("720x680")
            root.update()
            app.canvas.yview_moveto(1)
            root.update()
            capture_owned_window(root, output / "compact-footer.png")
            location = Path(temp) / "SYNTHETIC library"
            projects = location / "HwpReview/Projects";projects.mkdir(parents=True)
            fixture(projects / "SYNTHETIC ready")
            partial = projects / "SYNTHETIC stopped";partial.mkdir();(partial / "_review").mkdir()
            staged = projects / "SYNTHETIC input";staged.mkdir();(staged / "SYNTHETIC.hwp").write_bytes(b"SYNTHETIC NO NATIVE")
            with patch.dict(os.environ, {"LOCALAPPDATA": str(location)}):
                app.show_library()
                deadline = time.monotonic() + 10
                while app.busy and time.monotonic() < deadline:
                    root.update();time.sleep(.02)
                if app.busy or app.library_window is None:raise RuntimeError("Synthetic library did not appear")
                root.update();capture_owned_window(app.library_window.top, output / "library.png")
                app.library_window.top.geometry("700x480");root.update()
                capture_owned_window(app.library_window.top, output / "library-compact.png")
                app.library_window.close()
            print("PASS: owned synthetic Tk welcome/ready/compact/library captures; NO HWP/browser/approval")
        finally:
            app.session.stop()
            root.destroy()


if __name__ == "__main__":
    main()
