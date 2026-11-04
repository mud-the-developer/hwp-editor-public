"""Synthetic only: exact-range template drafts, UI approval, CAS and preservation."""

from copy import deepcopy
from io import BytesIO
import json
from pathlib import Path
import re
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request
import xml.etree.ElementTree as E
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import hwp_review as r
import hwp_template_draft as d

PNG = bytes.fromhex("89504e470d0a1a0a") + b"SYNTHETIC ONLY"


def paragraph(text, identifier):
    p = E.Element(
        d.HP + "p", {"id": str(identifier), "paraPrIDRef": "0", "styleIDRef": "0"}
    )
    run = E.SubElement(p, d.HP + "run", {"charPrIDRef": "0"})
    E.SubElement(run, d.HP + "t").text = text
    return p


def fixture(payload=False):
    head = E.Element(d.HH + "head", {"version": "1.4", "secCnt": "1"})
    E.SubElement(head, d.HH + "beginNum", {"pic": "1"})
    refs = E.SubElement(head, d.HH + "refList")
    faces = E.SubElement(refs, d.HH + "fontfaces", {"itemCnt": "7"})
    for language in d.LANGS.values():
        face = E.SubElement(
            faces, d.HH + "fontface", {"lang": language, "fontCnt": "1"}
        )
        E.SubElement(face, d.HH + "font", {"id": "0", "face": "TOY Report Font"})
    border = E.SubElement(refs, d.HH + "borderFills", {"itemCnt": "1"})
    E.SubElement(border, d.HH + "borderFill", {"id": "1"})
    chars = E.SubElement(refs, d.HH + "charProperties", {"itemCnt": "1"})
    char = E.SubElement(chars, d.HH + "charPr", {"id": "0", "borderFillIDRef": "0"})
    E.SubElement(char, d.HH + "fontRef", {key: "0" for key in d.LANGS})
    tabs = E.SubElement(refs, d.HH + "tabProperties", {"itemCnt": "1"})
    E.SubElement(tabs, d.HH + "tabPr", {"id": "0"})
    paras = E.SubElement(refs, d.HH + "paraProperties", {"itemCnt": "1"})
    prop = E.SubElement(paras, d.HH + "paraPr", {"id": "0", "tabPrIDRef": "0"})
    E.SubElement(prop, d.HH + "heading", {"type": "NONE", "idRef": "0"})
    styles = E.SubElement(refs, d.HH + "styles", {"itemCnt": "1"})
    E.SubElement(
        styles,
        d.HH + "style",
        {
            "id": "0",
            "name": "TOY",
            "paraPrIDRef": "0",
            "charPrIDRef": "0",
            "nextStyleIDRef": "0",
        },
    )
    section = E.Element(d.HP + "sec")
    if not payload:
        first = paragraph("SOURCE PREFIX", 10)
        page = E.SubElement(first[0], d.HP + "secPr")
        page = E.SubElement(
            page, d.HP + "pagePr", {"width": "60000", "height": "84000"}
        )
        E.SubElement(
            page, d.HP + "margin", {"left": "5000", "right": "5000", "gutter": "0"}
        )
        section.extend(
            [
                first,
                paragraph("YEAR3 BOUNDARY", 11),
                paragraph("OLD PLACEHOLDER", 12),
                paragraph("OTHER SECTION", 13),
            ]
        )
    else:
        section.extend([paragraph("IMPORTED BODY", 1), paragraph("", 2)])
        pic = E.SubElement(
            section[1][0], d.HP + "pic", {"id": "7", "instanceID": "8", "zOrder": "0"}
        )
        E.SubElement(pic, d.HP + "sz", {"width": "1000", "height": "1000"})
        E.SubElement(pic, d.HC + "img", {"binaryItemIDRef": "image"})
        cap = E.SubElement(
            pic, d.HP + "caption", {"side": "BOTTOM", "lastWidth": "1000"}
        )
        sub = E.SubElement(cap, d.HP + "subList")
        p = paragraph("TOY CAPTION", 3)
        sub.append(p)
        ctrl = E.SubElement(p[0], d.HP + "ctrl")
        number = E.SubElement(
            ctrl, d.HP + "autoNum", {"numType": "PICTURE", "num": "1"}
        )
        E.SubElement(number, d.HP + "autoNumFormat", {"type": "DIGIT"})
    package = E.Element(d.OPF + "package")
    E.SubElement(package, d.OPF + "metadata").text = "LOCKED SOURCE METADATA"
    manifest = E.SubElement(package, d.OPF + "manifest")
    for identifier, href, kind in (
        ("header", "Contents/header.xml", "application/xml"),
        ("section0", d.PART, "application/xml"),
    ):
        E.SubElement(
            manifest,
            d.OPF + "item",
            {"id": identifier, "href": href, "media-type": kind},
        )
    spine = E.SubElement(package, d.OPF + "spine")
    E.SubElement(spine, d.OPF + "itemref", {"idref": "section0"})
    parts = {
        "mimetype": b"application/hwp+zip",
        d.PART: d._xml(section),
        "Contents/header.xml": d._xml(head),
        "Contents/content.hpf": d._xml(package),
        "settings.xml": b"LOCKED SETTINGS",
        "Preview/PrvImage.png": b"LOCKED PREVIEW",
        "Preview/PrvText.txt": b"TOY",
    }
    if payload:
        E.SubElement(
            manifest,
            d.OPF + "item",
            {"id": "image", "href": "BinData/image.png", "media-type": "image/png"},
        )
        parts["Contents/content.hpf"] = d._xml(package)
        parts["BinData/image.png"] = PNG
    stream = BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, raw in parts.items():
            archive.writestr(name, raw)
    return stream.getvalue()


def mutate(raw, part, function):
    parts = r.archive(BytesIO(raw))
    root = r.xml_root(parts[part])
    function(root)
    parts[part] = d._xml(root)
    stream = BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in parts.items():
            archive.writestr(name, data)
    return stream.getvalue()


class TemplateDraftTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source = self.root / "source.hwpx"
        self.source.write_bytes(fixture())
        self.payload = self.root / "payload.hwpx"
        self.payload.write_bytes(fixture(True))
        self.spec = {
            "label": "SYNTHETIC ONLY",
            "source": str(self.source),
            "payload": str(self.payload),
            "source_sha256": r.file_hash(self.source),
            "payload_sha256": r.file_hash(self.payload),
            "target_range": [2, 3],
            "payload_ranges": [[0, 2]],
        }
        self.profile = self.root / "profile.json"
        r.write_json(self.profile, {"contract": d.CONTRACT, "jobs": [self.spec]})
        self.workspace = self.root / "NEW REVIEW"
        self.server = None

    def tearDown(self):
        if self.server is not None:
            self.server.shutdown()
            self.thread.join(5)
            self.server.server_close()
        self.temporary.cleanup()

    def prepare(self):
        return d.prepare(self.profile, self.workspace)

    def image_case(
        self, *, same=False, geometry=False, duplicate=False, unsupported=False
    ):
        def png(colour):
            pixmap = r.fitz.Pixmap(r.fitz.csRGB, r.fitz.IRect(0, 0, 3, 2), False)
            pixmap.set_rect(pixmap.irect, colour)
            return pixmap.tobytes("png")

        self.before_png, self.after_png = png((255, 0, 0)), png((0, 0, 255))
        source_raw, _ = d.compose(
            self.source.read_bytes(), self.payload.read_bytes(), self.spec
        )
        source = d.parts_from(source_raw)
        member = next(name for name in source if name.startswith("BinData/td_"))
        source[member] = self.before_png
        source_root = r.xml_root(source[d.PART])
        picture = source_root.find(".//" + d.HP + "pic")
        assert picture is not None
        outside = deepcopy(picture)
        caption = outside.find(d.HP + "caption")
        assert caption is not None
        outside.remove(caption)
        outside.set("id", "9000")
        outside.set("instanceID", "9001")
        source_root[0][0].append(outside)
        if duplicate:
            copied = deepcopy(picture)
            copied.set("id", "9010")
            copied.set("instanceID", "9011")
            number = copied.find(".//" + d.HP + "autoNum")
            assert number is not None
            number.set("num", "2")
            source_root[3][0].append(copied)
        source[d.PART] = d._xml(source_root)
        if unsupported:
            package, manifest = d._manifest(source)
            next(item for item in manifest if item.get("href") == member).set(
                "media-type", "image/jpeg"
            )
            source["Contents/content.hpf"] = d._xml(package)
            source[member] = b"\xff\xd8\xffSYNTHETIC NON-PNG"
        self.source.write_bytes(d._zip(source_raw, source, []))
        payload_raw = self.payload.read_bytes()
        payload = d.parts_from(payload_raw)
        payload["BinData/image.png"] = self.before_png if same else self.after_png
        if geometry:
            root = r.xml_root(payload[d.PART])
            size = root.find(".//" + d.HP + "sz")
            assert size is not None
            size.set("height", "2000")
            payload[d.PART] = d._xml(root)
        self.payload.write_bytes(d._zip(payload_raw, payload, []))
        self.spec.update(
            source_sha256=r.file_hash(self.source),
            payload_sha256=r.file_hash(self.payload),
            target_range=[2, 4],
        )
        r.write_json(self.profile, {"contract": d.CONTRACT, "jobs": [self.spec]})
        self.prepare()
        self.image_base = (
            "/api/template-images/" + d.state(self.workspace)["plan_sha256"] + "/0"
        )

    def request(self, route, payload=None, headers=None):
        self.prepare() if not self.workspace.exists() else None
        if self.server is None:
            self.server = r.create_template_review_server(self.workspace)
            self.thread = threading.Thread(
                target=self.server.serve_forever, daemon=True
            )
            self.thread.start()
            self.url = "http://127.0.0.1:" + str(self.server.server_port)
            with urllib.request.urlopen(self.url) as response:
                html = response.read().decode()
            match = re.search(r'name="review-token" content="([a-f0-9]+)"', html)
            assert match is not None
            self.token = match[1]
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
        except urllib.error.HTTPError as error:
            with error:
                return error.code, error.read()

    def create_via_ui(self):
        code, raw = self.request("/api/template-state")
        self.assertEqual(code, 200, raw)
        current = json.loads(raw)
        code, raw = self.request(
            "/api/template-draft",
            {"plan_sha256": current["plan_sha256"], "draft_only_confirmed": True},
            {"If-Match": current["revision"]},
        )
        self.assertEqual(code, 200, raw)
        return d.state(self.workspace)

    def test_pure_import_preserves_prefix_suffix_assets_and_resources(self):
        raw, report = d.compose(
            self.source.read_bytes(), self.payload.read_bytes(), self.spec
        )
        after = r.archive(BytesIO(raw))
        before = r.archive(self.source)
        root = r.xml_root(after[d.PART])
        self.assertEqual(
            d.texts([root[0], root[1], root[-1]]),
            ["SOURCE PREFIX", "YEAR3 BOUNDARY", "OTHER SECTION"],
        )
        self.assertIn("IMPORTED BODY", d.texts(list(root)))
        self.assertNotIn("OLD PLACEHOLDER", d.texts(list(root)))
        self.assertEqual(after["settings.xml"], before["settings.xml"])
        self.assertEqual(after["Preview/PrvImage.png"], before["Preview/PrvImage.png"])
        self.assertTrue(
            any(
                name.startswith("BinData/td_") and data == PNG
                for name, data in after.items()
            )
        )
        self.assertEqual(report["inserted_pictures"], 1)
        self.assertIn("TOY CAPTION1", report["new_text"])
        self.assertIn("TOY CAPTION1", after["Preview/PrvText.txt"].decode("utf-8"))
        self.assertFalse(report["native_qualification"])
        self.assertEqual(r.digest(raw), report["candidate_sha256"])
        again, _ = d.compose(
            self.source.read_bytes(), self.payload.read_bytes(), self.spec
        )
        self.assertEqual(raw, again)

    def test_object_identity_and_stack_order_are_separate_namespaces(self):
        source = mutate(
            self.source.read_bytes(), d.PART, lambda doc: doc[0].set("id", "2147483648")
        )
        raw, _ = d.compose(source, self.payload.read_bytes(), self.spec)
        body = r.xml_root(r.archive(BytesIO(raw))[d.PART])
        imported = list(body)[2:4]
        identifiers = [
            node.attrib[key]
            for root in imported
            for node in root.iter()
            for key in ("id", "instanceID")
            if key in node.attrib
        ]
        self.assertEqual(len(identifiers), len(set(identifiers)))
        self.assertTrue(all(int(value) < 2147483648 for value in identifiers))
        self.assertEqual(
            [
                node.get("zOrder")
                for root in imported
                for node in root.iter()
                if "zOrder" in node.attrib
            ],
            ["1"],
        )
        self.assertEqual(body[0].get("id"), "2147483648")

    def test_prepare_creates_no_candidate_or_approval_and_never_reuses_workspace(self):
        result = self.prepare()
        self.assertEqual(result["candidate_files_written"], 0)
        self.assertFalse(list(self.workspace.rglob("*template-review.hwpx")))
        self.assertFalse((self.workspace / "approved-plan.json").exists())
        with self.assertRaises(FileExistsError):
            self.prepare()

    def test_public_legal_route_is_not_a_document_reader_or_approval(self):
        self.prepare()
        before = {
            str(path.relative_to(self.workspace)): r.file_hash(path)
            for path in self.workspace.rglob("*")
            if path.is_file()
        }
        code, public = self.request("/legal", headers={"X-Review-Token": ""})
        self.assertEqual(code, 200)
        self.assertNotIn(self.spec["source_sha256"].encode(), public)
        self.assertEqual(
            self.request(
                "/legal?file=inputs/0-source.hwpx",
                headers={"X-Review-Token": ""},
            ),
            (code, public),
        )
        self.assertEqual(
            self.request("/legal", headers={"Host": "example.invalid"})[0], 403
        )
        self.assertEqual(
            self.request(
                "/legal/../inputs/0-source.hwpx", headers={"X-Review-Token": ""}
            )[0],
            403,
        )
        self.assertEqual(
            before,
            {
                str(path.relative_to(self.workspace)): r.file_hash(path)
                for path in self.workspace.rglob("*")
                if path.is_file()
            },
        )

    def test_apply_requires_ui_draft_permission_not_an_old_approval(self):
        self.prepare()
        (self.workspace / "approved-plan.json").write_text(
            json.dumps({"approved_via": "local-review-ui", "kind": "template-review-draft-v1"}),
            encoding="utf-8",
        )
        with self.assertRaises((ValueError, FileNotFoundError)):
            d.apply(self.workspace, d.state(self.workspace)["revision"])
        self.assertFalse((self.workspace / r.APPLY_LOCK).exists())

    def test_one_ui_action_creates_unapproved_artifact_and_exact_acceptance_does_not_write_document(self):
        code, raw = self.request("/api/template-state")
        self.assertEqual(code, 200)
        current = json.loads(raw)
        code, raw = self.request(
            "/api/template-draft",
            {"plan_sha256": current["plan_sha256"], "draft_only_confirmed": True},
            {"If-Match": current["revision"]},
        )
        self.assertEqual(code, 200, raw)
        result = d.result_state(self.workspace, d.state(self.workspace))
        self.assertEqual(len(result["artifacts"]), 1)
        item = result["artifacts"][0]
        path = self.workspace / item["file"]
        before = path.read_bytes()
        self.assertFalse((self.workspace / "approved-plan.json").exists())
        self.assertTrue((self.workspace / "draft-permission.json").is_file())
        code, artifact = self.request("/artifact/0")
        self.assertEqual(code, 200)
        self.assertEqual(r.digest(artifact), item["sha256"])
        current = json.loads(self.request("/api/template-state")[1])
        code, raw = self.request(
            "/api/template-accept", {"index": 0, "sha256": item["sha256"]},
            {"If-Match": current["revision"]},
        )
        self.assertEqual(code, 200, raw)
        self.assertEqual(path.read_bytes(), before)
        self.assertTrue((self.workspace / "artifact-1-acceptance.json").is_file())
        path.write_bytes(b"TAMPERED")
        blocked = json.loads(self.request("/api/template-state")[1])
        self.assertTrue(blocked["execution_recorded"])
        self.assertEqual(blocked["artifacts"], [])
        self.assertEqual(self.request("/artifact/0")[0], 409)

    def test_host_origin_token_revision_and_boolean_guards(self):
        self.request("/api/template-state")
        payload = {
            "plan_sha256": d.state(self.workspace)["plan_sha256"],
            "draft_only_confirmed": True,
        }
        for headers in (
            {"Host": "evil.invalid"},
            {"Origin": "http://evil.invalid"},
            {"X-Review-Token": "wrong"},
        ):
            self.assertEqual(
                self.request("/api/template-draft", payload, headers)[0], 403
            )
        self.assertEqual(
            self.request("/api/template-draft", payload, {"If-Match": "stale"})[0],
            409,
        )
        current = d.state(self.workspace)
        payload["draft_only_confirmed"] = 1
        self.assertEqual(
            self.request(
                "/api/template-draft", payload, {"If-Match": current["revision"]}
            )[0],
            409,
        )
        self.assertFalse((self.workspace / "draft-permission.json").exists())

    def test_changed_source_payload_or_snapshot_blocks_before_output(self):
        for target in ("source", "payload", "snapshot"):
            with self.subTest(target=target):
                workspace = self.root / ("w-" + target)
                d.prepare(self.profile, workspace)
                path = (
                    workspace / "inputs/0-source.hwpx"
                    if target == "snapshot"
                    else getattr(self, target)
                )
                before = path.read_bytes()
                path.write_bytes(before + b"CHANGED")
                with self.assertRaises(ValueError):
                    d.state(workspace)
                path.write_bytes(before)

    def test_import_rejects_unsafe_control_unknown_ref_and_width(self):
        mutations = [
            lambda doc: E.SubElement(doc[0][0], d.HP + "secPr"),
            lambda doc: doc[0].set("paraPrIDRef", "999"),
            lambda doc: doc.find(".//" + d.HP + "sz").set("width", "60000"),
        ]
        for change in mutations:
            with self.subTest(change=change), self.assertRaises(ValueError):
                d.compose(
                    self.source.read_bytes(),
                    mutate(self.payload.read_bytes(), d.PART, change),
                    self.spec,
                )

    def test_outside_automatic_picture_counter_is_not_silently_renumbered(self):
        def number(doc):
            ctrl = E.SubElement(doc[0][0], d.HP + "ctrl")
            E.SubElement(ctrl, d.HP + "autoNum", {"numType": "PICTURE", "num": "5"})

        with self.assertRaises(ValueError):
            d.compose(
                mutate(self.source.read_bytes(), d.PART, number),
                self.payload.read_bytes(),
                self.spec,
            )

    def test_independent_validator_rejects_locked_xml_or_package_change(self):
        raw, report = d.compose(
            self.source.read_bytes(), self.payload.read_bytes(), self.spec
        )
        changed = mutate(
            raw, d.PART, lambda doc: doc[0][0].find(d.HP + "t").set("changed", "yes")
        )
        with self.assertRaises(ValueError):
            d.validate(
                self.source.read_bytes(),
                changed,
                [2, 3],
                report["inserted_paragraphs"],
                [
                    name
                    for name in r.archive(BytesIO(raw))
                    if name not in r.archive(self.source)
                ],
            )

    def test_failed_attempt_keeps_lease_and_evidence_without_retry(self):
        self.prepare()
        current = d.state(self.workspace)
        payload = {"plan_sha256": current["plan_sha256"], "draft_only_confirmed": True}
        self.assertEqual(self.request("/api/template-state")[0], 200)
        with patch.object(d, "compose", side_effect=ValueError("synthetic preflight failure")):
            self.assertEqual(self.request("/api/template-draft", payload,
                                          {"If-Match": current["revision"]})[0], 409)
        self.assertFalse((self.workspace / r.APPLY_LOCK).exists())
        with patch.object(r, "assert_apply_lock_owned",
                          side_effect=ValueError("SYNTHETIC failure after lease")):
            self.assertEqual(self.request("/api/template-draft", payload,
                                          {"If-Match": current["revision"]})[0], 409)
        self.assertTrue((self.workspace / r.APPLY_LOCK).exists())
        self.assertEqual(
            r.read_json(self.workspace / "template-result.json")["status"], "failed"
        )
        with self.assertRaises(ValueError):
            d.apply(self.workspace, d.state(self.workspace)["revision"])

    def test_only_reachable_resources_are_imported_and_missing_reachable_refs_fail(
        self,
    ):
        def unused(head):
            bullets = E.SubElement(
                head.find(d.HH + "refList"), d.HH + "bullets", {"itemCnt": "1"}
            )
            bullet = E.SubElement(bullets, d.HH + "bullet", {"id": "1"})
            E.SubElement(bullet, d.HH + "paraHead", {"charPrIDRef": "4294967295"})

        payload = mutate(self.payload.read_bytes(), "Contents/header.xml", unused)
        _, report = d.compose(self.source.read_bytes(), payload, self.spec)
        self.assertEqual(report["resource_imports"]["bullets"], 0)

        def used(head):
            head.find(".//" + d.HH + "heading").attrib.update(
                {"type": "BULLET", "idRef": "1"}
            )

        with self.assertRaises(ValueError):
            d.compose(
                self.source.read_bytes(),
                mutate(payload, "Contents/header.xml", used),
                self.spec,
            )

    def test_source_derived_text_keeps_exact_resource_context_and_rejects_drift(self):
        def bullet_context(head):
            refs = head.find(d.HH + "refList")
            bullets = E.SubElement(refs, d.HH + "bullets", {"itemCnt": "1"})
            bullet = E.SubElement(bullets, d.HH + "bullet", {"id": "1"})
            E.SubElement(bullet, d.HH + "paraHead", {"charPrIDRef": "4294967295"})
            head.find(".//" + d.HH + "heading").attrib.update(
                {"type": "BULLET", "idRef": "1"}
            )

        source = mutate(self.source.read_bytes(), "Contents/header.xml", bullet_context)

        def replace_body(section):
            section[2].find(".//" + d.HP + "t").text = "REVISED SOURCE BODY"

        payload = mutate(source, d.PART, replace_body)
        spec = {
            **self.spec,
            "source_sha256": r.digest(source),
            "payload_sha256": r.digest(payload),
            "payload_ranges": [[2, 3]],
        }
        candidate, _ = d.compose(source, payload, spec)
        before, after = d.parts_from(source), d.parts_from(candidate)
        self.assertEqual(set(before), set(after))
        for name in before:
            if name not in {d.PART, "Preview/PrvText.txt"}:
                self.assertEqual(before[name], after[name], name)
        old_section, new_section = r.xml_root(before[d.PART]), r.xml_root(after[d.PART])
        self.assertEqual(d.texts([new_section[2]]), ["REVISED SOURCE BODY"])
        for index in (0, 1, 3):
            self.assertEqual(E.tostring(old_section[index]), E.tostring(new_section[index]))

        for drift in ("missing_body_reference", "header_bytes", "other_resource_bytes"):
            with self.subTest(drift=drift):
                parts = d.parts_from(payload)
                if drift == "missing_body_reference":
                    section = r.xml_root(parts[d.PART])
                    section[2].find(d.HP + "run").set("charPrIDRef", "4294967295")
                    parts[d.PART] = d._xml(section)
                elif drift == "header_bytes":
                    parts["Contents/header.xml"] += b"\n"
                else:
                    parts["settings.xml"] += b" FOREIGN"
                changed = d._zip(payload, parts, [])
                with self.assertRaisesRegex(ValueError, "Unresolved reachable charProperties"):
                    d.compose(source, changed, {**spec, "payload_sha256": r.digest(changed)})

    def test_permission_cannot_be_reused_after_proposal_or_scope_tampering(self):
        self.create_via_ui()
        path = self.workspace / "template-plan.json"
        plan = r.read_json(path)
        plan["jobs"][0]["before_anchor"] = ["FALSE BOUNDARY"]
        r.write_json(path, plan)
        with self.assertRaises(ValueError):
            d.state(self.workspace)

    def test_image_review_is_lossless_selected_and_readonly_without_plan_changes(self):
        self.image_case()
        before = {
            str(path.relative_to(self.workspace)): path.read_bytes()
            for path in self.workspace.rglob("*")
            if path.is_file()
        }
        plan_before = d.state(self.workspace)
        job = plan_before["plan"]["jobs"][0]
        candidate_before, report_before = d.compose(
            self.source.read_bytes(), self.payload.read_bytes(), job
        )
        code, raw = self.request(self.image_base)
        self.assertEqual(code, 200, raw)
        evidence = json.loads(raw)
        self.assertEqual(evidence["matched_count"], 0)
        self.assertEqual([item["index"] for item in evidence["before"]], [0])
        self.assertEqual([item["index"] for item in evidence["after"]], [0])
        self.assertNotIn("_blob", raw.decode())
        self.assertEqual(
            self.request(self.image_base + "/before/0"), (200, self.before_png)
        )
        self.assertEqual(
            self.request(self.image_base + "/after/0"), (200, self.after_png)
        )
        self.assertEqual(
            self.request(self.image_base + "/before/1")[0],
            404,
            "Outside-range picture must not be exposed",
        )
        with urllib.request.urlopen(
            urllib.request.Request(
                self.url + self.image_base + "/after/0",
                headers={"X-Review-Token": self.token},
            )
        ) as response:
            self.assertEqual(response.headers["Content-Type"], "image/png")
            self.assertEqual(
                response.headers["X-Image-Sha256"], r.digest(self.after_png)
            )
            self.assertEqual(response.headers["Cache-Control"], "no-store")
            self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
            self.assertIn("img-src blob:", response.headers["Content-Security-Policy"])
        self.assertEqual(
            before,
            {
                str(path.relative_to(self.workspace)): path.read_bytes()
                for path in self.workspace.rglob("*")
                if path.is_file()
            },
        )
        self.assertEqual(d.state(self.workspace), plan_before)
        self.assertEqual(
            d.compose(self.source.read_bytes(), self.payload.read_bytes(), job),
            (candidate_before, report_before),
        )

    def test_image_review_host_token_plan_and_index_boundaries(self):
        self.image_case()
        for headers in (
            {"X-Review-Token": ""},
            {"X-Review-Token": "wrong"},
            {"Host": "evil.invalid"},
        ):
            self.assertEqual(
                self.request(self.image_base + "/after/0", headers=headers)[0], 403
            )
        self.assertEqual(
            self.request("/api/template-images/" + "0" * 64 + "/0")[0], 409
        )
        for suffix in (
            "/before/-1",
            "/after/1",
            "/after/10000",
            "/other/0",
            "/after/0?path=../source.hwpx",
            "/after/%2e%2e",
        ):
            self.assertEqual(self.request(self.image_base + suffix)[0], 404, suffix)
        self.assertEqual(self.request(self.image_base[:-1] + "1")[0], 404)

    def test_image_review_geometry_only_does_not_claim_png_changed(self):
        self.image_case(same=True, geometry=True)
        evidence = json.loads(self.request(self.image_base)[1])
        self.assertEqual(evidence["matched_count"], 0)
        self.assertEqual(
            evidence["before"][0]["sha256"], evidence["after"][0]["sha256"]
        )
        self.assertNotEqual(
            evidence["before"][0]["properties_sha256"],
            evidence["after"][0]["properties_sha256"],
        )
        self.assertEqual(evidence["before"][0]["height"], "1000")
        self.assertEqual(evidence["after"][0]["height"], "2000")
        self.assertEqual(
            self.request(self.image_base + "/after/0"), (200, self.before_png)
        )

    def test_image_review_matches_remapped_ids_not_archive_order(self):
        self.image_case(same=True)
        evidence = json.loads(self.request(self.image_base)[1])
        self.assertEqual(
            (evidence["matched_count"], evidence["before"], evidence["after"]),
            (1, [], []),
        )
        self.assertEqual(self.request(self.image_base + "/before/0")[0], 404)

    def test_image_review_duplicate_occurrences_are_not_collapsed(self):
        self.image_case(same=True, duplicate=True)
        code, raw = self.request(self.image_base)
        self.assertEqual(code, 200, raw)
        evidence = json.loads(raw)
        self.assertEqual(evidence["matched_count"], 1)
        self.assertEqual([item["index"] for item in evidence["before"]], [1])
        self.assertEqual(evidence["after"], [])
        self.assertEqual(
            self.request(self.image_base + "/before/1"), (200, self.before_png)
        )
        self.assertEqual(self.request(self.image_base + "/before/0")[0], 404)

    def test_image_review_non_png_is_not_transcoded_or_served(self):
        self.image_case(unsupported=True)
        evidence = json.loads(self.request(self.image_base)[1])
        self.assertFalse(evidence["before"][0]["available"])
        self.assertIn("변환하거나 대체하지 않습니다", evidence["before"][0]["reason"])
        self.assertEqual(self.request(self.image_base + "/before/0")[0], 404)
        self.assertEqual(
            self.request(self.image_base + "/after/0"), (200, self.after_png)
        )

    def test_image_review_stale_inputs_and_bounds_fail_without_output(self):
        self.image_case()
        self.assertEqual(self.request(self.image_base)[0], 200)
        for path in (
            self.source,
            self.payload,
            self.workspace / "inputs/0-source.hwpx",
        ):
            raw = path.read_bytes()
            try:
                path.write_bytes(raw + b"CHANGED")
                self.assertEqual(self.request(self.image_base + "/after/0")[0], 409)
            finally:
                path.write_bytes(raw)
        path = self.workspace / "template-plan.json"
        raw = path.read_bytes()
        plan = json.loads(raw)
        plan["jobs"][0]["target_range"] = [1, 4]
        try:
            r.write_json(path, plan)
            self.assertEqual(self.request(self.image_base)[0], 409)
        finally:
            path.write_bytes(raw)
        self.assertFalse((self.workspace / "template-result.json").exists())
        self.assertFalse((self.workspace / "approved-plan.json").exists())

    def test_malformed_or_over_limit_png_has_no_browser_preview(self):
        for blob in (
            PNG,
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
            + (1000000).to_bytes(4) * 2
            + bytes(20),
        ):
            raw = fixture(True)
            parts = d.parts_from(raw)
            parts["BinData/image.png"] = blob
            pictures = d._review_pictures(d._zip(raw, parts, []), [0, 2])
            self.assertFalse(pictures[0]["available"])
            self.assertIsNone(pictures[0]["_blob"])

    def test_recorded_result_without_lease_is_delivered_read_only_not_regenerated(self):
        code, raw = self.request("/api/template-state")
        self.assertEqual(code, 200)
        current = json.loads(raw)
        self.assertEqual(
            self.request(
                "/api/template-draft",
                {"plan_sha256": current["plan_sha256"], "draft_only_confirmed": True},
                {"If-Match": current["revision"]},
            )[0],
            200,
        )
        (
            self.workspace / r.APPLY_LOCK
        ).unlink()  # Synthetic tampering, never a real lease.
        code, raw = self.request("/api/template-state")
        self.assertEqual(code, 200)
        current = json.loads(raw)
        self.assertFalse(current["locked"])
        self.assertTrue(current["execution_recorded"])
        snapshots = {
            str(path): path.read_bytes()
            for path in self.workspace.rglob("*")
            if path.is_file()
        }
        self.assertEqual(
            self.request(
                "/api/template-draft",
                {"plan_sha256": current["plan_sha256"], "draft_only_confirmed": True},
                {"If-Match": current["revision"]},
            )[0], 200,
        )
        self.assertEqual(
            snapshots,
            {
                str(path): path.read_bytes()
                for path in self.workspace.rglob("*")
                if path.is_file()
            },
        )

    def test_completion_requires_all_files_and_complete_result_metadata(self):
        r.write_json(
            self.profile,
            {
                "contract": d.CONTRACT,
                "jobs": [self.spec, {**self.spec, "label": "SYNTHETIC SECOND"}],
            },
        )
        code, raw = self.request("/api/template-state")
        self.assertEqual(code, 200)
        current = json.loads(raw)
        code, raw = self.request(
            "/api/template-draft",
            {"plan_sha256": current["plan_sha256"], "draft_only_confirmed": True},
            {"If-Match": current["revision"]},
        )
        self.assertEqual(code, 200, raw)
        result = r.read_json(self.workspace / "template-result.json")
        self.assertEqual(
            len(json.loads(self.request("/api/template-state")[1])["artifacts"]), 2
        )
        second = self.workspace / result["artifacts"][1]["file"]
        saved = second.read_bytes()
        for mode in ("missing_second", "partial_metadata", "failed_metadata"):
            with self.subTest(mode=mode):
                altered = deepcopy(result)
                if mode == "missing_second":
                    second.unlink()
                elif mode == "partial_metadata":
                    altered["artifacts"] = altered["artifacts"][:1]
                else:
                    altered["status"] = "failed"
                r.write_json(self.workspace / "template-result.json", altered)
                state = json.loads(self.request("/api/template-state")[1])
                self.assertEqual(state["artifacts"], [])
                self.assertTrue(state["execution_recorded"])
                self.assertTrue(state["result_error"])
                self.assertEqual(self.request("/artifact/0")[0], 409)
                second.write_bytes(saved)
        r.write_json(self.workspace / "template-result.json", result)
        self.assertEqual(
            len(json.loads(self.request("/api/template-state")[1])["artifacts"]), 2
        )

    def test_frozen_pure_template_operations_keep_approval_and_input_gates(self):
        with patch.object(sys, "frozen", True, create=True):
            prepared = self.prepare()
            current = d.state(self.workspace)
            self.assertEqual(current["plan_sha256"], prepared["plan_sha256"])
            raw, report = d.compose(
                self.source.read_bytes(), self.payload.read_bytes(), self.spec
            )
            self.assertEqual(r.digest(raw), report["candidate_sha256"])
            self.assertFalse((self.workspace / "approved-plan.json").exists())
            with self.assertRaises((FileNotFoundError, ValueError)):
                d.apply(self.workspace, current["revision"])
            self.assertFalse((self.workspace / r.APPLY_LOCK).exists())
            self.payload.write_bytes(self.payload.read_bytes() + b"CHANGED INPUT")
            with self.assertRaises(ValueError):
                d.state(self.workspace)

    def test_frozen_raw_template_server_requires_report_binding(self):
        self.prepare()
        with (
            patch.object(r, "frozen", return_value=True),
            self.assertRaises(ValueError),
        ):
            r.create_template_review_server(self.workspace)

    def test_old_native_contract_cannot_accept_template_mode(self):
        import hwp_contract_apply

        with self.assertRaises(ValueError):
            hwp_contract_apply.require_qualified({"schema": d.CONTRACT})


    def test_permission_record_and_artifact_acceptance_helpers_never_persist_authority(self):
        self.prepare()
        current = d.state(self.workspace)
        before = {str(path): path.read_bytes() for path in self.workspace.rglob("*") if path.is_file()}
        path, record = d.draft_permission_record(self.workspace, current["revision"])
        self.assertFalse(path.exists())
        self.assertEqual(before, {str(path): path.read_bytes() for path in self.workspace.rglob("*") if path.is_file()})
        self.create_via_ui()
        result = r.read_json(self.workspace / "template-result.json")
        item = result["artifacts"][0]
        before = {str(path): path.read_bytes() for path in self.workspace.rglob("*") if path.is_file()}
        acceptance_path, acceptance = d.artifact_acceptance_record(self.workspace, 0, item["sha256"])
        self.assertFalse(acceptance_path.exists())
        self.assertEqual(before, {str(path): path.read_bytes() for path in self.workspace.rglob("*") if path.is_file()})
        observed = d.result_state(self.workspace, d.state(self.workspace))
        self.assertFalse(observed["artifacts"][0]["accepted"])
        current = d.state(self.workspace)
        code, raw = self.request("/api/template-accept", {"index": 0, "sha256": item["sha256"]},
                                 {"If-Match": current["revision"]})
        self.assertEqual(code, 200, raw)
        observed = d.result_state(self.workspace, d.state(self.workspace))
        self.assertEqual(observed["accepted_indices"], [0])
        self.assertTrue(observed["artifacts"][0]["accepted"])
        artifact = self.workspace / item["file"]
        raw = artifact.read_bytes()
        before = {str(path): path.read_bytes() for path in self.workspace.rglob("*") if path.is_file()}
        d.apply_candidate(self.workspace, current["revision"], r.file_hash(path))
        self.assertEqual(artifact.read_bytes(), raw)
        self.assertEqual(before, {str(path): path.read_bytes() for path in self.workspace.rglob("*") if path.is_file()})
        wrong = {**acceptance, "sha256": "0" * 64}
        acceptance_path.write_text(json.dumps(wrong), encoding="utf-8")
        rejected = d.result_state(self.workspace, d.state(self.workspace))
        self.assertFalse(rejected["artifacts"])
        self.assertTrue(rejected["result_error"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
