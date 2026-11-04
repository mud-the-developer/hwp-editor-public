"""Cache startup/lifetime guard: synthetic modules and TEMP files, never COM."""

import ast
import builtins
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, call, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import hwp_security as security


class CachedWrapper:
    CLSID = "{5E6A8276-CF1C-42B8-BCED-319548B02AF6}"


STARTUP_CLASSES: dict[str, type] = {CachedWrapper.CLSID: CachedWrapper}
for name, iid in (
    ("IXHwpWindows", "{74099719-83AB-416E-98BE-A199D1A74CDC}"),
    ("IXHwpWindow", "{D87D278F-9D76-45BE-9AA1-A93A7AA410C9}"),
    ("IXHwpDocuments", "{FBA628D5-0C64-4BBF-81DF-47FEAF65BF2B}"),
    ("IXHwpDocument", "{AEB10CD9-3A09-4752-B2AB-947137273436}"),
):
    STARTUP_CLASSES[iid] = type(name, (), {"CLSID": iid})


def cached_com_modules(com, client, root):
    """Give existing fake-COM integration fixtures metadata, never a real module."""
    if getattr(client, "__file__", None) is not None:
        raise ValueError("Synthetic COM module required")
    cache = getattr(client, "gencache", SimpleNamespace())
    if getattr(cache, "__file__", None) is not None:
        raise ValueError("Synthetic cache module required")
    cache.GetClassForCLSID = Mock(side_effect=STARTUP_CLASSES.get)
    package = ModuleType("win32com")
    directory = str(Path(root).resolve() / "SYNTHETIC-COM-CACHE")
    package.__dict__.update(
        __gen_path__=directory,
        gen_py=SimpleNamespace(__path__=[directory]),
        __path__=[],
        client=client,
    )
    client.__dict__["gencache"] = cache
    return {
        "pythoncom": com,
        "win32com": package,
        "win32com.client": client,
        "win32com.client.gencache": cache,
    }


class AutomationCacheTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.cache = self.root / "gen_py"
        self.cache.mkdir()
        self.index = self.cache / "dicts.dat"
        self.index.write_bytes(b"SYNTHETIC EXISTING INDEX")
        self.package = ModuleType("win32com")
        self.package.__dict__.update(
            __gen_path__=str(self.cache),
            gen_py=SimpleNamespace(__path__=[str(self.cache)]),
        )
        self.client = ModuleType("win32com.client")
        self.gencache = SimpleNamespace(
            GetClassForCLSID=Mock(side_effect=STARTUP_CLASSES.get),
            is_readonly=False,
            EnsureDispatch=Mock(side_effect=AssertionError("No fallback")),
        )
        self.client.__dict__.update(
            gencache=self.gencache,
            DispatchEx=Mock(side_effect=AssertionError("No COM creation in preflight")),
        )
        self.package.__dict__["client"] = self.client
        patcher = patch.dict(
            sys.modules,
            {
                "win32com": self.package,
                "win32com.client": self.client,
                "win32com.client.gencache": self.gencache,
            },
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_ready_metadata_does_not_create_com_and_job_trace_writes_still_work(self):
        before = sys.dont_write_bytecode
        with security.automation_cache():
            self.assertTrue(sys.dont_write_bytecode)
            (self.root / "job-trace.json").write_text("SYNTHETIC", encoding="utf-8")
        self.assertEqual(sys.dont_write_bytecode, before)
        self.assertEqual(self.index.read_bytes(), b"SYNTHETIC EXISTING INDEX")
        self.assertEqual(
            self.gencache.GetClassForCLSID.call_args_list,
            [call(iid) for iid in STARTUP_CLASSES],
        )
        self.client.DispatchEx.assert_not_called()
        self.gencache.EnsureDispatch.assert_not_called()
        self.assertFalse(self.gencache.is_readonly)

    def test_missing_or_wrong_wrapper_blocks_without_dispatch_or_generation(self):
        for value in (None, object(), type("Wrong", (), {"CLSID": "WRONG"})):
            with self.subTest(value=value):
                self.gencache.GetClassForCLSID.side_effect = None
                self.gencache.GetClassForCLSID.return_value = value
                with self.assertRaisesRegex(RuntimeError, "wrapper"):
                    with security.automation_cache():
                        self.fail("Unavailable wrapper entered caller")
        self.client.DispatchEx.assert_not_called()
        self.gencache.EnsureDispatch.assert_not_called()

    def test_missing_or_wrong_startup_children_fail_before_com_initialization(self):
        import native_hwp

        com = SimpleNamespace(CoInitialize=Mock(), CoUninitialize=Mock())
        for iid in list(STARTUP_CLASSES)[1:]:
            for bad in (None, object(), type("WrongChild", (), {"CLSID": "WRONG"})):
                with self.subTest(iid=iid, bad=bad):
                    self.gencache.GetClassForCLSID.reset_mock()
                    self.gencache.GetClassForCLSID.side_effect = (
                        lambda requested, bad=bad, iid=iid: (
                            bad if requested == iid else STARTUP_CLASSES.get(requested)
                        )
                    )
                    with patch.dict(sys.modules, {"pythoncom": com}):
                        with self.assertRaisesRegex(RuntimeError, "wrapper"):
                            native_hwp.export(
                                self.root / "NOT-OPENED.hwp",
                                self.root / "NOT-CREATED.pdf",
                            )
                    self.assertEqual(
                        self.gencache.GetClassForCLSID.call_args_list[-1], call(iid)
                    )
                    self.client.DispatchEx.assert_not_called()
                    self.gencache.EnsureDispatch.assert_not_called()
                    com.CoInitialize.assert_not_called()
                    com.CoUninitialize.assert_not_called()
                    self.assertFalse((self.root / "NOT-CREATED.pdf").exists())

    def test_lazy_child_generation_attempt_fails_before_com_and_keeps_cache_bytes(self):
        import native_hwp

        com = SimpleNamespace(CoInitialize=Mock(), CoUninitialize=Mock())
        child = list(STARTUP_CLASSES)[1]

        def lookup(iid):
            if iid == child:
                os.makedirs(self.cache, exist_ok=True)
            return STARTUP_CLASSES.get(iid)

        self.gencache.GetClassForCLSID.side_effect = lookup
        with patch.dict(sys.modules, {"pythoncom": com}):
            with self.assertRaisesRegex(RuntimeError, "자동 변경"):
                native_hwp.export(
                    self.root / "NOT-OPENED.hwp", self.root / "NOT-CREATED.pdf"
                )
        self.assertEqual(
            self.gencache.GetClassForCLSID.call_args_list,
            [call(CachedWrapper.CLSID), call(child)],
        )
        self.assertEqual(self.index.read_bytes(), b"SYNTHETIC EXISTING INDEX")
        self.assertEqual(list(self.cache.iterdir()), [self.index])
        self.client.DispatchEx.assert_not_called()
        self.gencache.EnsureDispatch.assert_not_called()
        com.CoInitialize.assert_not_called()
        com.CoUninitialize.assert_not_called()
        self.assertFalse((self.root / "NOT-CREATED.pdf").exists())
        self.assertFalse(self.gencache.is_readonly)

    def test_lookup_cache_write_is_blocked_before_bytes_change_and_never_retried(self):
        def write(*args):
            self.index.write_bytes(b"MUST NOT REPLACE")
            return CachedWrapper

        self.gencache.GetClassForCLSID.side_effect = write
        with self.assertRaisesRegex(RuntimeError, "자동 변경"):
            with security.automation_cache():
                self.fail("Cache mutation entered caller")
        self.assertEqual(self.index.read_bytes(), b"SYNTHETIC EXISTING INDEX")
        self.gencache.GetClassForCLSID.assert_called_once()
        self.client.DispatchEx.assert_not_called()
        self.gencache.EnsureDispatch.assert_not_called()

    def test_import_time_write_is_blocked_before_lookup(self):
        original_import = builtins.__import__

        def importing(name, *args, **kwargs):
            if name == "win32com.client":
                self.index.write_bytes(b"IMPORT REBUILD")
            return original_import(name, *args, **kwargs)

        with patch.object(builtins, "__import__", side_effect=importing):
            with self.assertRaisesRegex(RuntimeError, "자동 변경"):
                with security.automation_cache():
                    self.fail("Mutating import entered caller")
        self.assertEqual(self.index.read_bytes(), b"SYNTHETIC EXISTING INDEX")
        self.gencache.GetClassForCLSID.assert_not_called()
        self.client.DispatchEx.assert_not_called()

    def test_lazy_cache_mutation_stays_blocked_through_caller_cleanup(self):
        cleaned = []
        with self.assertRaisesRegex(RuntimeError, "자동 변경"):
            with security.automation_cache():
                try:
                    (self.cache / "LATE-CHILD.py").write_text("NOT CREATED")
                finally:
                    (self.root / "cleanup.json").write_text("SYNTHETIC")
                    cleaned.append(True)
        self.assertEqual(cleaned, [True])
        self.assertFalse((self.cache / "LATE-CHILD.py").exists())
        self.assertTrue((self.root / "cleanup.json").is_file())

    def test_cache_mutators_and_destination_renames_are_denied(self):
        source = self.root / "job.txt"
        source.write_text("SYNTHETIC")
        actions = (
            lambda: self.index.unlink(),
            lambda: (self.cache / "NEW").mkdir(),
            lambda: source.replace(self.cache / "REPLACED"),
            lambda: self.index.chmod(0o600),
        )
        for action in actions:
            with self.subTest(action=action):
                with self.assertRaisesRegex(RuntimeError, "자동 변경"):
                    with security.automation_cache():
                        action()
        self.assertEqual(self.index.read_bytes(), b"SYNTHETIC EXISTING INDEX")
        self.assertTrue(source.exists())

    def test_caller_exception_and_nested_context_restore_only_process_flags(self):
        original = sys.dont_write_bytecode
        with self.assertRaisesRegex(ValueError, "SYNTHETIC"):
            with security.automation_cache():
                with security.automation_cache():
                    self.assertTrue(sys.dont_write_bytecode)
                self.assertTrue(sys.dont_write_bytecode)
                raise ValueError("SYNTHETIC caller failure")
        self.assertEqual(sys.dont_write_bytecode, original)
        (self.cache / "OWN-SYNTHETIC-AFTER.txt").write_text("Outside guard")

    def test_swallowed_lookup_mutation_cannot_enter_caller(self):
        def swallowed(*args):
            try:
                self.index.write_bytes(b"NOT WRITTEN")
            except security._CacheMutationBlocked:
                return CachedWrapper

        self.gencache.GetClassForCLSID.side_effect = swallowed
        with self.assertRaisesRegex(RuntimeError, "자동 변경"):
            with security.automation_cache():
                self.fail("Swallowed mutation entered caller")
        self.client.DispatchEx.assert_not_called()
        self.assertEqual(self.index.read_bytes(), b"SYNTHETIC EXISTING INDEX")

    def test_swallowed_caller_mutation_cannot_be_success(self):
        with self.assertRaisesRegex(RuntimeError, "자동 변경"):
            with security.automation_cache():
                try:
                    self.index.write_bytes(b"NOT WRITTEN")
                except security._CacheMutationBlocked:
                    pass
        self.assertEqual(self.index.read_bytes(), b"SYNTHETIC EXISTING INDEX")

    def test_diagnostic_cache_denial_cannot_prevent_cleanup_or_hide_failure(self):
        from hwp_worker_trace import WorkerTrace

        trace = WorkerTrace.__new__(WorkerTrace)
        trace.path = self.cache / "SYNTHETIC-TRACE.jsonl"
        trace.path.write_bytes(b"")
        trace.phase, trace.started, trace.failed = 0, time.monotonic(), False
        cleanup = []
        with self.assertRaisesRegex(RuntimeError, "자동 변경"):
            with security.automation_cache():
                try:
                    trace.event("clear_begin")
                finally:
                    cleanup.append("QUIT STILL RUNS")
        self.assertTrue(trace.failed)
        self.assertEqual(cleanup, ["QUIT STILL RUNS"])
        self.assertEqual(trace.path.read_bytes(), b"")

    def test_other_thread_trace_is_not_disabled(self):
        errors = []

        def trace():
            try:
                (self.root / "thread-trace.json").write_text("SYNTHETIC")
            except BaseException as error:
                errors.append(type(error).__name__)

        with security.automation_cache():
            thread = threading.Thread(target=trace)
            thread.start()
            thread.join(5)
            self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertTrue((self.root / "thread-trace.json").is_file())

    def test_invalid_cache_location_fails_before_lookup(self):
        self.package.__dict__["__gen_path__"] = None
        with self.assertRaisesRegex(RuntimeError, "위치"):
            with security.automation_cache():
                self.fail("Unknown cache path entered caller")
        self.gencache.GetClassForCLSID.assert_not_called()

    def test_missing_cold_index_does_not_import_client_or_rebuild(self):
        self.index.unlink()
        sys.modules.pop("win32com.client")
        with self.assertRaisesRegex(RuntimeError, "준비"):
            with security.automation_cache():
                self.fail("Missing cold index entered caller")
        self.gencache.GetClassForCLSID.assert_not_called()
        self.assertNotIn("win32com.client", sys.modules)
        self.assertFalse(self.index.exists())

    def test_readonly_archive_loader_does_not_require_a_physical_index_or_flip_flags(
        self,
    ):
        self.index.unlink()
        sys.modules.pop("win32com.client")

        class ArchiveLoader:
            archive = "SYNTHETIC.zip"

            def load_module(self, fullname):
                raise AssertionError("Synthetic import is supplied by the fixture")

        self.package.__loader__ = ArchiveLoader()
        self.gencache.is_readonly = True
        original_import = builtins.__import__

        def embedded(name, globals=None, locals=None, fromlist=(), level=0):
            if name == "win32com.client":
                sys.modules[name] = self.client
                return self.client if fromlist else self.package
            return original_import(name, globals, locals, fromlist, level)

        with patch.object(builtins, "__import__", side_effect=embedded):
            with security.automation_cache():
                self.assertTrue(self.gencache.is_readonly)
        self.assertTrue(self.gencache.is_readonly)
        self.assertFalse(self.index.exists())
        self.client.DispatchEx.assert_not_called()

    def test_real_export_entry_rejects_cache_before_com_initialization(self):
        import native_hwp

        com = SimpleNamespace(CoInitialize=Mock(), CoUninitialize=Mock())
        self.gencache.GetClassForCLSID.side_effect = None
        self.gencache.GetClassForCLSID.return_value = None
        with patch.dict(sys.modules, {"pythoncom": com}):
            with self.assertRaisesRegex(RuntimeError, "wrapper"):
                native_hwp.export(
                    self.root / "NOT-OPENED.hwp", self.root / "NOT-CREATED.pdf"
                )
        self.client.DispatchEx.assert_not_called()
        com.CoInitialize.assert_not_called()
        com.CoUninitialize.assert_not_called()
        self.assertFalse((self.root / "NOT-CREATED.pdf").exists())

    def test_all_runtime_dispatches_and_client_imports_are_inside_shared_guard(self):
        root = Path(security.__file__).parent
        sessions = 0
        for name in (
            "hwp_prepare_native.py",
            "hwp_text_apply.py",
            "hwp_contract_apply.py",
            "native_hwp.py",
        ):
            with self.subTest(module=name):
                tree = ast.parse((root / name).read_bytes())
                for node in ast.walk(tree):
                    self.assertFalse(
                        isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Attribute)
                        and node.func.attr == "DispatchEx"
                    )
                    self.assertFalse(
                        isinstance(node, ast.Import)
                        and any(alias.name == "win32com.client" for alias in node.names)
                    )
                    if (
                        isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Name)
                        and node.func.id == "owned_session"
                    ):
                        sessions += 1
        self.assertEqual(sessions, 5)
        tree = ast.parse((root / "hwp_native_owner.py").read_bytes())
        parents = {
            child: node
            for node in ast.walk(tree)
            for child in ast.iter_child_nodes(node)
        }
        dispatches = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "DispatchEx"
        ]
        self.assertEqual(len(dispatches), 1)
        ancestor = parents.get(dispatches[0])
        guarded = False
        while ancestor is not None:
            if isinstance(ancestor, ast.With) and any(
                isinstance(item.context_expr, ast.Call)
                and isinstance(item.context_expr.func, ast.Name)
                and item.context_expr.func.id == "automation_cache"
                for item in ancestor.items
            ):
                guarded = True
            ancestor = parents.get(ancestor)
        self.assertTrue(guarded)

    def test_frozen_selftest_resolves_client_without_executing_cache_import(self):
        path = Path(security.__file__).with_name("packaged_smoke_runtime.py")
        tree = ast.parse(path.read_bytes())
        imports = [
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        ]
        self.assertNotIn("win32com.client", imports)
        self.assertTrue(
            any(
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "find_spec"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and node.args[0].value == "win32com.client"
                for node in ast.walk(tree)
            )
        )

    def test_suppressed_audit_registration_fails_closed_in_fresh_python(self):
        code = (
            "import sys;sys.path.insert(0,sys.argv[1]);import hwp_security as s;"
            "sys.addaudithook=lambda hook:None\n"
            "try:\n with s.automation_cache(): raise AssertionError('entered')\n"
            "except RuntimeError:\n assert 'win32com' not in sys.modules\n"
            "else: raise AssertionError('guard missing')\n"
            "print('BLOCKED BEFORE COM')\n"
        )
        result = subprocess.run(
            [
                sys.executable,
                "-I",
                "-B",
                "-c",
                code,
                str(Path(security.__file__).parent),
            ],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "BLOCKED BEFORE COM")


if __name__ == "__main__":
    unittest.main()
