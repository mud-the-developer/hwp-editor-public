"""Synthetic exact-range intake and standard-Tk wizard behavior; no Native/model."""

from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import tkinter as tk
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import hwp_report_harness as harness
import hwp_report_project as projects
import hwp_report_ui as ui
import hwp_review as review
import hwp_template_draft as draft
from test_template_draft import fixture, mutate, paragraph


class ReportInputFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.source = self.root / "source.hwpx"
        self.payload = self.root / "payload.hwpx"
        self.source.write_bytes(fixture())
        self.payload.write_bytes(fixture(True))

    def basic(self, **kwargs):
        options = {
            "label": "SYNTHETIC ONLY", "caption_policy": "legacy",
            "goal": "Keep exact report scope", "decisions": ["Keep original geometry"],
        }
        options.update(kwargs)
        return ui.basic_inputs(ui.capture_document(self.source), ui.capture_document(self.payload), [2, 3], [0, 2], **options)


class ReportInputTests(ReportInputFixture):
    def test_preview_numbers_are_actual_top_level_indexes_and_nested_text_stays_in_owner(self):
        source = ui.capture_document(self.source)
        self.assertEqual(source.paragraphs[2].text, "OLD PLACEHOLDER")
        self.assertEqual(ui.selected_range(source, [2], source=True), [2, 3])
        payload = ui.capture_document(self.payload)
        self.assertIn("TOY CAPTION", payload.paragraphs[1].text)
        self.assertEqual(ui.selected_range(payload, [0, 1]), [0, 2])
        profile, feedback = self.basic()
        info = projects.create(profile, feedback, base=self.root / "Projects")
        self.assertTrue(info.gate_passed, info.issues)
        self.assertEqual(projects.read_inputs(info.root)[0]["jobs"][0]["target_range"], [2, 3])

    def test_discontinuous_and_boundary_replacement_are_not_guessed(self):
        source = ui.capture_document(self.source)
        for indexes in ([0], [3], [1, 3], []):
            with self.subTest(indexes=indexes), self.assertRaises(ValueError):
                ui.selected_range(source, indexes, source=True)
        for bounds in ((True, 3), (2, 2), (2, 10), (-1, 3)):
            with self.subTest(bounds=bounds), self.assertRaises(ValueError):
                ui.exact_range(source, *bounds, source=True)

    def test_nonparagraph_child_retains_its_index_but_cannot_be_selected(self):
        def change(section):
            section[2].tag = draft.HP + "unsupported"
        self.source.write_bytes(mutate(self.source.read_bytes(), draft.PART, change))
        preview = ui.capture_document(self.source)
        self.assertFalse(preview.paragraphs[2].is_paragraph)
        with self.assertRaises(ValueError):
            ui.exact_range(preview, 2, 3, source=True)
        self.assertEqual(preview.paragraphs[3].text, "OTHER SECTION")

    def test_hash_and_paragraph_preview_use_the_same_captured_bytes(self):
        original = self.payload.read_bytes()
        parse = draft.parts_from
        def change_path_after_capture(raw):
            self.payload.write_bytes(fixture())
            return parse(raw)
        with patch.object(draft, "parts_from", side_effect=change_path_after_capture):
            preview = ui.capture_document(self.payload)
        self.assertEqual(preview.sha256, review.digest(original))
        self.assertEqual(preview.paragraphs[0].text, "IMPORTED BODY")
        self.assertNotEqual(preview.sha256, review.file_hash(self.payload))
        profile, feedback = ui.basic_inputs(
            ui.capture_document(self.source), preview, [2, 3], [0, 2],
            label="SYNTHETIC drift", caption_policy="legacy", goal="Keep captured draft", decisions=[],
        )
        failed = projects.create(profile, feedback, base=self.root / "Projects")
        self.assertFalse(failed.gate_passed)
        self.assertEqual(projects.read_inputs(failed.root)[0]["jobs"][0]["payload_sha256"], review.digest(original))

    def test_caption_policy_requires_explicit_selection_and_is_pinned(self):
        with self.assertRaises(ValueError):
            self.basic(caption_policy=None)
        modern, _ = self.basic(caption_policy=draft.captions.POLICY)
        self.assertEqual(modern["jobs"][0]["caption_policy"], draft.captions.POLICY)
        legacy, _ = self.basic()
        self.assertNotIn("caption_policy", legacy["jobs"][0])

    def test_own_excerpt_is_optional_but_declaration_is_required_when_selected(self):
        evidence = self.root / "own-excerpt.txt"
        evidence.write_text("Our synthetic observation is 12.5 ms", encoding="utf-8")
        pinned = ui.capture_evidence(evidence)
        with self.assertRaises(ValueError):
            self.basic(evidence=pinned)
        def change(section):
            section[0][0].find(draft.HP + "t").text = "Our synthetic observation is 12.5 ms"
        self.payload.write_bytes(mutate(self.payload.read_bytes(), draft.PART, change))
        profile, feedback = self.basic(evidence=pinned, own_achievement=True)
        info = projects.create(profile, feedback, base=self.root / "Projects")
        self.assertTrue(info.gate_passed, info.issues)
        evidence.write_text("Different evidence", encoding="utf-8")
        self.assertFalse(projects.inspect(info.root).gate_passed)

    def test_adding_excerpt_preserves_prior_numeric_evidence_and_id_collisions(self):
        def change(section):
            section[0][0].find(draft.HP + "t").text = "SYNTHETIC 12.5 ms and 8.5 ms"
        self.payload.write_bytes(mutate(self.payload.read_bytes(), draft.PART, change))
        old = self.root / "prior-evidence.txt"
        old.write_text("SYNTHETIC own observation 12.5 ms", encoding="utf-8")
        extra = self.root / "extra-evidence.txt"
        extra.write_text("SYNTHETIC own observation 8.5 ms", encoding="utf-8")
        policy = {
            "schema": harness.FEEDBACK, "mode": "authoring",
            "goal": "Retain both synthetic observations", "decisions": [],
            "sources": [{
                "id": "own-excerpt", "path": str(old),
                "sha256": review.file_hash(old), "ownership": "own", "use": "achievement",
            }],
        }
        profile, feedback = self.basic(
            feedback=policy, evidence=ui.capture_evidence(extra), own_achievement=True
        )
        info = projects.create(profile, feedback, base=self.root / "Projects")
        self.assertTrue(info.gate_passed, info.issues)
        self.assertEqual(info.phase, "awaiting_draft_permission")
        self.assertFalse((info.review / "approved-plan.json").exists())

    def test_evidence_selection_rejects_non_utf8_oversized_or_conversion_inputs(self):
        path = self.root / "excerpt.txt"
        path.write_bytes(b"\xff")
        with self.assertRaises(ValueError):
            ui.capture_evidence(path)
        path.write_bytes(b"x" * 524289)
        with self.assertRaises(ValueError):
            ui.capture_evidence(path)
        pdf = self.root / "excerpt.pdf"
        pdf.write_bytes(b"%PDF")
        with self.assertRaises(ValueError):
            ui.capture_evidence(pdf)

    def test_advanced_two_jobs_four_ranges_and_additional_rules_stay_usable(self):
        def extend(section):
            section.extend([paragraph("SYNTHETIC appendix A", 100), paragraph("SYNTHETIC appendix B", 101)])
        self.payload.write_bytes(mutate(self.payload.read_bytes(), draft.PART, extend))
        profile, feedback = self.basic()
        profile["jobs"][0]["payload_ranges"] = [[0, 1], [1, 2], [2, 3], [3, 4]]
        profile["jobs"].append(deepcopy(profile["jobs"][0]))
        profile["jobs"][1]["label"] = "Second explicit job"
        feedback.update(terms=[{"short": "RTT", "definition": "Round-trip time (RTT)", "avoid": ["왕복 지연"]}], protected_text=[[], []])
        pfile, ffile = self.root / "profile.json", self.root / "feedback.json"
        pfile.write_text(json.dumps(profile), encoding="utf-8")
        ffile.write_text(json.dumps(feedback), encoding="utf-8")
        imported_profile = ui._import_json(pfile, "profile")
        imported_feedback = ui._import_json(ffile, "feedback")
        self.assertEqual(imported_profile["jobs"][0]["payload_ranges"], profile["jobs"][0]["payload_ranges"])
        self.assertEqual(imported_feedback, feedback)
        info = projects.create(imported_profile, imported_feedback, base=self.root / "Projects")
        self.assertTrue(info.gate_passed, info.issues)
        self.assertEqual(projects.read_inputs(info.root), (imported_profile, feedback))

    def test_advanced_strict_modes_and_pinned_baselines_are_not_downgraded(self):
        baseline = self.root / "locked.hwpx"
        baseline.write_bytes(self.payload.read_bytes())
        for mode in ("terminology", "image-layout"):
            feedback = {
                "schema": harness.FEEDBACK, "mode": mode, "goal": "Keep locked scope",
                "decisions": ["Only the explicit requested change"],
                "baselines": [{"path": str(baseline), "sha256": review.file_hash(baseline)}],
                "protected_text": [["Original quote"]],
            }
            if mode == "image-layout":
                feedback["image_scope"] = [[0]]
            path = self.root / (mode + ".json")
            path.write_text(json.dumps(feedback), encoding="utf-8")
            imported = ui._import_json(path, "feedback")
            self.assertEqual(imported["mode"], mode)
            self.assertTrue(Path(imported["baselines"][0]["path"]).samefile(baseline))
            self.assertEqual(imported.get("image_scope"), feedback.get("image_scope"))
            with self.assertRaises(ValueError):
                self.basic(feedback=imported)

    def test_import_rejects_duplicate_keys_and_bogus_ranges_without_execution(self):
        path = self.root / "bad.json"
        path.write_text('{"contract":"x","contract":"template-review-draft-v1","jobs":[]}', encoding="utf-8")
        with self.assertRaises(ValueError):
            ui._import_json(path, "profile")
        profile, _ = self.basic()
        profile["jobs"][0]["payload_ranges"] = [[0, 2], [1, 2]]
        path.write_text(json.dumps(profile), encoding="utf-8")
        with self.assertRaises(ValueError):
            ui._import_json(path, "profile")

    def test_newer_or_cancelled_selections_hide_stale_completed_previews(self):
        mailbox = ui.InputMailbox()
        old = mailbox.begin("source")
        old_preview = ui.capture_document(self.source)
        new = mailbox.begin("source")
        new_preview = ui.capture_document(self.payload)
        mailbox.publish("source", old, old_preview)
        mailbox.publish("source", new, new_preview)
        received = mailbox.take()
        self.assertEqual(received, [("source", new_preview, None)])
        self.assertTrue(old.is_set())
        cancelled = mailbox.begin("payload")
        mailbox.close()
        mailbox.publish("payload", cancelled, old_preview)
        self.assertFalse(mailbox.take())
        self.assertTrue(cancelled.is_set())
        with self.assertRaises(InterruptedError):
            ui.capture_document(self.source, cancelled)


class ReportWizardTests(ReportInputFixture):
    """Real Tk widgets, deterministic file completion delivery, no browser/server."""

    def setUp(self):
        super().setUp()
        try:
            self.parent = tk.Tk()
        except tk.TclError as exc:
            self.skipTest("Tk display unavailable: " + str(exc))
        self.parent.withdraw()
        self.addCleanup(self.parent.destroy)
        self.window = None

    def make_window(self, callback, seed=None):
        self.window = ui.ReportDraftWindow(self.parent, callback, seed=seed)
        self.addCleanup(self.window.close)
        return self.window

    def complete_file(self, key, path):
        self.window._load(key, path)
        result = self.window._mailbox._results.get(timeout=10)
        self.window._mailbox._results.put(result)
        self.window._poll()

    def test_actual_basic_widgets_prepare_a_valid_job_without_manual_hashes_or_json(self):
        prepared = []
        def prepare(profile, feedback):
            prepared.append(projects.create(profile, feedback, base=self.root / "Projects"))
        window = self.make_window(prepare)
        self.complete_file("source", self.source)
        self.complete_file("payload", self.payload)
        window._documents["source"][1].selection_set("2")
        window._use_selection("source")
        window._documents["payload"][1].selection_set("0", "1")
        window._use_selection("payload")
        window.goal.insert("1.0", "Retain exact scope")
        window.decisions.insert("1.0", "Retain panel direction")
        window.caption.set("기존 그림 하단 캡션 정책")
        window._prepare()
        self.assertTrue(prepared[0].gate_passed, prepared[0].issues)
        self.assertEqual(projects.read_inputs(prepared[0].root)[0]["jobs"][0]["payload_ranges"], [[0, 2]])
        self.assertTrue(window._closed)
        self.assertFalse((prepared[0].review / "approved-plan.json").exists())

    def test_keyboard_focus_reveals_scrolled_goal_without_preparing(self):
        prepared = []
        window = self.make_window(lambda profile, feedback: prepared.append(True))
        self.parent.deiconify()
        window.top.geometry("750x620")
        self.parent.update()
        window.goal.focus_force()
        self.parent.update()
        top = window.goal.winfo_rooty() - window.canvas.winfo_rooty()
        bottom = top + window.goal.winfo_height()
        self.assertGreaterEqual(top, 0)
        self.assertLessEqual(bottom, window.canvas.winfo_height())
        self.assertFalse(prepared)

    def test_revision_widgets_keep_multiline_original_decisions_and_reuse_exact_project(self):
        profile, feedback = self.basic()
        profile["jobs"].append(deepcopy(profile["jobs"][0]))
        feedback["decisions"] = ["  Keep exact wording  ", "Decision with\na preserved newline"]
        first = projects.create(profile, feedback, base=self.root / "Projects")
        reopened = []
        window = self.make_window(lambda p, f: reopened.append(projects.create(p, f, base=self.root / "Projects", previous=first.root)), seed=projects.read_inputs(first.root))
        self.assertEqual(window.notebook.index("current"), 1)
        window._prepare()
        self.assertEqual(reopened[0].root, first.root)
        self.assertEqual(projects.read_inputs(first.root), (profile, feedback))

    def test_failed_full_feedback_import_cannot_silently_fall_back_to_basic_authoring(self):
        called = []
        window = self.make_window(lambda p, f: called.append((p, f)))
        invalid = self.root / "invalid-feedback.json"
        invalid.write_bytes(b'{"schema":"invalid"}')
        self.complete_file("feedback", invalid)
        with patch.object(ui.messagebox, "showerror"):
            window._prepare()
        self.assertFalse(called)
        self.assertFalse(window._closed)
        self.assertIn("feedback", window._invalid)

    def test_close_cancels_only_owned_work_and_ignores_late_worker_completion(self):
        called = []
        window = self.make_window(lambda p, f: called.append((p, f)))
        token = window._mailbox.begin("source")
        window.close()
        window._mailbox.publish("source", token, ui.capture_document(self.source))
        self.assertFalse(window._mailbox.take())
        self.assertTrue(token.is_set())
        self.assertFalse(called)
        self.assertFalse(window._after)


if __name__ == "__main__":
    unittest.main()
