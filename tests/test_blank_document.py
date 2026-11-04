"""Real blank-package consumers; no provider, Hancom or protocol-font fixture."""

from copy import deepcopy
from io import BytesIO
import json
from pathlib import Path
import subprocess
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import hwp_report_project as projects
import hwp_report_writer as writer
import hwp_template_draft as draft


class BlankDocumentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.destination = self.root / "새 빈 문서.hwpx"
        self.base = self.root / ".hwpreview" / "Projects"
        denied = patch.object(subprocess, "Popen", side_effect=AssertionError("No process is authorized"))
        denied.start()
        self.addCleanup(denied.stop)

    def create(self):
        return projects.new_blank_document(self.destination, base=self.base)

    def snapshot(self):
        return {path.relative_to(self.root): path.read_bytes()
                for path in self.root.rglob("*") if path.is_file()}

    def preview(self, info):
        rows = projects.sections(info.root)
        return projects.preview_request(info.root, "빈 본문에 첫 문장을 작성해 주세요.",
                                        selection_ids=[rows[1]["id"]])

    def proposal(self, preview, text):
        request = preview["request"]
        return {"schema": "hwp-report-proposal-v1", "request_id": request["request_id"],
                "context_sha256": request["context_sha256"],
                "jobs": [{"job_id": request["jobs"][0]["job_id"], "paragraphs": [text]}],
                "summary": ["빈 본문에 첫 문장을 작성했습니다."]}

    def mutate(self, raw, change):
        parts = draft.parts_from(raw)
        section = projects._body(raw)
        change(section)
        parts[draft.PART] = draft._xml(section)
        return draft._zip(raw, parts, [])

    def test_real_package_xml_manifest_resource_and_blank_content_closure(self):
        info = self.create()
        self.assertIsInstance(info, projects.ReportProjectInfo)
        self.assertTrue(info.document_first)
        self.assertTrue(info.gate_passed, info.issues)
        self.assertEqual(info.phase, "awaiting_request")
        self.assertEqual(info.root.parent, self.base)
        raw = self.destination.read_bytes()
        self.assertEqual(projects.source(info.root)["data"], raw)
        with zipfile.ZipFile(BytesIO(raw)) as archive:
            self.assertIsNone(archive.testzip())
            members = archive.infolist()
            self.assertEqual(members[0].filename, "mimetype")
            self.assertEqual(members[0].compress_type, zipfile.ZIP_STORED)
            self.assertEqual(archive.read("mimetype"), b"application/hwp+zip")
            self.assertEqual(len(members), len({entry.filename for entry in members}))
        parts = draft.parts_from(raw)
        xml = {path: draft.review.xml_root(blob) for path, blob in parts.items()
               if path.endswith((".xml", ".hpf", ".rdf"))}
        self.assertFalse(any(path.startswith("BinData/") for path in parts))
        self.assertFalse(any("signat" in path.lower() or "encrypt" in path.lower() for path in parts))
        for node in xml["META-INF/container.xml"].iter():
            if node.get("full-path"):
                self.assertIn(node.get("full-path"), parts)
        manifest = xml["Contents/content.hpf"].find(draft.OPF + "manifest")
        items = {node.get("id"): node for node in manifest}
        self.assertEqual(set(items), {"header", "section0", "settings"})
        for node in items.values():
            self.assertIn(node.get("href"), parts)
        for node in xml["Contents/content.hpf"].find(draft.OPF + "spine"):
            self.assertIn(node.get("idref"), items)
        header = xml["Contents/header.xml"]
        self.assertEqual(header.get("secCnt"), "1")
        refs = header.find(draft.HH + "refList")
        for container in refs:
            self.assertEqual(int(container.get("itemCnt")), len(container))
        section = xml[draft.PART]
        self.assertEqual(len(section), 3)
        self.assertEqual([node.text for node in section.iter(draft.HP + "t")], [None, None, None])
        self.assertEqual(draft.texts(section), [])
        self.assertEqual(parts["Preview/PrvText.txt"].strip(), b"")
        _, required, fonts = draft._dependencies(list(section), refs)
        self.assertEqual(required["styles"], {"0"})
        self.assertEqual(required["charProperties"], {"0"})
        self.assertEqual(required["paraProperties"], {"0"})
        faces = refs.find(draft.HH + "fontfaces")
        self.assertEqual({node.get("lang") for node in faces}, set(draft.LANGS.values()))
        for face in faces:
            self.assertEqual(int(face.get("fontCnt")), len(face))
            self.assertEqual(fonts[face.get("lang")], {font.get("id") for font in face})
            self.assertTrue(all(font.get("face") and font.get("isEmbedded") == "0" for font in face))
        section_properties = section[0].find(".//" + draft.HP + "secPr")
        numbering_ids = {node.get("id") for node in refs.find(draft.HH + "numberings")}
        self.assertIn(section_properties.get("outlineShapeIDRef"), numbering_ids)
        page = section_properties.find(draft.HP + "pagePr")
        self.assertGreater(int(page.get("height")), int(page.get("width")))
        self.assertFalse(info.outputs)
        self.assertFalse(info.requests)
        self.assertFalse(list(info.root.rglob("*permission*")))
        self.assertFalse(list(info.root.rglob("*acceptance*")))

    def test_one_empty_interior_scope_can_prepare_first_text_without_authority(self):
        info = self.create()
        before = self.snapshot()
        rows = projects.sections(info.root)
        self.assertEqual([row["selectable"] for row in rows], [False, True, False])
        self.assertEqual([row["boundary"] for row in rows], [True, False, True])
        self.assertEqual([row["text"] for row in rows], ["", "", ""])
        self.assertFalse(rows[1]["has_objects"])
        target = projects.memo_target(info.root, None, info.source_sha256, 0, 1)
        self.assertTrue(target["editable"], target["reason"])
        self.assertEqual(target["quote"], "")
        preview = self.preview(info)
        request = writer.validate_request(preview["request"])
        self.assertEqual(request["jobs"][0]["paragraphs"], [""])
        path, record = projects.request_record(info.root, preview)
        self.assertFalse(path.exists())
        self.assertEqual(record["authority"], "create_unapproved_candidate_only")
        self.assertEqual(self.snapshot(), before)
        with self.assertRaises((ValueError, OSError)):
            projects.generate(info.root, request["request_id"], self.proposal(preview, "첫 본문입니다."))
        self.assertEqual(self.snapshot(), before)
        for row in (rows[0], rows[2]):
            with self.assertRaises(ValueError):
                projects.preview_request(info.root, "보존 경계를 바꾸세요.", selection_ids=[row["id"]])

    def test_first_body_text_uses_existing_compiler_and_preserves_boundaries_and_fonts(self):
        info = self.create()
        raw = self.destination.read_bytes()
        preview = self.preview(info)
        proposal = writer.validate_proposal(preview["request"], self.proposal(preview, "첫 본문입니다."))
        donor = projects._donor(raw, [1, 2], proposal["jobs"][0]["paragraphs"])
        candidate, report = draft.compose(raw, donor, {"target_range": [1, 2], "payload_ranges": [[0, 1]]})
        projects._validate_revision(raw, candidate, [1, 2], [1, 2])
        before, after = projects._body(raw), projects._body(candidate)
        self.assertEqual(draft.node_hash([before[0], before[2]]), draft.node_hash([after[0], after[2]]))
        self.assertEqual(projects._text_body(candidate, [1, 2]), ["첫 본문입니다."])
        self.assertEqual(report["new_text"], ["첫 본문입니다."])
        old_faces = draft.review.xml_root(draft.parts_from(raw)["Contents/header.xml"]).find(
            draft.HH + "refList/" + draft.HH + "fontfaces")
        new_faces = draft.review.xml_root(draft.parts_from(candidate)["Contents/header.xml"]).find(
            draft.HH + "refList/" + draft.HH + "fontfaces")
        for old, new in zip(old_faces, new_faces):
            self.assertEqual(draft.node_hash(list(old)), draft.node_hash(list(new)[:len(old)]))
        self.assertEqual(self.destination.read_bytes(), raw)
        with self.assertRaises(ValueError):
            writer.validate_proposal(preview["request"], self.proposal(preview, ""))
        with self.assertRaises(ValueError):
            writer.validate_proposal(preview["request"], self.proposal(preview, "   "))
        revised_request = deepcopy(preview["request"])
        revised_request["jobs"][0]["paragraphs"] = ["첫 본문입니다."]
        revised_request["context_sha256"] = writer._digest({
            key: value for key, value in revised_request.items() if key != "context_sha256"})
        unchanged = self.proposal({"request": revised_request}, "첫 본문입니다.")
        with self.assertRaisesRegex(ValueError, "실질적인 변경"):
            writer.validate_proposal(revised_request, unchanged)

    def test_reserved_names_and_ntfs_streams_cannot_modify_an_existing_file(self):
        protected = self.root / "existing.hwpx"
        protected.write_bytes(b"Existing user bytes")
        before = self.snapshot()
        for name in ("CON.hwpx", "NUL.hwpx", "existing.hwpx:attached.hwpx"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                projects.new_blank_document(self.root / name, base=self.base)
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(self.base.exists())

    def test_collision_never_changes_existing_file_or_creates_project(self):
        self.destination.write_bytes(b"Existing user bytes")
        before = self.snapshot()
        with self.assertRaises(FileExistsError):
            self.create()
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(self.base.exists())

    def test_existing_directory_and_wrong_extension_are_not_overwritten(self):
        self.destination.mkdir()
        with self.assertRaises(OSError):
            self.create()
        self.assertTrue(self.destination.is_dir())
        with self.assertRaises(ValueError):
            projects.new_blank_document(self.root / "not-a-document.txt", base=self.base)
        self.assertFalse((self.root / "not-a-document.txt").exists())
        self.assertFalse(self.base.exists())

    def test_intake_failure_preserves_new_blank_and_refuses_replacement(self):
        with patch.object(projects, "new_document", side_effect=ValueError("Intake stopped")):
            with self.assertRaisesRegex(ValueError, "Intake stopped"):
                self.create()
        retained = self.destination.read_bytes()
        self.assertIn(draft.PART, draft.parts_from(retained))
        with self.assertRaises(FileExistsError):
            self.create()
        self.assertEqual(self.destination.read_bytes(), retained)
        self.assertFalse(self.base.exists())

    def test_empty_authoring_keeps_mixed_object_inline_and_missing_text_guards(self):
        self.create()
        original = self.destination.read_bytes()

        def mixed(section):
            run = draft.ET.SubElement(section[1], draft.HP + "run", {"charPrIDRef": "1"})
            draft.ET.SubElement(run, draft.HP + "t")

        def inline(section):
            draft.ET.SubElement(section[1][0][0], draft.HP + "lineBreak")

        def missing(section):
            section[1][0].remove(section[1][0][0])

        cases = [("mixed", mixed), ("inline", inline), ("missing", missing)]
        for kind in ("tbl", "pic", "ctrl", "shape", "unknown"):
            cases.append((kind, lambda section, tag=kind: draft.ET.SubElement(section[1][0], draft.HP + tag)))
        for name, change in cases:
            with self.subTest(name=name):
                path = self.root / (name + ".hwpx")
                path.write_bytes(self.mutate(original, change))
                info = projects.new_document(path, base=self.base)
                with self.assertRaises(ValueError):
                    self.preview(info)
        for value in ("   ", "x" * 8001):
            raw = self.mutate(original, lambda section: setattr(section[1][0][0], "text", value))
            with self.assertRaises(ValueError):
                projects._text_body(raw, [1, 2])

    def test_unsupported_signed_encrypted_and_multiple_sections_stay_rejected(self):
        self.create()
        original = self.destination.read_bytes()
        for name in ("META-INF/signatures.xml", "META-INF/encryption.xml", "Contents/section1.xml"):
            with self.subTest(name=name):
                source = self.root / (name.rsplit("/", 1)[-1] + ".hwpx")
                parts = draft.parts_from(original)
                parts[name] = b"<unsupported/>"
                source.write_bytes(draft._zip(original, parts, [name]))
                before = self.snapshot()
                with self.assertRaises(ValueError):
                    projects.new_document(source, base=self.base)
                self.assertEqual(self.snapshot(), before)


class BlankDocumentRendererTests(unittest.TestCase):
    """Exercise the shipped WASM parser/layout/SVG, not a fake renderer."""

    @unittest.skipUnless(shutil.which("node"), "Node is required for the shipped rhwp WASM consumer")
    def test_shipped_rhwp_renders_blank_and_first_body_text(self):
        viewer = Path(__file__).resolve().parents[1] / "native/HWPReview.Native/ViewerAssets/vendor/rhwp"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            blank = root / "blank.hwpx"
            info = projects.new_blank_document(blank, base=root / "Projects")
            self.assertTrue(info.gate_passed, info.issues)
            raw = blank.read_bytes()
            donor = projects._donor(raw, [1, 2], ["첫 본문입니다."])
            revised, _ = draft.compose(raw, donor, {"target_range": [1, 2], "payload_ranges": [[0, 1]]})
            authored = root / "authored.hwpx"
            authored.write_bytes(revised)
            script = """
                import { readFileSync } from 'node:fs';
                import { join } from 'node:path';
                const [viewer, ...paths] = process.argv.slice(1);
                const source = readFileSync(join(viewer, 'rhwp.js'));
                const { initSync, HwpDocument } = await import(
                    'data:text/javascript;base64,' + source.toString('base64'));
                initSync({ module: readFileSync(join(viewer, 'rhwp_bg.wasm')) });
                const results = [];
                for (const path of paths) {
                    const document = new HwpDocument(new Uint8Array(readFileSync(path)));
                    try {
                        results.push({ format: document.getSourceFormat(),
                            text: JSON.parse(document.getTextFileText()),
                            pages: document.pageCount(), svg: document.renderPageSvg(0) });
                    } finally { document.free(); }
                }
                process.stdout.write(JSON.stringify(results));
            """
            result = subprocess.run(
                [shutil.which("node"), "--input-type=module", "--eval", script,
                 str(viewer), str(blank), str(authored)],
                capture_output=True, text=True, encoding="utf-8", timeout=60, check=True,
            )
            results = json.loads(result.stdout)
            self.assertEqual(len(results), 2)
            for rendered in results:
                self.assertEqual(rendered["format"], "hwpx")
                self.assertEqual(rendered["pages"], 1)
                svg = draft.ET.fromstring(rendered["svg"])
                self.assertEqual(svg.tag, "{http://www.w3.org/2000/svg}svg")
            self.assertEqual(results[0]["text"].split("\r\n"), ["", "", ""])
            self.assertEqual(results[1]["text"].split("\r\n"), ["", "첫 본문입니다.", ""])


if __name__ == "__main__":
    unittest.main()
