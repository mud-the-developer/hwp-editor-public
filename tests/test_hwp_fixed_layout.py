"""New row-minimum contracts in TEMP synthetic packages only; no real approvals."""

from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
import http.client
import json
import re
import socket
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
import zipfile

import fitz
import test_hwp_review as fixture

r = fixture.r
fixed = r.hwp_fixed_layout
SYNTHETIC_HPF: bytes | None = (
    b'<package xmlns="http://www.idpf.org/2007/opf/"><metadata><meta name="ModifiedDate">2000-01-01T00:00:00Z</meta></metadata><spine><itemref idref="header" linear="yes"/><itemref idref="section0" linear="yes"/></spine></package>'
)


def without_selected_cache(raw):
    """Remove only the fixed TEMP fixture's selected paragraph cache."""
    root = ET.fromstring(raw)
    selected = [
        p
        for p in root.iter(r.HP + "p")
        if "".join(t.text or "" for t in p.iter(r.HP + "t")) == "short"
    ]
    assert len(selected) == 1
    caches = selected[0].findall(r.HP + "linesegarray")
    assert len(caches) == 1
    selected[0].remove(caches[0])
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


class FixedLayoutTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.base = self.path / "baseline.hwpx"
        self.xml = b"""<hs:sec xmlns:hs="http://www.hancom.co.kr/hwpml/2011/section" xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph"><hp:p><hp:run><hp:tbl id="synthetic-table" rowCnt="1" colCnt="2"><hp:sz width="2000" height="300"/><hp:tr>
<hp:tc><hp:subList><hp:p paraPrIDRef="1"><hp:run charPrIDRef="1"><hp:t>Unique synthetic slot</hp:t></hp:run><hp:linesegarray><hp:lineseg textpos="0" vertpos="0" vertsize="100"/><hp:lineseg textpos="12" vertpos="160" vertsize="100"/></hp:linesegarray></hp:p></hp:subList><hp:cellSpan colSpan="1" rowSpan="1"/><hp:cellSz width="1000" height="20"/><hp:cellMargin top="20" bottom="20" left="20" right="20"/></hp:tc>
<hp:tc><hp:subList><hp:p paraPrIDRef="1"><hp:run charPrIDRef="1"><hp:t>Protected synthetic label</hp:t></hp:run><hp:linesegarray><hp:lineseg textpos="0" vertpos="0" vertsize="100"/></hp:linesegarray></hp:p></hp:subList><hp:cellSpan colSpan="1" rowSpan="1"/><hp:cellSz width="1000" height="20"/><hp:cellMargin top="20" bottom="20" left="20" right="20"/></hp:tc>
</hp:tr></hp:tbl></hp:run></hp:p></hs:sec>"""
        self.xml = self.xml.replace(
            b"</hp:tc>", b'<hp:cellAddr rowAddr="0" colAddr="1"/></hp:tc>'
        )
        self.xml = self.xml.replace(
            b'<hp:cellAddr rowAddr="0" colAddr="1"/>',
            b'<hp:cellAddr rowAddr="0" colAddr="0"/>',
            1,
        )
        fixture.make_hwpx(self.base, self.xml)
        if SYNTHETIC_HPF is not None:
            with zipfile.ZipFile(self.base, "a") as package:
                package.writestr("Contents/content.hpf", SYNTHETIC_HPF)
        anchors, _ = r.index_hwpx(self.base)
        for anchor in anchors:
            anchor.update(page_hint=1, boxes=[[10, 10, 90, 20]])
        self.manifest = {
            "workspace_id": "synthetic-fixed-only",
            "baseline_hwpx_sha256": r.file_hash(self.base),
            "anchors": anchors,
            "protected_pages": [],
            "pages": [{"page": 1}],
        }
        old = {
            "schema": 1,
            "workspace_id": self.manifest["workspace_id"],
            "baseline_hwpx_sha256": self.manifest["baseline_hwpx_sha256"],
            "edits": [
                {
                    "anchor_id": anchors[0]["anchor_id"],
                    "old_text": anchors[0]["old_text"],
                    "new_text": "short",
                }
            ],
        }
        self.native = r.hwp_native_plan.proposal(old, self.manifest, self.base)
        self.plan = fixed.proposal(self.native, self.manifest, self.base)

    def candidate(self, changes=None, extras=None):
        parts = fixed.template_parts(self.base, self.plan)
        name = "Contents/section0.xml"
        parts[name] = (
            parts[name]
            .replace(b"Unique synthetic slot", b"short")
            .replace(b'<ns1:lineseg textpos="12" vertpos="160" vertsize="100" />', b"")
        )
        if changes:
            changed = changes(parts[name])
            self.assertNotEqual(
                changed, parts[name], "The mutation must actually touch the fixture"
            )
            parts[name] = changed
        if extras:
            parts.update(extras)
        target = self.path / "candidate.hwpx"
        with zipfile.ZipFile(target, "w") as z:
            for key, value in parts.items():
                z.writestr(key, value)
        return target

    def test_exact_derived_minima_and_new_policy_are_required(self):
        self.assertEqual(
            self.plan["row_minima"],
            [
                {
                    "part": "Contents/section0.xml",
                    "table_id": "synthetic-table",
                    "row_heights": [300],
                }
            ],
        )
        self.assertEqual(len(r.validate_plan(self.plan, self.manifest, self.base)), 1)
        for field, value in (
            ("native_policy", "anything"),
            ("schema", 3.0),
            ("row_minima", []),
        ):
            plan = deepcopy(self.plan)
            plan[field] = value
            with self.assertRaises(ValueError):
                r.validate_plan(plan, self.manifest, self.base)
        plan = deepcopy(self.plan)
        plan["row_minima"][0]["row_heights"][0] += 1
        with self.assertRaises(ValueError):
            r.validate_plan(plan, self.manifest, self.base)

    def test_old_native_ui_approval_cannot_authorize_new_minima_or_preview(self):
        r.write_json(
            self.path / "approved-plan.json",
            {
                "approved_via": "local-review-ui",
                "plan": self.native,
                "plan_sha256": r.plan_hash(self.native),
                "approved_scope": r.native_approval_scope(self.native),
            },
        )
        with self.assertRaises(ValueError):
            r.approved_plan(self.path, self.plan, self.manifest)
        approval = {
            "approved_via": "local-review-ui",
            "plan": self.plan,
            "plan_sha256": r.plan_hash(self.plan),
            "approved_scope": r.native_approval_scope(self.plan),
        }
        approval["approved_scope"].pop("row_minima_confirmed")
        r.write_json(self.path / "approved-plan.json", approval)
        with self.assertRaises(ValueError):
            r.approved_plan(self.path, self.plan, self.manifest)

    def test_minima_only_template_preserves_original_and_rejects_overwrite(self):
        before = self.base.read_bytes()
        target = self.path / "new-template.hwpx"
        fixed.write_template(self.base, target, self.plan)
        self.assertEqual(self.base.read_bytes(), before)
        with self.assertRaises(FileExistsError):
            fixed.write_template(self.base, target, self.plan)
        root = ET.fromstring(r.archive(target)["Contents/section0.xml"])
        self.assertEqual(
            [s.get("height") for s in root.iter(r.HP + "cellSz")], ["300", "300"]
        )
        self.assertIn(
            b"Unique synthetic slot", r.archive(target)["Contents/section0.xml"]
        )

    def test_new_gate_accepts_only_declared_minima_and_old_gate_stays_strict(self):
        candidate = self.candidate()
        self.assertEqual(
            fixed.structural_gate(self.base, candidate, self.plan)["policy"],
            fixed.POLICY,
        )
        with self.assertRaises(ValueError):
            r.hwp_native_plan.structural_gate(self.base, candidate, self.native)

    def test_selected_cache_absence_is_rejected_even_with_exact_text_and_minima(self):
        self.assertEqual(
            fixed.structural_gate(self.base, self.candidate(), self.plan)["policy"],
            fixed.POLICY,
        )
        baseline, plan = self.base.read_bytes(), deepcopy(self.plan)
        candidate = self.candidate(without_selected_cache)
        self.assertIn(b">short<", r.archive(candidate)["Contents/section0.xml"])
        with self.assertRaisesRegex(
            ValueError, "one nonempty selected paragraph line cache"
        ):
            fixed.structural_gate(self.base, candidate, self.plan)
        self.assertEqual(self.base.read_bytes(), baseline)
        self.assertEqual(self.plan, plan)

    def test_size_font_width_unselected_cache_and_image_locks_remain(self):
        cases = [
            lambda b: b.replace(b'height="300"', b'height="299"', 1),
            lambda b: b.replace(b'width="1000"', b'width="999"', 1),
            lambda b: b.replace(b'charPrIDRef="1"', b'charPrIDRef="2"', 1),
            lambda b: b.replace(b"Protected synthetic label", b"unapproved label"),
            lambda b: b.replace(b"</", b"<!-- injected --></", 1),
        ]
        for change in cases:
            with self.subTest(change=change):
                with self.assertRaises(ValueError):
                    fixed.structural_gate(self.base, self.candidate(change), self.plan)
        with self.assertRaises(ValueError):
            fixed.structural_gate(
                self.base,
                self.candidate(extras={"BinData/image.png": b"changed seal"}),
                self.plan,
            )

    def test_ambiguous_or_nonmatching_original_row_height_is_rejected(self):
        raw = self.xml.replace(b'height="300"', b'height="301"', 1)
        fixture.make_hwpx(self.base, raw)
        with self.assertRaises(ValueError):
            fixed.row_minima(self.base, self.plan["edits"])

    def test_http_new_contract_needs_explicit_confirmation_and_preserves_prior_scope(
        self,
    ):
        # Fake source and fake approval entirely within TEMP. Never a real workspace.
        source = self.path / "synthetic.hwp"
        source.write_bytes(b"SYNTHETIC ONLY")
        pdf = self.path / "baseline.pdf"
        pdf.write_bytes(b"SYNTHETIC ONLY")
        self.manifest.update(
            source=str(source),
            source_audit={"sha256": r.file_hash(source)},
            baseline_pdf_sha256=r.file_hash(pdf),
            roundtrip_passed=True,
            objects={"tbl": 1, "pic": 0},
        )
        serial_manifest = {
            **self.manifest,
            "anchors": [
                {
                    key: value
                    for key, value in anchor.items()
                    if not isinstance(value, bytes)
                }
                for anchor in self.manifest["anchors"]
            ],
        }
        r.write_json(self.path / "manifest.json", serial_manifest)
        r.write_json(
            self.path / "comments.json",
            {"workspace_id": self.manifest["workspace_id"], "comments": []},
        )
        r.write_json(self.path / "proposal-layout.json", self.plan)
        approval = {
            "approved_via": "local-review-ui",
            "plan": self.native,
            "plan_sha256": r.plan_hash(self.native),
            "approved_scope": r.native_approval_scope(self.native),
        }
        r.write_json(self.path / "approved-plan.json", approval)
        old_bytes = (self.path / "approved-plan.json").read_bytes()
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        process = subprocess.Popen(
            [
                sys.executable,
                str(r.ROOT / "scripts/hwp_review.py"),
                "serve",
                str(self.path),
                "--port",
                str(port),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.addCleanup(process.wait)
        self.addCleanup(process.terminate)
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
        self.addCleanup(connection.close)
        for _ in range(100):
            try:
                connection.request("GET", "/")
                response = connection.getresponse()
                html = response.read().decode()
                break
            except OSError:
                time.sleep(0.02)
        else:
            self.fail("Synthetic layout HTTP server did not start")
        match = re.search(r"const token='([^']+)'", html)
        assert match is not None, "Synthetic review token missing"
        token = match.group(1)
        connection.request("GET", "/api/state?proposal=layout")
        state = json.loads(connection.getresponse().read())
        self.assertEqual(state["proposal"]["schema"], 3)
        self.assertIsNone(state["approved_sha256"])
        headers = {
            "Content-Type": "application/json",
            "Origin": f"http://127.0.0.1:{port}",
            "X-Review-Token": token,
        }
        payload = {
            "plan_sha256": r.plan_hash(self.plan),
            "proposal_kind": "layout",
            "native_policy_confirmed": True,
            "regions_reviewed": [edit["anchor_id"] for edit in self.plan["edits"]],
        }
        connection.request("POST", "/api/approve", json.dumps(payload), headers)
        response = connection.getresponse()
        response.read()
        self.assertEqual(response.status, 400)
        self.assertEqual((self.path / "approved-plan.json").read_bytes(), old_bytes)
        payload["row_minima_confirmed"] = True
        connection.request("POST", "/api/approve", json.dumps(payload), headers)
        response = connection.getresponse()
        response.read()
        self.assertEqual(response.status, 200)
        self.assertEqual(
            r.read_json(self.path / "approved-plan.json")["approved_scope"],
            r.native_approval_scope(self.plan),
        )
        self.assertEqual(
            next((self.path / "approval-history").iterdir()).read_bytes(), old_bytes
        )
        self.assertFalse(
            (self.path / "runs").exists(), "Approval must not create an apply run"
        )

    def test_thumbnail_policy_is_bounded_and_does_not_change_old_policy(self):
        png = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 8, 8), False)
        png.clear_with(255)
        a = png.tobytes("png")
        png.clear_with(0)
        b = png.tobytes("png")
        parts = r.archive(self.base)
        parts["Preview/PrvImage.png"] = a
        with zipfile.ZipFile(self.base, "w") as z:
            for key, value in parts.items():
                z.writestr(key, value)
        self.manifest["baseline_hwpx_sha256"] = r.file_hash(self.base)
        self.native["baseline_hwpx_sha256"] = r.file_hash(self.base)
        self.plan = fixed.proposal(self.native, self.manifest, self.base)
        self.assertTrue(
            fixed.structural_gate(
                self.base, self.candidate(extras={"Preview/PrvImage.png": b}), self.plan
            )["generated_thumbnail_changed"]
        )
        with self.assertRaises(ValueError):
            fixed.structural_gate(
                self.base,
                self.candidate(extras={"Preview/PrvImage.png": b"not PNG"}),
                self.plan,
            )
        with self.assertRaises(ValueError):
            r.hwp_native_plan.structural_gate(
                self.base,
                self.candidate(extras={"Preview/PrvImage.png": b}),
                self.native,
            )
