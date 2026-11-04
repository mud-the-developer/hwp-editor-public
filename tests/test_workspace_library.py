"""SYNTHETIC library/export tests. No Native/source approvals/real owner scan."""

from pathlib import Path
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import hwp_workspace as w
import hwp_review as r
import hwp_desktop as d
from test_desktop import fixture, snapshot, DesktopWidgetTests


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.base = self.root / "Projects"
        self.base.mkdir()
        self.ready = self.base / "ready"
        fixture(self.ready)
        self.manifest = r.read_json(self.ready / "manifest.json")

    def notes(self, text="SYNTHETIC NOTE ONLY"):
        payload = {
            "workspace_id": self.manifest["workspace_id"],
            "comments": [
                {
                    "id": "SYNTHETIC",
                    "page": 1,
                    "anchor_id": None,
                    "region": [0.2, 0.3],
                    "comment": text,
                }
            ],
        }
        r.write_json(self.ready / "comments.json", payload)

    def test_readonly_scan_does_not_hash_sources_start_native_or_write_config(self):
        before = snapshot(self.base)
        with patch.object(
            r,
            "verify_baseline",
            side_effect=AssertionError("No heavy verification during scan"),
        ):
            listing = w.scan_library(self.base)
        self.assertEqual(listing.entries[0].state, "prepared")
        self.assertIn("다시 검증", listing.entries[0].note)
        self.assertEqual(snapshot(self.base), before)

    def test_workspace_empty_and_nested_filename_listing_has_no_intake_or_writes(self):
        folder = self.root / "work"
        folder.mkdir()
        self.assertEqual(w.scan_workspace()["root"], "")
        self.assertEqual(w.scan_workspace(folder)["documents"], [])
        nested = folder / "양식"
        nested.mkdir()
        for name in ("서식.HWPX", "본문.hwpx", "지원하지 않음.hwtx", "자료.txt"):
            (nested / name).touch()
        before = snapshot(folder)
        with (
            patch.object(w.projects, "inspect", side_effect=AssertionError("No live admission during listing")),
            patch.object(w.projects, "new_document", side_effect=AssertionError("No capture during listing")),
            patch.object(w.projects.draft, "parts_from", side_effect=AssertionError("No document reads")),
        ):
            listing = w.scan_workspace(folder)
        self.assertEqual([row["relative_path"] for row in listing["documents"]],
                         ["양식/본문.hwpx", "양식/서식.HWPX"])
        self.assertTrue(all(row["project_root"] == "" for row in listing["documents"]))
        self.assertEqual(snapshot(folder), before)
        self.assertFalse((folder / ".hwpreview").exists())

    def test_workspace_skips_managed_and_saved_project_interiors(self):
        folder = self.root / "work"
        folder.mkdir()
        hidden = folder / ".HWPReview"
        hidden.mkdir()
        (hidden / "snapshot.hwpx").touch()
        saved = folder / "saved"
        saved.mkdir()
        (saved / "report-project.json").write_text("{}", encoding="utf-8")
        (saved / "source.hwpx").touch()
        (folder / "document.hwpx").touch()
        listing = w.scan_workspace(folder)
        self.assertEqual([row["name"] for row in listing["documents"]], ["document.hwpx"])
        self.assertEqual(listing["projects"][0]["root"], str(saved))
        for path in (hidden / "snapshot.hwpx", saved / "source.hwpx"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                w.workspace_document(folder, path)
        with self.assertRaises(ValueError):
            w.work_folder(saved)

    def test_workspace_bounds_and_access_failures_are_visible_and_deterministic(self):
        folder = self.root / "work"
        folder.mkdir()
        for name in ("z.hwpx", "a.hwpx", "b.hwpx"):
            (folder / name).touch()
        listing = w.scan_workspace(folder, limit=1)
        self.assertEqual([row["name"] for row in listing["documents"]], ["a.hwpx"])
        self.assertTrue(listing["limited"])
        self.assertTrue(listing["issues"])
        overflow = w.scan_workspace(folder, visit_limit=1)
        self.assertEqual(overflow["documents"], [])
        self.assertTrue(overflow["limited"])
        blocked = folder / "blocked"
        blocked.mkdir()
        original = w.os.scandir
        def scandir(path):
            if Path(path) == blocked:
                raise PermissionError("SYNTHETIC ACCESS DENIED")
            return original(path)
        with patch.object(w.os, "scandir", side_effect=scandir):
            listing = w.scan_workspace(folder)
        self.assertTrue(any("blocked" in item for item in listing["issues"]))
        deeper = blocked / "deeper"
        deeper.mkdir()
        self.assertTrue(w.scan_workspace(folder, depth_limit=1)["limited"])
        for kwargs in ({"limit": True}, {"visit_limit": 0}, {"depth_limit": 33}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                w.scan_workspace(folder, **kwargs)

    def test_workspace_lexical_reparse_and_hardlink_guards_are_reused(self):
        folder = self.root / "work"
        folder.mkdir()
        linked = folder / "linked"
        linked.mkdir()
        (linked / "hidden.hwpx").touch()
        guard = w.projects._unlinked
        def unlinked(path, **kwargs):
            if linked in (Path(path), *Path(path).parents):
                raise ValueError("SYNTHETIC REPARSE POINT")
            return guard(path, **kwargs)
        with patch.object(w.projects, "_unlinked", side_effect=unlinked):
            listing = w.scan_workspace(folder)
            self.assertEqual(listing["documents"], [])
            self.assertTrue(listing["issues"])
            with self.assertRaises(ValueError):
                w.work_folder(linked)
            with self.assertRaises(ValueError):
                w.workspace_document(folder, linked / "new.hwpx", new=True)
        source = folder / "regular.hwpx"
        source.touch()
        alias = folder / "alias.hwpx"
        try:
            os.link(source, alias)
        except OSError as error:
            self.skipTest("Hardlink unavailable: " + str(error))
        self.assertEqual(w.scan_workspace(folder)["documents"],
                         [{"path": str(linked / "hidden.hwpx"), "name": "hidden.hwpx",
                           "relative_path": "linked/hidden.hwpx", "project_root": ""}])
        with self.assertRaises(ValueError):
            w.workspace_document(folder, alias)

    def test_all_states_are_visible_without_guessing_or_repair(self):
        staged = self.base / "staged"
        staged.mkdir()
        (staged / "ONE.hwp").write_bytes(b"SYNTHETIC")
        partial = self.base / "partial"
        partial.mkdir()
        (partial / "_review").mkdir()
        malformed = self.base / "bad"
        malformed.mkdir()
        (malformed / "manifest.json").write_text("{")
        empty = self.base / "empty"
        empty.mkdir()
        states = {row.root.name: row.state for row in w.scan_library(self.base).entries}
        self.assertEqual(
            states,
            {
                "ready": "prepared",
                "staged": "staged",
                "partial": "incomplete",
                "bad": "attention",
                "empty": "empty",
            },
        )
        self.assertTrue((partial / "_review").exists())

    def test_nested_workspace_uses_document_name_and_page_count(self):
        nested = self.base / "nested"
        nested.mkdir()
        fixture(nested / "_review")
        row = next(
            row for row in w.scan_library(self.base).entries if row.root == nested
        )
        self.assertEqual(row.name, "source-fixture.hwp")
        self.assertEqual(row.pages, 1)

    def test_missing_library_never_creates_directory_or_preferences(self):
        missing = self.root / "missing"
        self.assertEqual(w.scan_library(missing).entries, ())
        self.assertFalse(missing.exists())
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(w.scan_library().base)

    def test_names_and_states_filter_case_insensitively_without_fulltext_upload(self):
        listing = w.scan_library(self.base)
        self.assertEqual(
            len(w.filter_library(listing, "SOURCE-FIXTURE", "prepared")), 1
        )
        self.assertEqual(w.filter_library(listing, "missing"), ())
        self.assertEqual(w.filter_library(listing, state="incomplete"), ())
        with self.assertRaises(ValueError):
            w.filter_library(listing, state="approved")

    def test_bounded_listing_marks_truncation_and_rejects_invalid_limit(self):
        (self.base / "second").mkdir()
        listing = w.scan_library(self.base, limit=1)
        self.assertEqual(len(listing.entries), 1)
        self.assertTrue(listing.limited)
        for value in (0, 201, True):
            with self.assertRaises(ValueError):
                w.scan_library(self.base, limit=value)

    def test_large_metadata_is_attention_not_silently_prepared(self):
        with patch.object(w, "MAX_METADATA", 10):
            listing = w.scan_library(self.base)
        self.assertEqual(listing.entries[0].state, "attention")

    def test_linked_folder_and_linked_destination_are_not_traversed(self):
        linked = self.base / "linked"
        linked.mkdir()
        with patch.object(w, "linked", side_effect=lambda path: path == linked):
            row = next(
                row for row in w.scan_library(self.base).entries if row.root == linked
            )
            self.assertEqual(row.state, "attention")
        target = self.root / "linked.md"
        with patch.object(w, "linked", side_effect=lambda path: path == target):
            with self.assertRaises(ValueError):
                w.export_notes(self.ready, target)
        self.assertFalse(target.exists())

    def test_export_malformed_unicode_fails_before_creating_output(self):
        self.notes("SYNTHETIC malformed \\ud800")
        payload = r.read_json(self.ready / "comments.json")
        payload["comments"][0]["comment"] = "\\ud800".encode().decode("unicode_escape")
        import json

        (self.ready / "comments.json").write_text(json.dumps(payload), encoding="utf-8")
        target = self.root / "unicode.md"
        with self.assertRaises(UnicodeEncodeError):
            w.export_notes(self.ready, target)
        self.assertFalse(target.exists())

    def test_export_oversized_notes_block_before_hashing_the_notes(self):
        target = self.root / "large.md"
        with (
            patch.object(w, "MAX_COMMENTS", 1),
            patch.object(r, "file_hash", wraps=r.file_hash) as hash_file,
        ):
            with self.assertRaises(ValueError):
                w.export_notes(self.ready, target)
            self.assertFalse(
                any(
                    call.args[0] == self.ready / "comments.json"
                    for call in hash_file.call_args_list
                )
            )
        self.assertFalse(target.exists())

    def test_multiple_inputs_are_attention_without_automatic_selection(self):
        folder = self.base / "multiple"
        folder.mkdir()
        (folder / "A.hwp").touch()
        (folder / "B.hwp").touch()
        self.assertEqual(w.describe_folder(folder).state, "attention")

    def test_export_has_source_identity_page_and_exact_saved_text_not_approval(self):
        self.notes("SYNTHETIC <script>alert(1)</script>\n~~~\nDO NOT EXECUTE")
        before = snapshot(self.ready)
        target = self.root / "notes.md"
        result = w.export_notes(self.ready, target)
        text = target.read_text(encoding="utf-8")
        self.assertIn(self.manifest["source_audit"]["sha256"], text)
        self.assertIn("## 1쪽", text)
        self.assertIn("DO NOT EXECUTE", text)
        self.assertIn("~~~~text", text)
        self.assertIn("수정 승인", text)
        self.assertEqual(result["notes"], 1)
        self.assertEqual(snapshot(self.ready), before)
        self.assertFalse((self.ready / "approved-plan.json").exists())

    def test_existing_export_file_is_never_overwritten(self):
        target = self.root / "existing.md"
        target.write_text("OWNER FILE")
        with self.assertRaises(FileExistsError):
            w.export_notes(self.ready, target)
        self.assertEqual(target.read_text(), "OWNER FILE")

    def test_workspace_and_project_root_destinations_are_forbidden(self):
        with self.assertRaises(ValueError):
            w.export_notes(self.ready, self.ready / "notes.md")
        root = self.base / "nested"
        root.mkdir()
        fixture(root / "_review")
        with self.assertRaises(ValueError):
            w.export_notes(root / "_review", root / "notes.md")
        with self.assertRaises(ValueError):
            w.export_notes(self.ready, self.root / "approved-plan.json")

    def test_changed_baseline_blocks_export_before_creating_file(self):
        (self.ready / "source-fixture.hwp").write_bytes(b"CHANGED SYNTHETIC")
        target = self.root / "blocked.md"
        with self.assertRaises(ValueError):
            w.export_notes(self.ready, target)
        self.assertFalse(target.exists())

    def test_invalid_page_or_envelope_blocks_export_before_write(self):
        self.notes()
        payload = r.read_json(self.ready / "comments.json")
        payload["comments"][0]["page"] = 9
        r.write_json(self.ready / "comments.json", payload)
        target = self.root / "blocked.md"
        with self.assertRaises(ValueError):
            w.export_notes(self.ready, target)
        self.assertFalse(target.exists())

    def test_export_race_is_blocked_when_saved_notes_change_during_read(self):
        self.notes()
        original = w.bounded_json

        def read(path, maximum):
            payload = original(path, maximum)
            self.notes("CHANGED SYNTHETIC NOTE")
            return payload

        target = self.root / "race.md"
        with patch.object(w, "bounded_json", side_effect=read):
            with self.assertRaises(ValueError):
                w.export_notes(self.ready, target)
        self.assertFalse(target.exists())

    def test_empty_saved_notes_are_honest_and_not_invented(self):
        target = self.root / "empty.md"
        self.assertEqual(w.export_notes(self.ready, target)["notes"], 0)
        self.assertIn("메모가 없습니다", target.read_text(encoding="utf-8"))


class LibraryWidgetTests(DesktopWidgetTests):
    def open_fixture_library(self, base):
        with patch.dict(os.environ, {"LOCALAPPDATA": str(base)}):
            self.app.show_library()
            self.wait()
        window = self.app.library_window
        if window is None:
            raise AssertionError("Synthetic library window was not created")
        return window

    def test_library_can_close_while_scan_runs_without_corrupting_current_document(
        self,
    ):
        self.app.info = self.info
        self.app.path.set(str(self.info.path))
        self.app.info = self.info
        with tempfile.TemporaryDirectory() as temp:
            window = self.open_fixture_library(Path(temp))
            window.close()
            self.assertIsNone(self.app.library_window)
            self.assertEqual(self.app.info, self.info)

    def test_selecting_prepared_row_uses_full_existing_workspace_inspection(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp).resolve()
            folder = base / "HwpReview/Projects/ONE"
            fixture(folder)
            window = self.open_fixture_library(base)
            row = window.tree.get_children()[0]
            window.tree.selection_set(row)
            with patch.object(
                d, "inspect_workspace", wraps=d.inspect_workspace
            ) as inspect:
                window.open_selected()
                self.wait()
                inspect.assert_called_once_with(folder)
            if self.app.info is None:
                raise AssertionError("Synthetic prepared workspace was not opened")
            self.assertEqual(self.app.info.path, folder)

    def test_bad_source_from_library_cannot_restore_ready_state(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp).resolve()
            folder = base / "HwpReview/Projects/ONE"
            fixture(folder)
            (folder / "source-fixture.hwp").write_bytes(b"CHANGED SYNTHETIC")
            window = self.open_fixture_library(base)
            window.tree.selection_set(window.tree.get_children()[0])
            window.open_selected()
            self.wait()
            self.assertIsNone(self.app.info)
            self.assertTrue(self.app.last_error)

    def test_export_picker_cancel_never_writes_or_saves_browser_draft(self):
        self.app.path.set(str(self.info.path))
        self.app.info = self.info
        with (
            patch.object(d.filedialog, "asksaveasfilename", return_value=""),
            patch.object(w, "export_notes") as export,
        ):
            self.app.export_saved_notes()
            export.assert_not_called()
        self.assertFalse(self.app.busy)

    def test_export_ui_keeps_verification_and_reports_saved_note_count(self):
        self.app.path.set(str(self.info.path))
        self.app.info = self.info
        with tempfile.TemporaryDirectory() as temp:
            destination = Path(temp) / "notes.md"
            with patch.object(
                d.filedialog, "asksaveasfilename", return_value=str(destination)
            ):
                self.app.export_saved_notes()
                self.wait()
            self.assertTrue(destination.is_file())
            self.assertIn("메모 0개", self.app.status.get())

    def test_opening_library_is_blocked_during_active_server_or_task(self):
        self.app.busy = True
        self.app.show_library()
        self.assertIsNone(self.app.library_window)
        self.app.busy = False


def load_tests(loader, tests, pattern):
    suite = unittest.TestSuite(loader.loadTestsFromTestCase(WorkspaceTests))
    for name in LibraryWidgetTests.__dict__:
        if name.startswith("test_"):
            suite.addTest(LibraryWidgetTests(name))
    return suite


if __name__ == "__main__":
    unittest.main()
