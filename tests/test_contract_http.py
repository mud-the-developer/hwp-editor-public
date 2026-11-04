"""Real loopback Native-contract API gates with denied child launch/synthetic consent."""

import http.client
import importlib
import json
import re
import threading
from unittest.mock import patch
import unittest

fixture = importlib.import_module("test_contract_control")
r = importlib.import_module("hwp_review")
control = importlib.import_module("hwp_contract_control")
worker = importlib.import_module("hwp_contract_apply")


class ContractHttpTests(unittest.TestCase):
    def stage(self, enabled=False):
        owner = fixture.ContractControlTests(
            "test_default_disabled_never_launches_or_changes_approval"
        )
        owner.setUp()
        self.addCleanup(owner.doCleanups)
        self.w, self.plan, self.payload, self.revision = owner.stage()
        self.server = r.create_review_server(
            self.w, 0, text_apply=True, contract_apply=enabled
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close)
        self.origin = f"http://127.0.0.1:{self.server.server_port}"
        response = self.request("GET", "/")
        token_match = re.search(r"const token='([a-f0-9]+)'", response[1].decode())
        if token_match is None:
            raise AssertionError("Synthetic server token missing")
        self.token = token_match.group(1)

    def close(self):
        job = getattr(self.server, "contract_apply_controller")
        if job.thread is not None:
            job.thread.join(5)
        self.server.shutdown()
        self.thread.join(5)
        self.server.server_close()

    def request(self, method, path, payload=None, headers=None):
        conn = http.client.HTTPConnection(
            "127.0.0.1", self.server.server_port, timeout=5
        )
        try:
            conn.request(
                method,
                path,
                body=json.dumps(payload).encode() if payload is not None else None,
                headers=headers or {},
            )
            response = conn.getresponse()
            return response.status, response.read()
        finally:
            conn.close()

    def headers(self):
        return {
            "Content-Type": "application/json",
            "Origin": self.origin,
            "X-Review-Token": self.token,
            "If-Match": self.revision,
        }

    def test_default_off_stays_readonly_and_never_launches(self):
        self.stage()
        before = (self.w / "approved-plan.json").read_bytes()
        code, body = self.request("GET", "/api/state?proposal=layout")
        self.assertEqual(code, 200)
        state = json.loads(body)
        self.assertFalse(state["contract_apply"]["enabled"])
        self.assertEqual(
            state["approval_scope_sha256"], self.payload["approval_scope_sha256"]
        )
        with patch.object(r, "apply") as apply:
            self.assertEqual(
                self.request(
                    "POST", "/api/apply-contract", self.payload, self.headers()
                )[0],
                400,
            )
            apply.assert_not_called()
        self.assertEqual((self.w / "approved-plan.json").read_bytes(), before)
        self.assertFalse((self.w / r.APPLY_LOCK).exists())

    def test_origin_token_missing_revision_stale_scope_rejected_before_job(self):
        self.stage(True)
        for change in (
            {"Origin": "http://localhost:9999"},
            {"X-Review-Token": "wrong"},
        ):
            self.assertEqual(
                self.request(
                    "POST",
                    "/api/apply-contract",
                    self.payload,
                    {**self.headers(), **change},
                )[0],
                403,
            )
        headers = self.headers()
        headers.pop("If-Match")
        self.assertEqual(
            self.request("POST", "/api/apply-contract", self.payload, headers)[0], 428
        )
        self.assertEqual(
            self.request(
                "POST",
                "/api/apply-contract",
                self.payload,
                {**self.headers(), "If-Match": '"stale"'},
            )[0],
            400,
        )
        bad = {**self.payload, "approval_scope_sha256": "0" * 64}
        self.assertEqual(
            self.request("POST", "/api/apply-contract", bad, self.headers())[0], 400
        )
        self.assertIsNone(getattr(self.server, "contract_apply_controller").thread)

    def test_accepted_is_only_start_both_modes_writes_and_close_locked(self):
        self.stage(True)
        entered = threading.Event()
        release = threading.Event()

        def slow(*args, **kwargs):
            entered.set()
            release.wait(5)
            kwargs["contract_exporter"].phase = 2

        with (
            patch.object(control, "registered_security_module"),
            patch.object(r, "apply", side_effect=slow),
        ):
            try:
                code, body = self.request(
                    "POST", "/api/apply-contract", self.payload, self.headers()
                )
                self.assertEqual(code, 202)
                self.assertTrue(json.loads(body)["accepted"])
                self.assertTrue(entered.wait(2))
                for path in (
                    "/api/apply-text",
                    "/api/apply-contract",
                    "/api/approve",
                    "/api/text-proposal",
                    "/api/fill-proposal",
                ):
                    self.assertEqual(
                        self.request("POST", path, self.payload, self.headers())[0], 423
                    )
                with self.assertRaises(RuntimeError):
                    self.server.server_close()
                code, body = self.request("GET", "/api/versions")
                self.assertEqual(code, 200)
                state = json.loads(body)
                self.assertTrue(state["contract_apply"]["active"])
                self.assertNotEqual(state["contract_apply"]["status"], "finished")
            finally:
                release.set()
                job = getattr(self.server, "contract_apply_controller")
                assert job.thread is not None
                job.thread.join(5)
        self.assertEqual(
            getattr(self.server, "contract_apply_controller").snapshot()["status"],
            "finished",
        )
        with patch.object(r, "apply") as apply:
            self.assertEqual(
                self.request(
                    "POST", "/api/apply-contract", self.payload, self.headers()
                )[0],
                400,
            )
            apply.assert_not_called()


if __name__ == "__main__":
    unittest.main()
