"""Synthetic report-harness lifecycle, feedback, scope and tamper regressions."""

import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import hwp_report_harness as h
import hwp_report_runtime as runtime
import hwp_review as r
import hwp_template_draft as d
from test_template_draft import fixture, mutate


class ReportHarnessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / "source.hwpx"
        self.payload = self.root / "payload.hwpx"
        self.source.write_bytes(fixture())
        self.payload.write_bytes(fixture(True))
        self.profile = self.root / "profile.json"
        self.feedback = self.root / "feedback.json"
        self.run_dir = self.root / "NEW HARNESS"
        self.spec = {
            "label": "SYNTHETIC ONLY",
            "source": str(self.source),
            "payload": str(self.payload),
            "target_range": [2, 3],
            "payload_ranges": [[0, 2]],
        }
        self.policy = {
            "schema": h.FEEDBACK,
            "goal": "SYNTHETIC: retain the exact agreed scope",
            "mode": "authoring",
            "decisions": [
                "Upper panel goes left; lower panel goes right. Do not ask this again."
            ],
        }
        self.server = None
        deny = patch.object(
            subprocess,
            "Popen",
            side_effect=AssertionError("No model, SDK or Native process"),
        )
        deny.start()
        self.addCleanup(deny.stop)

    def tearDown(self):
        if self.server is not None:
            self.server.shutdown()
            self.thread.join(5)
            self.server.server_close()

    def save(self):
        self.spec.update(
            source_sha256=r.file_hash(self.source),
            payload_sha256=r.file_hash(self.payload),
        )
        r.write_json(self.profile, {"contract": d.CONTRACT, "jobs": [self.spec]})
        r.write_json(self.feedback, self.policy)

    def start(self):
        self.save()
        self.started = h.prepare(self.profile, self.feedback, self.run_dir)
        self.binding = self.started["binding_sha256"]
        return self.started

    def set_body(self, value):
        def edit(root):
            node = root[0][0].find(d.HP + "t")
            assert node is not None
            node.text = value

        self.payload.write_bytes(mutate(self.payload.read_bytes(), d.PART, edit))

    def evidence(self, value, ownership="own", use="achievement"):
        path = self.root / "SYNTHETIC-evidence.txt"
        path.write_text(value, encoding="utf-8")
        self.policy["sources"] = [
            {
                "id": "toy_source",
                "path": str(path),
                "sha256": r.file_hash(path),
                "ownership": ownership,
                "use": use,
            }
        ]
        return path

    def revision(self, mode, value="User plane (U-plane) is observed at 12.5 ms."):
        self.set_body(value)
        baseline = self.root / "locked-prior-donor.hwpx"
        baseline.write_bytes(self.payload.read_bytes())
        raw, _ = d.compose(
            self.source.read_bytes(),
            self.payload.read_bytes(),
            {"target_range": [2, 3], "payload_ranges": [[0, 2]]},
        )
        self.source.write_bytes(raw)
        self.spec["target_range"] = [2, 4]
        self.policy.update(
            mode=mode,
            baselines=[{"path": str(baseline), "sha256": r.file_hash(baseline)}],
        )
        return baseline

    def image_revision(self):
        def png(width, height):
            p = r.fitz.Pixmap(r.fitz.csRGB, r.fitz.IRect(0, 0, width, height), False)
            p.set_rect(p.irect, (14, 116, 144))
            return p.tobytes("png")

        raw = self.payload.read_bytes()
        parts = d.parts_from(raw)
        parts["BinData/image.png"] = png(2, 2)
        self.payload.write_bytes(d._zip(raw, parts, []))
        baseline = self.revision("image-layout", "SYNTHETIC image-only scope")
        raw = self.payload.read_bytes()
        parts = d.parts_from(raw)
        root = r.xml_root(parts[d.PART])
        size = root.find(".//" + d.HP + "sz")
        assert size is not None
        size.set("height", "500")
        parts[d.PART] = d._xml(root)
        parts["BinData/image.png"] = png(4, 2)
        self.payload.write_bytes(d._zip(raw, parts, []))
        self.policy["image_scope"] = [[0]]
        return baseline

    def request(self, route, payload=None, headers=None):
        if self.server is None:
            self.server = r.create_template_review_server(
                self.run_dir / "review", report_binding=self.binding
            )
            self.thread = threading.Thread(
                target=self.server.serve_forever, daemon=True
            )
            self.thread.start()
            self.url = "http://127.0.0.1:" + str(self.server.server_port)
            with urllib.request.urlopen(self.url) as response:
                match = re.search(
                    rb'name="review-token" content="([a-f0-9]+)"', response.read()
                )
            assert match is not None
            self.token = match[1].decode()
        sent = {"X-Review-Token": self.token}
        if payload is not None:
            sent.update({"Origin": self.url, "Content-Type": "application/json"})
        sent.update(headers or {})
        req = urllib.request.Request(
            self.url + route,
            data=None if payload is None else json.dumps(payload).encode(),
            headers=sent,
        )
        try:
            with urllib.request.urlopen(req) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as exc:
            with exc:
                return exc.code, exc.read()

    def create(self):
        current = d.state(self.run_dir / "review")
        code, raw = self.request(
            "/api/template-draft",
            {"plan_sha256": current["plan_sha256"], "draft_only_confirmed": True},
            {"If-Match": current["revision"]},
        )
        self.assertEqual(code, 200, raw)
        return h.inspect(self.run_dir, self.binding)

    def test_prepare_records_decisions_without_approval_or_candidates(self):
        state = self.start()
        self.assertEqual(state["phase"], "awaiting_draft_permission")
        self.assertTrue(state["gate_passed"])
        self.assertEqual(state["decisions"], self.policy["decisions"])
        self.assertEqual(state["native_visual_review"], "not_observed")
        self.assertFalse(state["submission_approved"])
        self.assertFalse((self.run_dir / "review/approved-plan.json").exists())
        self.assertFalse(list(self.run_dir.rglob("*template-review.hwpx")))
        self.assertIn(
            "untrusted data", (self.run_dir / "THREAD.md").read_text(encoding="utf-8")
        )
        with self.assertRaises(FileExistsError):
            h.prepare(self.profile, self.feedback, self.run_dir)

    def test_readonly_inspect_and_append_only_observations(self):
        self.start()
        before = {
            str(p): p.read_bytes() for p in self.run_dir.rglob("*") if p.is_file()
        }
        h.inspect(self.run_dir, self.binding)
        self.assertEqual(
            before,
            {str(p): p.read_bytes() for p in self.run_dir.rglob("*") if p.is_file()},
        )
        first = h.observe(self.run_dir, self.binding)
        second = h.observe(self.run_dir, self.binding)
        self.assertNotEqual(first["observation"], second["observation"])
        for name, blob in before.items():
            self.assertEqual(Path(name).read_bytes(), blob)

    def test_unknown_numeric_tokens_block_old_engine_accepted_draft(self):
        self.set_body("SYNTHETIC throughput 999Mbps, not a real result.")
        self.save()
        d.compose(self.source.read_bytes(), self.payload.read_bytes(), self.spec)
        state = self.start()
        self.assertFalse(state["gate_passed"])
        self.assertTrue(any("999" in issue for issue in state["issues"]))
        self.assertEqual(state["phase"], "blocked")

    def test_only_declared_own_achievement_evidence_supports_new_numbers(self):
        self.set_body("SYNTHETIC observation 12.5 ms.")
        self.evidence("SYNTHETIC ONLY: observation 12.5 ms.")
        self.assertTrue(self.start()["gate_passed"])
        self.assertEqual(
            h.inspect(self.run_dir, self.binding)["numeric_check"],
            "token_provenance_only_not_claim_or_unit_truth",
        )

    def test_peer_or_unknown_achievement_sources_are_blocked(self):
        for ownership in ("peer", "unknown"):
            with self.subTest(ownership=ownership):
                self.run_dir = self.root / ("run-" + ownership)
                self.evidence("SYNTHETIC 12.5 ms.", ownership=ownership)
                self.set_body("SYNTHETIC 12.5 ms.")
                state = self.start()
                self.assertFalse(state["gate_passed"])
                self.assertIn("achievement_source_not_own:toy_source", state["issues"])
                self.assertTrue(
                    any(
                        "unsupported_numeric_tokens" in issue
                        for issue in state["issues"]
                    )
                )

    def test_peer_background_does_not_support_achievement_numbers(self):
        self.set_body("SYNTHETIC 12.5 ms.")
        self.evidence("SYNTHETIC 12.5 ms.", ownership="peer", use="background")
        self.assertFalse(self.start()["gate_passed"])

    def test_new_certification_language_is_not_automatic_pass(self):
        self.set_body("SYNTHETIC TTA PASS. This is not a real certificate.")
        self.evidence("SYNTHETIC TTA PASS, never real evidence.")
        state = self.start()
        self.assertFalse(state["gate_passed"])
        self.assertTrue(
            any("certification_language" in issue for issue in state["issues"])
        )

    def test_glossary_definitions_and_forced_korean_calques(self):
        self.set_body("User plane (U-plane) is observed; U-plane remains bounded.")
        self.assertTrue(self.start()["gate_passed"])
        self.run_dir = self.root / "bad-glossary"
        self.set_body("사용자평면 and U-plane without a definition.")
        state = self.start()
        self.assertFalse(state["gate_passed"])
        self.assertTrue(any("forced_translation" in issue for issue in state["issues"]))
        self.assertTrue(
            any("term_definition_count" in issue for issue in state["issues"])
        )

    def test_terminology_retains_fonts_images_structures_and_values(self):
        self.revision("terminology")
        self.set_body("User plane (U-plane) remains observed at 12.5 ms.")
        self.assertTrue(self.start()["gate_passed"])

    def test_terminology_cannot_change_values_or_font_geometry(self):
        self.revision("terminology")
        self.set_body("User plane (U-plane) remains observed at 15.0 ms.")
        self.payload.write_bytes(
            mutate(
                self.payload.read_bytes(),
                d.PART,
                lambda doc: doc[0].set("paraPrIDRef", "0"),
            )
        )
        state = self.start()
        self.assertFalse(state["gate_passed"])
        self.assertTrue(
            any("numeric_values_changed" in issue for issue in state["issues"])
        )
        self.run_dir = self.root / "font-drift"
        self.set_body("User plane (U-plane) remains observed at 12.5 ms.")
        self.payload.write_bytes(
            mutate(
                self.payload.read_bytes(),
                "Contents/header.xml",
                lambda head: head.find(".//" + d.HH + "font").set(
                    "face", "CHANGED FONT"
                ),
            )
        )
        state = self.start()
        self.assertFalse(state["gate_passed"])
        self.assertTrue(any("locked font" in issue for issue in state["issues"]))

    def test_image_aspect_fit_preserves_body_caption_and_other_properties(self):
        self.image_revision()
        self.assertTrue(self.start()["gate_passed"])

    def test_image_scope_cannot_hide_changed_caption_or_position(self):
        baseline = self.image_revision()
        self.payload.write_bytes(
            mutate(
                self.payload.read_bytes(),
                d.PART,
                lambda doc: doc.find(".//" + d.HP + "pic").set("zOrder", "8"),
            )
        )
        self.assertFalse(self.start()["gate_passed"])
        self.run_dir = self.root / "changed-caption"
        original = self.payload.read_bytes()
        base_parts = d.parts_from(baseline.read_bytes())
        parts = d.parts_from(original)
        root = r.xml_root(parts[d.PART])
        pic = root.find(".//" + d.HP + "pic")
        assert pic is not None
        pic.set("zOrder", "0")
        caption_text = pic.find(".//" + d.HP + "t")
        assert caption_text is not None
        caption_text.text = "CHANGED CAPTION"
        parts[d.PART] = d._xml(root)
        self.payload.write_bytes(d._zip(original, parts, []))
        self.assertIn("BinData/image.png", base_parts)
        self.assertFalse(self.start()["gate_passed"])

    def test_image_wrong_aspect_and_unlisted_scope_are_blocked(self):
        self.image_revision()

        def wrong(doc):
            size = doc.find(".//" + d.HP + "sz")
            assert size is not None
            size.set("height", "1000")

        self.payload.write_bytes(mutate(self.payload.read_bytes(), d.PART, wrong))
        self.assertFalse(self.start()["gate_passed"])
        self.run_dir = self.root / "wrong-scope"
        self.policy["image_scope"] = [[1]]
        self.assertFalse(self.start()["gate_passed"])

    def test_protected_source_text_cannot_be_removed(self):
        self.policy["protected_text"] = [["OLD PLACEHOLDER"]]
        state = self.start()
        self.assertFalse(state["gate_passed"])
        self.assertIn("protected_text_changed:0", state["issues"])

    def test_feedback_or_snapshot_drift_invalidates_bound_decisions(self):
        self.start()
        feedback = self.run_dir / "feedback.json"
        raw = feedback.read_bytes()
        feedback.write_bytes(raw + b" ")
        with self.assertRaisesRegex(ValueError, "feedback"):
            h.inspect(self.run_dir, self.binding)
        observed = h.observe(self.run_dir, self.binding)
        self.assertEqual(observed["phase"], "blocked")
        self.assertEqual(observed["outputs"], [])
        feedback.write_bytes(raw)
        self.assertTrue(h.inspect(self.run_dir, self.binding)["gate_passed"])

    def test_external_binding_and_locked_evaluator_cannot_be_replaced(self):
        engines = self.root / "scripts"
        engines.mkdir()
        source = Path(runtime.__file__).resolve().parents[1]
        names = dict.fromkeys(("scripts/" + name for name in runtime.SOURCE_FILES))
        names.update(dict.fromkeys(runtime.NATIVE_SOURCE_FILES))
        for name in names:
            target = self.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((source / name).read_bytes())
        with patch.object(runtime, "__file__", str(engines / "hwp_report_runtime.py")):
            self.start()
            with self.assertRaisesRegex(ValueError, "binding"):
                h.inspect(self.run_dir, "0" * 64)
            evaluator = engines / "hwp_report_harness.py"
            evaluator.write_bytes(evaluator.read_bytes() + b"\n# changed evaluator bytes\n")
            with self.assertRaisesRegex(ValueError, "evaluator"):
                h.inspect(self.run_dir, self.binding)
        path = self.run_dir / "harness.json"
        raw = path.read_bytes()
        path.write_bytes(raw + b" ")
        with self.assertRaisesRegex(ValueError, "binding"):
            h.inspect(self.run_dir, self.binding)

    def test_pinned_evidence_or_baseline_drift_blocks(self):
        path = self.evidence("SYNTHETIC evidence only.")
        self.start()
        path.write_text("CHANGED", encoding="utf-8")
        with self.assertRaises(ValueError):
            h.inspect(self.run_dir, self.binding)
        self.run_dir = self.root / "baseline-drift"
        self.policy.pop("sources")
        baseline = self.revision("terminology")
        self.set_body("User plane (U-plane) remains observed at 12.5 ms.")
        self.start()
        baseline.write_bytes(baseline.read_bytes() + b"CHANGED")
        with self.assertRaises(ValueError):
            h.inspect(self.run_dir, self.binding)

    def test_feedback_schema_rejects_extra_fields_and_unsafe_ownership(self):
        for change in (
            {"approve": True},
            {"mode": "shell"},
            {"decisions": "not a list"},
            {"image_scope": [[True]]},
        ):
            with self.subTest(change=change), self.assertRaises(ValueError):
                h.feedback_data(json.dumps({**self.policy, **change}).encode())
        self.policy["terms"] = [
            {"short": "U-plane", "definition": "User plane (U-plane)", "avoid": []}
        ]
        with self.assertRaises(ValueError):
            h.feedback_data(json.dumps(self.policy).encode())

    def test_prepare_failure_preserves_new_run_without_retry(self):
        self.policy["sources"] = [
            {
                "id": "missing",
                "path": str(self.root / "missing.txt"),
                "sha256": "0" * 64,
                "ownership": "own",
                "use": "achievement",
            }
        ]
        self.save()
        with self.assertRaises(FileNotFoundError):
            h.prepare(self.profile, self.feedback, self.run_dir)
        self.assertTrue((self.run_dir / "THREAD.md").is_file())
        self.assertTrue((self.run_dir / "PREPARE-FAILED.json").is_file())
        with self.assertRaises(FileExistsError):
            h.prepare(self.profile, self.feedback, self.run_dir)

    def test_ui_draft_exists_before_acceptance_and_delivery_is_idempotent(self):
        self.start()
        self.assertFalse(list(self.run_dir.rglob("*template-review.hwpx")))
        completed = self.create()
        self.assertEqual(completed["phase"], "generated_needs_hancom_review")
        self.assertEqual(completed["native_visual_review"], "not_observed")
        self.assertEqual(len(completed["outputs"]), 1)
        item = completed["outputs"][0]
        self.assertEqual(r.file_hash(Path(item["path"])), item["sha256"])
        before = {
            str(p): p.read_bytes()
            for p in (self.run_dir / "review").rglob("*")
            if p.is_file()
        }
        h.observe(self.run_dir, self.binding)
        self.assertEqual(
            before,
            {
                str(p): p.read_bytes()
                for p in (self.run_dir / "review").rglob("*")
                if p.is_file()
            },
        )
        current = d.state(self.run_dir / "review")
        self.assertEqual(
            self.request(
                "/api/template-draft",
                {"plan_sha256": current["plan_sha256"], "draft_only_confirmed": True},
                {"If-Match": current["revision"]},
            )[0],
            200,
        )

    def test_missing_artifact_is_not_completed_and_history_stays(self):
        self.start()
        completed = self.create()
        Path(completed["outputs"][0]["path"]).unlink()  # Owned synthetic output only.
        blocked = h.inspect(self.run_dir, self.binding)
        self.assertEqual(blocked["phase"], "blocked")
        self.assertFalse(blocked["gate_passed"])
        self.assertEqual(blocked["outputs"], [])
        self.assertEqual(self.request("/api/template-state")[0], 409)
        self.assertEqual(self.request("/artifact/0")[0], 409)
        self.assertTrue((self.run_dir / "review" / r.APPLY_LOCK).exists())

    def test_live_ui_gate_revalidates_feedback_before_draft_permission(self):
        self.start()
        code, raw = self.request("/api/template-state")
        self.assertEqual(code, 200)
        current = json.loads(raw)
        path = self.run_dir / "feedback.json"
        path.write_bytes(path.read_bytes() + b" ")
        self.assertEqual(self.request("/api/template-state")[0], 409)
        code, _ = self.request(
            "/api/template-draft",
            {
                "plan_sha256": current["plan_sha256"],
                "draft_only_confirmed": True,
            },
            {"If-Match": current["revision"]},
        )
        self.assertEqual(code, 409)
        self.assertFalse((self.run_dir / "review/draft-permission.json").exists())

    def test_feedback_replacement_after_hash_does_not_change_evaluated_bytes(self):
        self.set_body("사용자평면 without the required English terminology.")
        self.assertFalse(self.start()["gate_passed"])
        path = self.run_dir / "feedback.json"
        captured = path.read_bytes()
        forged = json.loads(captured)
        forged["terms"] = []
        replacement = json.dumps(forged).encode()
        original = r.digest
        swapped = False

        def racing_digest(raw):
            nonlocal swapped
            if raw == captured and not swapped:
                swapped = True
                path.write_bytes(replacement)
            return original(raw)

        with patch.object(r, "digest", side_effect=racing_digest):
            result = h.inspect(self.run_dir, self.binding)
        self.assertTrue(swapped)
        self.assertFalse(result["gate_passed"])
        self.assertTrue(
            any("forced_translation" in issue for issue in result["issues"])
        )

    def test_manifest_replacement_cannot_authenticate_forged_feedback(self):
        self.set_body("사용자평면 without the required English terminology.")
        self.start()
        path = self.run_dir / "harness.json"
        captured = path.read_bytes()
        feedback_path = self.run_dir / "feedback.json"
        forged_feedback = json.loads(feedback_path.read_bytes())
        forged_feedback["terms"] = []
        forged_raw = json.dumps(forged_feedback).encode()
        forged_manifest = json.loads(captured)
        forged_manifest["feedback_sha256"] = r.digest(forged_raw)
        original = r.digest
        swapped = False

        def racing_digest(raw):
            nonlocal swapped
            if raw == captured and not swapped:
                swapped = True
                path.write_bytes(json.dumps(forged_manifest).encode())
                feedback_path.write_bytes(forged_raw)
            return original(raw)

        with (
            patch.object(r, "digest", side_effect=racing_digest),
            self.assertRaisesRegex(ValueError, "feedback"),
        ):
            h.inspect(self.run_dir, self.binding)
        self.assertTrue(swapped)

    def test_generated_outputs_without_exact_draft_permission_fail_the_live_gate(self):
        self.start()
        self.create()
        (self.run_dir / "review/draft-permission.json").unlink()  # Synthetic fault only.
        state = h.inspect(self.run_dir, self.binding)
        self.assertEqual(state["phase"], "blocked")
        self.assertFalse(state["gate_passed"])
        self.assertIn(
            "missing_exact_UI_draft_permission_for_generated_outputs", state["issues"]
        )
        self.assertEqual(self.request("/api/template-state")[0], 409)
        self.assertEqual(self.request("/artifact/0")[0], 409)

    def test_header_only_or_corrupt_png_never_passes_image_layout(self):
        self.image_revision()
        raw = self.payload.read_bytes()
        original_parts = d.parts_from(raw)
        png = original_parts["BinData/image.png"]
        for index, invalid in enumerate(
            (png[:33], png[:-12], png[:40] + bytes([png[40] ^ 1]) + png[41:])
        ):
            with self.subTest(index=index):
                self.run_dir = self.root / ("invalid-image-" + str(index))
                parts = dict(original_parts)
                parts["BinData/image.png"] = invalid
                self.payload.write_bytes(d._zip(raw, parts, []))
                state = self.start()
                self.assertFalse(state["gate_passed"])
                self.assertTrue(any("PNG" in issue for issue in state["issues"]))

    def test_image_only_does_not_require_unrelated_text_changes(self):
        baseline = self.image_revision()
        before, after = baseline.read_bytes(), self.payload.read_bytes()

        def body(doc):
            node = doc[0][0].find(d.HP + "t")
            assert node is not None
            node.text = "사용자평면: this existing caption/prose must not be rewritten for an image-only request."

        before = mutate(before, d.PART, body)
        after = mutate(after, d.PART, body)
        baseline.write_bytes(before)
        self.payload.write_bytes(after)
        old_source = self.source.read_bytes()

        def source_body(doc):
            node = doc[2][0].find(d.HP + "t")
            assert node is not None
            node.text = "사용자평면: this existing caption/prose must not be rewritten for an image-only request."

        self.source.write_bytes(mutate(old_source, d.PART, source_body))
        self.policy["baselines"] = [
            {"path": str(baseline), "sha256": r.file_hash(baseline)}
        ]
        self.assertTrue(self.start()["gate_passed"])

    def test_harness_cannot_open_other_ui(self):
        self.start()
        with self.assertRaises(ValueError):
            r.create_review_server(self.run_dir / "review", report_binding=self.binding)

    def frozen_runtime(self):
        executable = self.root / "HWPReview.exe"
        executable.write_bytes(b"SYNTHETIC EXECUTABLE IDENTITY ONLY")
        assets = self.root / "packaged-assets"
        (assets / "docs").mkdir(parents=True)
        (assets / runtime.RULE_FILE).write_bytes(b"SYNTHETIC PINNED RUNTIME RULES")
        return executable, assets

    def test_frozen_report_exact_ui_creation_and_runtime_drift(self):
        executable, assets = self.frozen_runtime()
        with (
            patch.object(sys, "frozen", True, create=True),
            patch.object(sys, "_MEIPASS", str(assets), create=True),
            patch.object(sys, "executable", str(executable)),
            patch.object(runtime, "__file__", str(assets / "missing-PYZ-module.py")),
            patch.object(runtime, "_windows_scope", return_value=("device-A", "user-A")),
        ):
            self.assertEqual(self.start()["phase"], "awaiting_draft_permission")
            completed = self.create()
            self.assertEqual(completed["phase"], "generated_needs_hancom_review")
            artifact = Path(completed["outputs"][0]["path"])
            self.assertEqual(r.file_hash(artifact), completed["outputs"][0]["sha256"])
            preserved = {
                str(path): path.read_bytes()
                for path in (self.run_dir / "review").rglob("*")
                if path.is_file()
            }
            with patch.object(
                runtime, "_windows_scope", return_value=("device-B", "user-A")
            ):
                blocked = h.observe(self.run_dir, self.binding)
                self.assertEqual(blocked["phase"], "blocked")
                self.assertEqual(blocked["outputs"], [])
                self.assertEqual(self.request("/artifact/0")[0], 409)
            with patch.object(
                runtime, "_windows_scope", return_value=("device-A", "user-B")
            ):
                self.assertEqual(h.observe(self.run_dir, self.binding)["phase"], "blocked")
            original = executable.read_bytes()
            executable.write_bytes(b"X" + original[1:])
            self.assertEqual(h.observe(self.run_dir, self.binding)["phase"], "blocked")
            self.assertEqual(self.request("/api/template-state")[0], 409)
            executable.write_bytes(original)
            rule = assets / runtime.RULE_FILE
            rule.write_bytes(rule.read_bytes() + b"\nNEW RULE")
            self.assertEqual(h.observe(self.run_dir, self.binding)["phase"], "blocked")
            self.assertEqual(
                preserved,
                {
                    str(path): path.read_bytes()
                    for path in (self.run_dir / "review").rglob("*")
                    if path.is_file()
                },
            )

    def test_source_and_frozen_projects_cannot_cross_certify(self):
        source = self.start()
        source_root, source_binding = self.run_dir, self.binding
        self.assertTrue(source["gate_passed"])
        executable, assets = self.frozen_runtime()
        with (
            patch.object(sys, "frozen", True, create=True),
            patch.object(sys, "_MEIPASS", str(assets), create=True),
            patch.object(sys, "executable", str(executable)),
            patch.object(runtime, "_windows_scope", return_value=("device-A", "user-A")),
        ):
            self.assertEqual(h.observe(source_root, source_binding)["phase"], "blocked")
            self.run_dir = self.root / "FROZEN HARNESS"
            frozen = self.start()
            self.assertTrue(frozen["gate_passed"])
        self.assertEqual(h.observe(self.run_dir, self.binding)["phase"], "blocked")
        self.assertEqual(
            h.inspect(source_root, source_binding)["phase"], "awaiting_draft_permission"
        )

    def test_legacy_bound_run_is_not_migrated(self):
        self.start()
        path = self.run_dir / "harness.json"
        legacy = r.read_json(path)
        legacy["contract"] = "hwp-report-harness-v1"
        legacy.pop("runtime_identity")
        r.write_json(path, legacy)
        legacy_binding = r.file_hash(path)
        before = path.read_bytes()
        blocked = h.observe(self.run_dir, legacy_binding)
        self.assertEqual(blocked["phase"], "blocked")
        self.assertFalse(blocked["gate_passed"])
        self.assertEqual(blocked["outputs"], [])
        self.assertEqual(path.read_bytes(), before)
        self.assertFalse((self.run_dir / "review/approved-plan.json").exists())

    def test_unavailable_frozen_scope_preserves_failed_run_without_fallback(self):
        executable, assets = self.frozen_runtime()
        self.save()
        with (
            patch.object(sys, "frozen", True, create=True),
            patch.object(sys, "_MEIPASS", str(assets), create=True),
            patch.object(sys, "executable", str(executable)),
            patch.object(runtime, "_windows_scope", side_effect=OSError("denied")),
        ):
            with self.assertRaises(ValueError):
                h.prepare(self.profile, self.feedback, self.run_dir)
            self.assertTrue((self.run_dir / "PREPARE-FAILED.json").is_file())
            self.assertFalse((self.run_dir / "harness.json").exists())
            self.assertFalse((self.run_dir / "review/approved-plan.json").exists())
            with self.assertRaises(FileExistsError):
                h.prepare(self.profile, self.feedback, self.run_dir)


if __name__ == "__main__":
    unittest.main()
