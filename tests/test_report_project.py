"""Isolated synthetic report projects: retained authority, drift and failures."""

from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import hwp_report_harness as harness
import hwp_report_project as projects
import hwp_report_ui as inputs
import hwp_review as review
import hwp_template_draft as draft
from test_template_draft import fixture, mutate


class ReportProjectTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / "original.hwpx"
        self.payload = self.root / "draft.hwpx"
        self.source.write_bytes(fixture())
        self.payload.write_bytes(fixture(True))
        self.base = self.root / "Projects"
        self.profile, self.feedback = inputs.basic_inputs(
            inputs.capture_document(self.source), inputs.capture_document(self.payload),
            [2, 3], [0, 2], label="SYNTHETIC ONLY", caption_policy="legacy",
            goal="Retain the exact institution scope", decisions=["Keep the pinned panel order"],
        )
        self.deny = patch.object(subprocess, "Popen", side_effect=AssertionError("No document or model process"))
        self.deny.start()
        self.addCleanup(self.deny.stop)

    def create(self, **kwargs):
        return projects.create(self.profile, self.feedback, base=self.base, **kwargs)

    def saved_bytes(self, root):
        return {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}

    def test_real_basic_intake_reopens_original_inputs_without_expanded_terms(self):
        info = self.create()
        self.assertTrue(info.gate_passed, info.issues)
        self.assertEqual(info.phase, "awaiting_draft_permission")
        self.assertEqual(info.review, info.root / "run/review")
        binding = json.loads((info.root / "report-binding.json").read_bytes())
        self.assertEqual(binding["binding_sha256"], info.binding)
        self.assertEqual(projects.read_inputs(info.root), (self.profile, self.feedback))
        self.assertNotIn("terms", projects.read_inputs(info.root)[1])
        self.assertIn("terms", json.loads((info.root / "run/feedback.json").read_bytes()))
        before = self.saved_bytes(info.root)
        reopened = projects.inspect(info.root)
        self.assertTrue(reopened.gate_passed, reopened.issues)
        self.assertEqual(reopened.binding, info.binding)
        self.assertFalse((info.review / "approved-plan.json").exists())
        self.assertFalse(reopened.outputs)
        self.assertEqual(before, self.saved_bytes(info.root))

    def test_exact_usable_project_reused_with_or_without_previous(self):
        first = self.create()
        self.assertTrue(first.gate_passed, first.issues)
        before = self.saved_bytes(first.root)
        siblings = set(self.base.iterdir())
        self.assertEqual(self.create(previous=first.root).root, first.root)
        self.assertEqual(self.create().root, first.root)
        self.assertEqual(set(self.base.iterdir()), siblings)
        self.assertEqual(self.saved_bytes(first.root), before)

    def test_changed_inputs_get_new_unapproved_project_and_keep_old_decisions(self):
        first = self.create()
        before = self.saved_bytes(first.root)
        updated = deepcopy(self.profile)
        updated["jobs"][0]["label"] = "Explicit revised job"
        feedback = {key: value for key, value in self.feedback.items() if key not in ("goal", "decisions")}
        second = projects.create(updated, feedback, base=self.base, previous=first.root)
        self.assertNotEqual(second.root, first.root)
        self.assertTrue(second.gate_passed, second.issues)
        original_profile, preserved = projects.read_inputs(second.root)
        self.assertEqual(original_profile["jobs"][0]["target_range"], self.profile["jobs"][0]["target_range"])
        self.assertEqual(preserved["goal"], self.feedback["goal"])
        self.assertEqual(preserved["decisions"], self.feedback["decisions"])
        self.assertFalse((second.review / "approved-plan.json").exists())
        self.assertEqual(before, self.saved_bytes(first.root))

    def test_unsupported_numbers_keep_a_blocked_owned_attempt(self):
        def change(section):
            section[0][0].find(draft.HP + "t").text = "New unsupported measurement 98765 ms"
        self.payload.write_bytes(mutate(self.payload.read_bytes(), draft.PART, change))
        self.profile["jobs"][0]["payload_sha256"] = review.file_hash(self.payload)
        info = self.create()
        self.assertEqual(info.phase, "blocked")
        self.assertFalse(info.gate_passed)
        self.assertTrue(any("unsupported_numeric_tokens" in issue for issue in info.issues))
        self.assertTrue(projects.has_project_marker(info.root))
        self.assertTrue((info.root / "profile-input.json").is_file())
        self.assertTrue((info.root / "report-binding.json").is_file())
        self.assertFalse(info.outputs)
        self.assertFalse((info.review / "approved-plan.json").exists())

    def test_failed_prepare_keeps_original_snapshots_and_no_replacement_binding(self):
        self.profile["jobs"][0]["payload_sha256"] = "0" * 64
        failed = self.create()
        self.assertEqual(failed.phase, "failed_preserved")
        self.assertFalse(failed.gate_passed)
        self.assertTrue((failed.root / "prepare-failed.json").is_file())
        self.assertFalse((failed.root / "report-binding.json").exists())
        self.assertEqual(projects.read_inputs(failed.root), (self.profile, self.feedback))
        before = self.saved_bytes(failed.root)
        again = self.create(previous=failed.root)
        self.assertNotEqual(again.root, failed.root)
        self.assertEqual(self.saved_bytes(failed.root), before)

    def test_missing_external_binding_is_partial_not_reconstructed_from_harness(self):
        info = self.create()
        binding_path = info.root / "report-binding.json"
        binding_path.unlink()  # synthetic tamper only
        before = self.saved_bytes(info.root)
        reopened = projects.inspect(info.root)
        self.assertEqual(reopened.phase, "preparing")
        self.assertIsNone(reopened.binding)
        self.assertFalse(reopened.gate_passed)
        self.assertEqual(before, self.saved_bytes(info.root))
        self.assertFalse(binding_path.exists())

    def test_changed_harness_cannot_be_rebound_by_reopening(self):
        info = self.create()
        manifest = info.root / "run/harness.json"
        record = json.loads(manifest.read_bytes())
        record["plan_sha256"] = "0" * 64
        manifest.write_text(json.dumps(record), encoding="utf-8")
        binding_before = (info.root / "report-binding.json").read_bytes()
        reopened = projects.inspect(info.root)
        self.assertEqual(reopened.phase, "blocked")
        self.assertFalse(reopened.gate_passed)
        self.assertEqual((info.root / "report-binding.json").read_bytes(), binding_before)

    def test_runtime_drift_does_not_reuse_old_project_authority(self):
        info = self.create()
        before = self.saved_bytes(info.root)
        identity = harness.runtime.identity()
        changed_identity = {**identity, "rules_sha256": {
            **identity["rules_sha256"], "docs/hwp-agent-runtime.md": "0" * 64,
        }}
        with patch.object(harness.runtime, "identity", return_value=changed_identity):
            reopened = projects.inspect(info.root)
            self.assertEqual(reopened.phase, "blocked")
            self.assertFalse(reopened.gate_passed)
            second = self.create(previous=info.root)
            self.assertNotEqual(second.root, info.root)
            self.assertTrue(second.gate_passed, second.issues)
            self.assertFalse((second.review / "approved-plan.json").exists())
        self.assertEqual(before, self.saved_bytes(info.root))

    def test_changed_original_bytes_block_reopen_and_leave_revision_inputs_available(self):
        info = self.create()
        self.payload.write_bytes(fixture())
        reopened = projects.inspect(info.root)
        self.assertFalse(reopened.gate_passed)
        self.assertEqual(reopened.phase, "blocked")
        self.assertEqual(projects.read_inputs(info.root), (self.profile, self.feedback))
        self.assertFalse(reopened.outputs)

    def test_two_job_profile_keeps_all_ranges_and_full_feedback(self):
        self.profile["jobs"].append(deepcopy(self.profile["jobs"][0]))
        self.profile["jobs"][1]["label"] = "Second explicit report"
        self.feedback["terms"] = [{"short": "RTT", "definition": "Round-trip time (RTT)", "avoid": ["왕복 지연"]}]
        info = self.create()
        self.assertTrue(info.gate_passed, info.issues)
        self.assertEqual(projects.read_inputs(info.root), (self.profile, self.feedback))
        changed = deepcopy(self.feedback)
        changed["decisions"].append("Explicit new scope decision")
        second = projects.create(self.profile, changed, base=self.base, previous=info.root)
        self.assertNotEqual(second.root, info.root)
        self.assertEqual(projects.read_inputs(info.root)[1], self.feedback)

    def test_malformed_and_nonregular_marker_still_dispatch_then_block(self):
        root = self.root / "Malformed project"
        root.mkdir()
        marker = root / projects.PROJECT_FILE
        marker.write_bytes(b'{"broken":')
        self.assertTrue(projects.has_project_marker(root))
        self.assertEqual(projects.inspect(root).phase, "blocked")
        marker.unlink()
        marker.mkdir()
        self.assertTrue(projects.has_project_marker(root))
        self.assertFalse(projects.inspect(root).gate_passed)

    def test_large_valid_generated_plan_is_not_capped_as_intake_metadata(self):
        def change(section):
            section[0][0].find(draft.HP + "t").text = "a" * 270000
        self.payload.write_bytes(mutate(self.payload.read_bytes(), draft.PART, change))
        self.profile["jobs"][0]["payload_sha256"] = review.file_hash(self.payload)
        info = self.create()
        self.assertTrue(info.gate_passed, info.issues)
        self.assertEqual(info.phase, "awaiting_draft_permission")
        self.assertTrue(harness.inspect(info.root / "run", info.binding)["gate_passed"])
        self.assertFalse((info.review / "approved-plan.json").exists())

    def test_link_marker_and_linked_metadata_never_fall_through_or_become_ready(self):
        info = self.create()
        target = self.root / "external-binding.json"
        binding = info.root / "report-binding.json"
        target.write_bytes(binding.read_bytes())
        binding.unlink()
        try:
            binding.symlink_to(target)
        except OSError as exc:
            self.skipTest("Symlink creation unavailable: " + str(exc))
        self.assertFalse(projects.inspect(info.root).gate_passed)
        broken = self.root / "Linked marker"
        broken.mkdir()
        (broken / projects.PROJECT_FILE).symlink_to(self.root / "absent.json")
        self.assertTrue(projects.has_project_marker(broken))
        self.assertEqual(projects.inspect(broken).phase, "blocked")

    def test_linked_base_and_relative_base_cannot_receive_projects(self):
        with self.assertRaises(ValueError):
            projects.create(self.profile, self.feedback, base=Path("relative-projects"))
        linked = self.root / "linked-base"
        real = self.root / "real-base"
        real.mkdir()
        try:
            linked.symlink_to(real, target_is_directory=True)
        except OSError as exc:
            self.skipTest("Directory symlink creation unavailable: " + str(exc))
        with self.assertRaises(ValueError):
            projects.create(self.profile, self.feedback, base=linked)
        self.assertFalse(any(real.iterdir()))

    def test_default_root_shares_app_owned_projects(self):
        with patch.dict(os.environ, {"LOCALAPPDATA": str(self.root)}):
            info = projects.create(self.profile, self.feedback)
        self.assertEqual(info.root.parent, self.root / "HwpReview/Projects")
        self.assertTrue(info.gate_passed, info.issues)

    def test_hard_metadata_write_failure_reports_the_retained_owned_directory(self):
        with patch.object(projects, "_write_new", side_effect=PermissionError("Synthetic write denied")):
            with self.assertRaises(projects.ReportProjectError) as caught:
                self.create()
        retained = caught.exception.project_root
        self.assertEqual(retained.parent, self.base)
        self.assertTrue(retained.is_dir())


class DocumentFirstCoreTests(unittest.TestCase):
    """Pure core consumers; successful authority persistence is tested by HTTP."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / "synthetic-original.hwpx"
        self.source.write_bytes(fixture())
        self.info = projects.new_document(self.source, base=self.root / "projects")
        self.project = self.info.root
        self.selection = [row["id"] for row in projects.sections(self.project)
                          if row["text"] == "OLD PLACEHOLDER"]

    def saved(self):
        return {str(path.relative_to(self.project)): path.read_bytes()
                for path in self.project.rglob("*") if path.is_file()}

    def preview(self, **kwargs):
        return projects.preview_request(self.project, "본문을 명확하게 다듬어 주세요.",
                                        selection_ids=self.selection, **kwargs)

    def proposal(self, preview, text="개선한 본문입니다."):
        request = preview["request"]
        return {"schema": "hwp-report-proposal-v1", "request_id": request["request_id"],
                "context_sha256": request["context_sha256"],
                "jobs": [{"job_id": request["jobs"][0]["job_id"], "paragraphs": [text]}],
                "summary": ["선택한 본문의 표현을 다듬었습니다."]}

    def test_intake_and_preview_are_bounded_and_permission_helpers_do_not_write(self):
        self.assertTrue(self.info.document_first)
        self.assertEqual(self.info.phase, "awaiting_request")
        self.assertFalse(self.info.outputs)
        rows = projects.sections(self.project)
        self.assertTrue(all("index" not in row and "target_range" not in row for row in rows))
        self.assertTrue(all(row["id"].startswith("s-") for row in rows))
        before = self.saved()
        preview = self.preview()
        path, record = projects.request_record(self.project, preview)
        self.assertEqual(before, self.saved())
        self.assertFalse(path.exists())
        shared = json.dumps(preview["request"], ensure_ascii=False)
        self.assertNotIn(str(self.source), shared)
        self.assertNotIn("SOURCE PREFIX", shared)
        self.assertEqual(record["authority"], "create_unapproved_candidate_only")
        self.assertEqual(preview, self.preview())

    def test_changed_material_and_runtime_or_source_reject_the_exact_preview_record(self):
        material = self.root / "selected-excerpt.txt"
        material.write_text("명시적으로 선택한 근거 발췌문", encoding="utf-8")
        preview = self.preview(material_paths=[material])
        before = self.saved()
        original = material.read_bytes()
        material.write_bytes(original + b" drift")
        with self.assertRaises(ValueError):
            projects.request_record(self.project, preview)
        material.write_bytes(original)
        with patch.object(harness.runtime, "identity", return_value={"changed_runtime": True}):
            with self.assertRaises(ValueError):
                projects.request_record(self.project, preview)
        self.source.write_bytes(self.source.read_bytes() + b"drift")
        with self.assertRaises(ValueError):
            projects.request_record(self.project, preview)
        self.assertEqual(before, self.saved())

    def test_mutated_preview_scope_context_or_policy_cannot_prepare_permission(self):
        preview = self.preview()
        before = self.saved()
        for update in ("context", "policy", "bounds", "extra"):
            altered = deepcopy(preview)
            if update == "context":
                altered["request"]["context_sha256"] = "0" * 64
            elif update == "policy":
                altered["binding"]["policy"] = "unbounded"
            elif update == "bounds":
                altered["binding"]["target_range"] = [0, 4]
            else:
                altered["authority"] = "approved"
            with self.assertRaises(ValueError):
                projects.request_record(self.project, altered)
        self.assertEqual(before, self.saved())

    def test_ownership_declaration_is_explicit_bound_and_not_added_to_model_schema(self):
        material = self.root / "selected.txt"
        material.write_text("명시한 근거 98765 ms", encoding="utf-8")
        background = self.preview(material_paths=[material])
        own = self.preview(material_paths=[{"path": material, "name": material.name, "own_achievement": True}])
        self.assertFalse(background["binding"]["materials"][0]["own_achievement"])
        self.assertTrue(own["binding"]["materials"][0]["own_achievement"])
        self.assertNotEqual(background["request"]["request_id"], own["request"]["request_id"])
        self.assertNotIn("own_achievement", own["request"]["materials"][0])
        with self.assertRaises(ValueError):
            self.preview(material_paths=[{"path": material, "name": material.name, "own_achievement": 1}])

    def test_material_display_filename_is_bound_in_context_and_permission_revalidation(self):
        material = self.root / "m-opaque-inbox-id.txt"
        material.write_text("명시적으로 선택한 코드 @not-a-path.py ^caret", encoding="utf-8")
        selected = {"path": material, "name": "선택한 소스.py", "own_achievement": False}
        preview = self.preview(material_paths=[selected])
        reference, = preview["request"]["materials"]
        self.assertEqual(reference["name"], selected["name"])
        self.assertEqual(reference["text"], material.read_text(encoding="utf-8"))
        self.assertNotIn(material.name, json.dumps(preview["request"], ensure_ascii=False))
        before = self.saved()
        path, record = projects.request_record(self.project, preview)
        self.assertEqual(record["preview"], preview)
        self.assertFalse(path.exists())
        changed = self.preview(material_paths=[{**selected, "name": "다른 소스.py"}])
        self.assertNotEqual(changed["request"]["request_id"], preview["request"]["request_id"])
        altered = deepcopy(preview)
        altered["binding"]["materials"][0]["name"] = "다른 소스.py"
        with self.assertRaises(ValueError):
            projects.request_record(self.project, altered)
        for name in ("../다른.py", "하위\\다른.py", "제어\x00.py"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.preview(material_paths=[{**selected, "name": name}])
        self.assertEqual(before, self.saved())

    def test_adapter_consumes_closed_proposal_in_existing_compose_and_preserves_locked_objects(self):
        import hwp_report_writer as writer
        before = self.saved()
        preview = self.preview()
        proposal = writer.validate_proposal(preview["request"], self.proposal(preview))
        source = self.source.read_bytes()
        source = mutate(source, draft.PART,
                        lambda section: draft.ET.SubElement(section[3][0], draft.HP + "tbl", {"id": "900"}))
        donor = projects._donor(source, [2, 3], proposal["jobs"][0]["paragraphs"])
        candidate, report = draft.compose(source, donor, {
            "target_range": [2, 3], "payload_ranges": [[0, 1]],
        })
        old, new = projects._body(source), projects._body(candidate)
        self.assertEqual(draft.node_hash([old[3]]), draft.node_hash([new[3]]))
        self.assertEqual(report["new_text"], ["개선한 본문입니다."])
        old_parts, new_parts = draft.parts_from(source), draft.parts_from(candidate)
        self.assertEqual(old_parts["settings.xml"], new_parts["settings.xml"])
        self.assertEqual(old_parts["Preview/PrvImage.png"], new_parts["Preview/PrvImage.png"])
        self.assertEqual(before, self.saved())

    def test_external_validator_keeps_objects_styles_numbers_and_outside_scope_locked(self):
        source = self.source.read_bytes()
        donor = projects._donor(source, [2, 3], ["본문을 다듬었습니다."])
        base, _ = draft.compose(source, donor, {"target_range": [2, 3], "payload_ranges": [[0, 1]]})
        changed = mutate(base, draft.PART,
                         lambda section: setattr(section[2][0].find(draft.HP + "t"), "text", "외부 표현 수정"))
        projects._validate_external(base, changed, [2, 3])
        for changed in (
            mutate(base, draft.PART, lambda section: section[2].set("styleIDRef", "99")),
            mutate(base, draft.PART, lambda section: setattr(section[0][0].find(draft.HP + "t"), "text", "LOCKED")),
            mutate(base, draft.PART, lambda section: setattr(section[2][0].find(draft.HP + "t"), "text", "98765 ms")),
        ):
            with self.assertRaises(ValueError):
                projects._validate_external(base, changed, [2, 3])

    def test_unknown_task_and_version_operations_do_not_create_permission_or_artifacts(self):
        before = self.saved()
        for operation in (
            lambda: projects.begin_request(self.project, "r-" + "0" * 32),
            lambda: projects.fail_request(self.project, "r-" + "0" * 32, "provider failure"),
            lambda: projects.acceptance_record(self.project, "v-" + "0" * 32, "0" * 64),
            lambda: projects.import_revision(self.project, self.source, "v-" + "0" * 32),
        ):
            with self.assertRaises((ValueError, OSError)):
                operation()
        self.assertEqual(before, self.saved())

    def test_no_grant_and_old_approval_do_not_authorize_generation(self):
        preview = self.preview()
        task = self.project / "tasks" / preview["request"]["request_id"]
        task.mkdir()
        (task / "approved-plan.json").write_text('{"approved_via":"local-review-ui"}')
        before = self.saved()
        with self.assertRaises((ValueError, OSError)):
            projects.generate(self.project, preview["request"]["request_id"], self.proposal(preview))
        self.assertEqual(before, self.saved())
        self.assertFalse(list((self.project / "versions").iterdir()))

    def test_objects_and_boundary_scope_are_rejected_not_dropped(self):
        with self.assertRaises(ValueError):
            projects.preview_request(self.project, "전체 문서를 바꾸세요.")
        first = projects.sections(self.project)[0]["id"]
        with self.assertRaises(ValueError):
            projects.preview_request(self.project, "경계를 바꾸세요.", selection_ids=[first])
        raw = mutate(fixture(), draft.PART,
                     lambda section: draft.ET.SubElement(section[2][0], draft.HP + "tbl"))
        other = self.root / "native-object.hwpx"
        other.write_bytes(raw)
        project = projects.new_document(other, base=self.root / "projects").root
        row = next(row for row in projects.sections(project) if row["text"] == "OLD PLACEHOLDER")
        self.assertTrue(row["has_objects"])
        with self.assertRaisesRegex(ValueError, "준비된 HWPX"):
            projects.preview_request(project, "본문 수정", selection_ids=[row["id"]])

    def test_old_project_contract_is_rejected_without_rebinding_or_migration(self):
        marker = self.project / projects.PROJECT_FILE
        original = json.loads(marker.read_bytes())
        original["contract"] = "hwp-report-project-v1"
        marker.write_text(json.dumps(original), encoding="utf-8")
        before = self.saved()
        self.assertFalse(projects.inspect(self.project).gate_passed)
        with self.assertRaises(ValueError):
            self.preview()
        self.assertEqual(before, self.saved())


if __name__ == "__main__":
    unittest.main()
