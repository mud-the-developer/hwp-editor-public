"""Pure/default-deny checks for the one-shot metadata-only diagnostic."""

import ast
import hashlib
import importlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

m = importlib.import_module("native_blank_metadata_probe")


class BlankMetadataTests(unittest.TestCase):
    def test_classifier_never_outputs_text_and_distinguishes_bom(self):
        self.assertEqual(
            m.describe_value(None), {"python_type": "NoneType", "is_none": True}
        )
        self.assertTrue(m.describe_value("")["literal_empty"])
        self.assertTrue(m.describe_value(" \r\n")["strip_empty"])
        bom = m.describe_value("\ufeff\r\n")
        self.assertFalse(bom["strip_empty"])
        self.assertTrue(bom["starts_unicode_bom"])
        self.assertTrue(bom["single_bom_then_whitespace_only"])
        self.assertFalse(bom["has_non_bom_non_whitespace"])
        for value in ("OWNER PRIVATE TEXT", b"\xff\xfeOWNER PRIVATE TEXT", "\ud800"):
            metadata = m.describe_value(value)
            self.assertNotIn("OWNER PRIVATE TEXT", json.dumps(metadata))
            self.assertNotIn("value", metadata)
        self.assertEqual(
            m.describe_value(b"abc")["sha256"], hashlib.sha256(b"abc").hexdigest()
        )
        self.assertTrue(m.describe_value("text")["has_non_bom_non_whitespace"])

        class Secret:
            def __repr__(self):
                raise AssertionError("No repr allowed")

        self.assertEqual(m.describe_value(Secret())["python_type"], "other")

    def test_explicit_flags_frozen_and_fresh_path_gate_native(self):
        with (
            tempfile.TemporaryDirectory() as temp,
            patch.object(m, "ROOT", Path(temp)),
            patch.object(m, "native_metadata") as native,
        ):
            (Path(temp) / "work").mkdir()
            target = Path(temp) / "work/toy-hwp-blank-metadata-SYNTHETIC"
            for flags in (
                {},
                {"run_native": True},
                {"metadata_only": True},
                {"run_native": 1, "metadata_only": True},
            ):
                with self.assertRaises(ValueError):
                    m.run(target, **flags)
            with (
                patch.object(m.sys, "frozen", True, create=True),
                self.assertRaises(ValueError),
            ):
                m.run(target, run_native=True, metadata_only=True)
            for path in (Path(temp) / "outside", Path(temp) / "work/wrong"):
                with self.assertRaises(ValueError):
                    m.run(path, run_native=True, metadata_only=True)
            target.mkdir()
            with self.assertRaises(ValueError):
                m.run(target, run_native=True, metadata_only=True)
            native.assert_not_called()

    def test_process_inventory_is_metadata_only_and_rejects_unknown_shape(self):
        result = SimpleNamespace(
            returncode=0, stdout=b'{"pid":123,"creation_ms":456}', stderr=b""
        )
        with patch.object(m.subprocess, "run", return_value=result) as call:
            self.assertEqual(m.hwp_inventory(), [{"pid": 123, "creation_ms": 456}])
            args = call.call_args.args[0]
            self.assertNotIn("Stop-Process", args[-1])
            self.assertNotIn("CommandLine", args[-1])
            self.assertNotIn("MainWindowTitle", args[-1])
            result.stdout = b'{"pid":123,"creation_ms":456,"path":"PRIVATE"}'
            with self.assertRaises(ValueError):
                m.hwp_inventory()
            result.returncode = 1
            with self.assertRaises(RuntimeError):
                m.hwp_inventory()

    def test_existing_hwp_blocks_before_coinit_or_dispatch(self):
        import pythoncom
        import win32com.client

        with (
            patch.object(
                m, "hwp_inventory", return_value=[{"pid": 123, "creation_ms": 456}]
            ),
            patch.object(pythoncom, "CoInitialize") as initialize,
            patch.object(win32com.client, "DispatchEx") as dispatch,
        ):
            with self.assertRaises(RuntimeError):
                m.native_metadata(Mock(), {})
            initialize.assert_not_called()
            dispatch.assert_not_called()

    def test_probe_cannot_open_edit_save_clear_register_or_kill(self):
        tree = ast.parse(
            Path(__file__)
            .with_name("native_blank_metadata_probe.py")
            .read_text(encoding="utf-8")
        )
        forbidden = {
            "Open",
            "Save",
            "SaveAs",
            "Clear",
            "Run",
            "RegisterModule",
            "InsertText",
            "SetTextFile",
            "Kill",
            "TerminateProcess",
        }
        calls = [
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        ]
        self.assertFalse(forbidden.intersection(calls))
        self.assertEqual(calls.count("Quit"), 1)
        self.assertEqual(calls.count("GetTextFile"), 1)

    def test_native_failure_records_only_error_type_and_never_retries(self):
        with (
            tempfile.TemporaryDirectory() as temp,
            patch.object(m, "ROOT", Path(temp)),
            patch.object(
                m, "native_metadata", side_effect=RuntimeError("PRIVATE RAW CONTENT")
            ) as native,
        ):
            (Path(temp) / "work").mkdir()
            target = Path(temp) / "work/toy-hwp-blank-metadata-SYNTHETIC"
            data = m.run(target, run_native=True, metadata_only=True)
            self.assertEqual(data["status"], "blocked")
            self.assertNotIn("PRIVATE", json.dumps(data))
            self.assertFalse(data["raw_content_recorded"])
            self.assertFalse(data["document_save_performed"])
            native.assert_called_once()
            with self.assertRaises(ValueError):
                m.run(target, run_native=True, metadata_only=True)
            native.assert_called_once()


if __name__ == "__main__":
    unittest.main()
