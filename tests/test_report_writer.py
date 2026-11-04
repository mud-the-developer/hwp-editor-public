"""Pure writer consent/scope tests; only call_chat is mocked, never a provider."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import hwp_report_writer as writer


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def pin(request):
    request["context_sha256"] = digest({key: value for key, value in request.items()
                                       if key != "context_sha256"})
    return request


class ReportWriterTests(unittest.TestCase):
    def setUp(self):
        self.runtime = {"cli": "SYNTHETIC/profile/bin/pi.cmd", "rules_sha256": "a" * 64}
        self.runtime["sha256"] = digest(self.runtime)
        self.model = {"provider": "fixture", "id": "selected-model"}
        self.request = pin({
            "schema": "hwp-report-request-v1", "request_id": "r-" + "1" * 32,
            "instruction": "측정값은 유지하고 간결하게 작성하세요.",
            "jobs": [{"job_id": "scope-a", "label": "실험 결과",
                      "paragraphs": ["측정 지연은 15 ms입니다."], "has_objects": False}],
            "materials": [{"id": "evidence-a", "name": "선택한 측정 기록",
                           "text": "측정 지연: 15 ms", "sha256": "b" * 64}],
            "decisions": ["측정값과 단위를 유지"],
        })
        self.proposal = {
            "schema": "hwp-report-proposal-v1", "request_id": self.request["request_id"],
            "context_sha256": self.request["context_sha256"],
            "jobs": [{"job_id": "scope-a", "paragraphs": ["측정 지연: 15 ms."]}],
            "summary": ["측정값과 단위를 유지하며 문장을 간결하게 정리했습니다."],
        }
        # Any accidental subprocess/provider route is a hard failure, not a fake SDK.
        self.deny = patch("subprocess.Popen", side_effect=AssertionError("No process/provider permitted"))
        self.deny.start()
        self.addCleanup(self.deny.stop)

    def consent(self):
        return {"preview_sha256": writer.preview(self.request, self.model, self.runtime["sha256"])["preview_sha256"],
                "context_confirmed": True, "cost_confirmed": True, "draft_only_confirmed": True}

    def response(self, **changes):
        result = {"model": self.model, "outcome": "settled",
                  "text": json.dumps(self.proposal, ensure_ascii=False),
                  "tool_activity": [{"id": "read-1", "name": "get_review_context", "status": "finished"}],
                  "artifacts": [], "document_shared": True, "usage": None,
                  "history": [{"role": "assistant", "content": "must not persist"}]}
        result.update(changes)
        return result

    def generate(self, consent=None, cancel=None):
        return writer.generate(self.runtime, self.model, self.request,
                               self.consent() if consent is None else consent,
                               cancel or threading.Event())

    def test_request_validation_is_detached_without_model_or_runtime(self):
        with patch.object(writer.hwp_pi_chat, "call_chat") as call:
            checked = writer.validate_request(self.request)
            self.assertEqual(checked, self.request)
            checked["jobs"][0]["paragraphs"].append("detached")
            self.assertEqual(len(self.request["jobs"][0]["paragraphs"]), 1)
            invalid = deepcopy(self.request)
            invalid["instruction"] += " changed"
            with self.assertRaises(ValueError):
                writer.validate_request(invalid)
            call.assert_not_called()

    def test_success_is_consumer_validated_without_history_or_write_artifacts(self):
        with patch.object(writer.hwp_pi_chat, "call_chat", return_value=self.response()) as call:
            result = self.generate()
        self.assertEqual(set(result), {"proposal", "model", "usage", "tool_activity"})
        accepted = writer.validate_proposal(self.request, result["proposal"])
        self.assertEqual(accepted["jobs"][0]["paragraphs"], ["측정 지연: 15 ms."])
        self.assertNotEqual(accepted["jobs"][0]["paragraphs"], self.request["jobs"][0]["paragraphs"])
        call.assert_called_once()

    def test_every_consent_field_is_exact_and_true_not_numeric(self):
        original = self.consent()
        variants = [{}, {**original, "extra": True}]
        for key in original:
            missing = dict(original)
            del missing[key]
            variants.append(missing)
        for key in ("context_confirmed", "cost_confirmed", "draft_only_confirmed"):
            variants.extend([{**original, key: value} for value in (False, 1, "true", None)])
        with patch.object(writer.hwp_pi_chat, "call_chat") as call:
            for consent in variants:
                with self.subTest(consent=consent), self.assertRaises(ValueError):
                    self.generate(consent)
            call.assert_not_called()

    def test_old_consent_cannot_cover_changed_scope_material_instruction_model_or_runtime(self):
        consent = self.consent()
        cases = []
        for change in ("scope", "material", "instruction", "decision"):
            request = deepcopy(self.request)
            if change == "scope":
                request["jobs"][0]["paragraphs"] = ["다른 원본"]
            elif change == "material":
                request["materials"][0]["text"] += " 새 기록"
            elif change == "instruction":
                request["instruction"] += " 새 요청"
            else:
                request["decisions"].append("새 결정")
            cases.append((self.runtime, self.model, pin(request)))
        cases.append((self.runtime, {**self.model, "id": "other"}, self.request))
        runtime = {**self.runtime, "cli": "SYNTHETIC/other/pi.cmd"}
        runtime["sha256"] = digest({key: value for key, value in runtime.items() if key != "sha256"})
        cases.append((runtime, self.model, self.request))
        with patch.object(writer.hwp_pi_chat, "call_chat") as call:
            for runtime, model, request in cases:
                with self.subTest(request=request), self.assertRaises(ValueError):
                    writer.generate(runtime, model, request, consent, threading.Event())
            call.assert_not_called()

    def test_unpinned_context_and_runtime_tampering_block_before_send(self):
        consent = self.consent()
        self.request["instruction"] += " tampered"
        with patch.object(writer.hwp_pi_chat, "call_chat") as call:
            with self.assertRaises(ValueError):
                self.generate(consent)
            pin(self.request)
            consent = self.consent()
            self.runtime["cli"] = "SYNTHETIC/tampered"
            with self.assertRaises(ValueError):
                self.generate(consent)
            call.assert_not_called()

    def test_context_and_instruction_caps_reject_instead_of_truncate(self):
        with patch.object(writer.hwp_pi_chat, "call_chat") as call:
            for instruction in ("가" * 16001, "😀" * 8001):
                request = pin({**self.request, "instruction": instruction})
                with self.assertRaises(ValueError):
                    writer.preview(request, self.model, self.runtime["sha256"])
            request = deepcopy(self.request)
            request["materials"][0]["text"] = "가" * 12000
            with self.assertRaises(ValueError):
                writer.preview(pin(request), self.model, self.runtime["sha256"])
            call.assert_not_called()

    def test_provider_failure_is_preserved_without_resend(self):
        failure = RuntimeError("provider uncertain outcome")
        with patch.object(writer.hwp_pi_chat, "call_chat", side_effect=failure) as call:
            with self.assertRaises(RuntimeError) as caught:
                self.generate()
            self.assertIs(caught.exception, failure)
            call.assert_called_once()

    def test_unsettled_malformed_wrong_model_or_forbidden_tool_never_retry(self):
        variants = [self.response(outcome=outcome) for outcome in ("incomplete", "history_full")]
        variants += [self.response(text=text) for text in ("", "```json\n{}\n```", "{} trailing", "null",
                     '{"schema":1,"schema":2}', '{"schema":NaN}')]
        variants += [self.response(model={**self.model, "id": "fallback"}),
                     self.response(tool_activity=[{"id": "bad", "name": "request_approved_job", "status": "finished"}]),
                     self.response(artifacts=[{"kind": "text_draft"}]), self.response(document_shared=False)]
        for response in variants:
            with self.subTest(response=response), patch.object(writer.hwp_pi_chat, "call_chat", return_value=response) as call:
                with self.assertRaises(ValueError):
                    self.generate()
                call.assert_called_once()

    def test_proposal_is_closed_scoped_changed_bounded_and_control_free(self):
        variants = []
        def changed(edit):
            value = deepcopy(self.proposal)
            edit(value)
            variants.append(value)
        changed(lambda value: value.update(extra=True))
        changed(lambda value: value.update(request_id="r-" + "2" * 32))
        changed(lambda value: value.update(context_sha256="f" * 64))
        changed(lambda value: value["jobs"][0].update(job_id="unselected"))
        changed(lambda value: value["jobs"][0].update(paragraphs=self.request["jobs"][0]["paragraphs"]))
        changed(lambda value: value["jobs"][0].update(paragraphs=["  측정 지연은 15 ms입니다.  "]))
        for paragraphs in ([], [True], [""], ["x" * 8001], ["a\x00b"], ["a\nb"], ["a\u0085b"], ["a\ud800b"], ["x"] * 129):
            changed(lambda value, paragraphs=paragraphs: value["jobs"][0].update(paragraphs=paragraphs))
        changed(lambda value: value.update(summary=["x"] * 4))
        changed(lambda value: value.update(summary=["x" * 501]))
        changed(lambda value: value["jobs"].append(deepcopy(value["jobs"][0])))
        changed(lambda value: value["jobs"][0].update(paragraphs=["x" * 8000] * 8))
        for proposal in variants:
            with self.subTest(proposal=repr(proposal)[:100]), self.assertRaises(ValueError):
                writer.validate_proposal(self.request, proposal)

    def test_two_jobs_require_unique_exact_changed_coverage(self):
        self.request["jobs"].append({"job_id": "scope-b", "label": "결론", "paragraphs": ["원본 결론"], "has_objects": False})
        pin(self.request)
        self.proposal["context_sha256"] = self.request["context_sha256"]
        self.proposal["jobs"].append({"job_id": "scope-b", "paragraphs": ["새 결론"]})
        self.assertEqual(len(writer.validate_proposal(self.request, self.proposal)["jobs"]), 2)
        self.proposal["jobs"][1]["job_id"] = "scope-a"
        with self.assertRaises(ValueError):
            writer.validate_proposal(self.request, self.proposal)

    def test_objects_numeric_booleans_and_hidden_request_fields_block(self):
        for flag in (True, 0, 1, None):
            request = deepcopy(self.request)
            request["jobs"][0]["has_objects"] = flag
            with self.assertRaises(ValueError):
                writer.preview(pin(request), self.model, self.runtime["sha256"])
        request = pin({**self.request, "approval": {"execute": True}})
        with self.assertRaises(ValueError):
            writer.preview(request, self.model, self.runtime["sha256"])

    def test_cancellation_before_and_after_bridge_does_not_resend(self):
        cancel = threading.Event()
        cancel.set()
        with patch.object(writer.hwp_pi_chat, "call_chat") as call:
            with self.assertRaises(ValueError):
                self.generate(cancel=cancel)
            call.assert_not_called()
        cancel.clear()
        def cancel_result(*args, **kwargs):
            cancel.set()
            return self.response()
        with patch.object(writer.hwp_pi_chat, "call_chat", side_effect=cancel_result) as call:
            with self.assertRaises(ValueError):
                self.generate(cancel=cancel)
            call.assert_called_once()


if __name__ == "__main__":
    unittest.main()
