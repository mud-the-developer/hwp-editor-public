"""Synthetic only: owned captions, exact local references and legacy isolation."""

from copy import deepcopy
from pathlib import Path
import sys
import tempfile
import unittest
import xml.etree.ElementTree as E

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import hwp_object_captions as c
import hwp_template_draft as d
import hwp_review as r
from test_template_draft import fixture, mutate, paragraph


def source_with_prefix():
    raw = mutate(
        fixture(),
        "Contents/header.xml",
        lambda head: head.find(d.HH + "beginNum").set("tbl", "1"),
    )

    def add(root):
        for i in range(8):
            E.SubElement(
                root[0][0],
                d.HP + "container",
                {"id": str(101 + i), "numberingType": "PICTURE"},
            )
        for i in range(6):
            E.SubElement(
                root[0][0],
                d.HP + "pic",
                {
                    "id": str(201 + i),
                    "instid": str(201 + i),
                    "numberingType": "PICTURE",
                },
            )

    return mutate(raw, d.PART, add)


def modern_payload():
    def edit(root):
        pic = root[1].find(".//" + d.HP + "pic")
        assert pic is not None
        pic.set("numberingType", "PICTURE")
        pic.set("instid", pic.attrib["id"])
        p = paragraph("", 4)
        tbl = E.SubElement(p[0], d.HP + "tbl", {"id": "9", "numberingType": "TABLE"})
        E.SubElement(tbl, d.HP + "sz", {"width": "1000", "height": "1000"})
        cap = E.SubElement(tbl, d.HP + "caption", {"side": "TOP", "lastWidth": "1000"})
        sub = E.SubElement(cap, d.HP + "subList")
        cap_p = paragraph("TOY TABLE ", 5)
        sub.append(cap_p)
        ctrl = E.SubElement(cap_p[0], d.HP + "ctrl")
        E.SubElement(ctrl, d.HP + "autoNum", {"numType": "TABLE", "num": "1"})
        root.append(p)
        root[0].append(c.reference_run("7", "PICTURE", 1, "0", "1300000000"))
        root[0].append(c.reference_run("9", "TABLE", 1, "0", "1300000001"))

    return mutate(fixture(True), d.PART, edit)


def specification():
    return {
        "target_range": [2, 3],
        "payload_ranges": [[0, 3]],
        "caption_policy": c.POLICY,
    }


class ObjectCaptionTests(unittest.TestCase):
    def setUp(self):
        self.source = source_with_prefix()
        self.payload = modern_payload()
        self.spec = specification()

    def test_global_prefix_counts_containers_and_reference_ids_are_closed(self):
        raw, report = d.compose(self.source, self.payload, self.spec)
        self.assertEqual(report["caption_number_bases"], {"PICTURE": 14, "TABLE": 0})
        self.assertEqual(
            (
                report["native_picture_captions"],
                report["native_table_captions"],
                report["body_cross_references"],
            ),
            (1, 1, 2),
        )
        body = r.xml_root(d.parts_from(raw)[d.PART])
        nodes = list(body)[2:5]
        model = c.inspect(nodes, report["caption_number_bases"])
        self.assertEqual(
            sorted(target["number"] for target in model["targets"].values()), [1, 15]
        )
        for reference in model["references"]:
            target = model["targets"][reference["target"]]
            self.assertEqual(reference["display"].text, str(target["number"]))
            self.assertEqual(
                reference["end"].get("beginIDRef"), reference["begin"].get("id")
            )
            self.assertEqual(reference["begin"].get("fieldid"), c.FIELD_ID)
        pic = next(
            target["node"]
            for target in model["targets"].values()
            if target["kind"] == "PICTURE"
        )
        self.assertEqual(pic.get("id"), pic.get("instid"))
        self.assertNotEqual(pic.get("id"), "7")

    def test_new_model_requires_explicit_exact_policy(self):
        spec = dict(self.spec)
        spec.pop("caption_policy")
        with self.assertRaises(ValueError):
            d.compose(self.source, self.payload, spec)
        for value in (None, True, False, "unknown", 1):
            with self.subTest(value=value), self.assertRaises(ValueError):
                d.compose(
                    self.source, self.payload, {**self.spec, "caption_policy": value}
                )

    def test_malformed_captions_are_not_admitted(self):
        def wrong_side(root):
            root.find(".//" + d.HP + "tbl/" + d.HP + "caption").set("side", "BOTTOM")

        def orphan(root):
            E.SubElement(root[0][0], d.HP + "ctrl").append(
                E.Element(d.HP + "autoNum", {"numType": "TABLE", "num": "2"})
            )

        def duplicate(root):
            pic = root.find(".//" + d.HP + "pic")
            pic.append(deepcopy(pic.find(d.HP + "caption")))

        def alias(root):
            root.find(".//" + d.HP + "pic").set("instid", "999")

        for change in (wrong_side, orphan, duplicate, alias):
            with self.subTest(change=change.__name__), self.assertRaises(ValueError):
                d.compose(self.source, mutate(self.payload, d.PART, change), self.spec)

    def test_caption_counter_native_parent_chain_is_required_on_input_and_readback(
        self,
    ):
        def move_counter(root):
            cap = root.find(".//" + d.HP + "tbl/" + d.HP + "caption")
            assert cap is not None
            run = cap.find(d.HP + "subList/" + d.HP + "p/" + d.HP + "run")
            assert run is not None
            ctrl = run.find(d.HP + "ctrl")
            assert ctrl is not None
            number = ctrl.find(d.HP + "autoNum")
            assert number is not None
            ctrl.remove(number)
            run.remove(ctrl)
            cap.append(number)

        with self.assertRaisesRegex(ValueError, "control chain"):
            d.compose(
                self.source, mutate(self.payload, d.PART, move_counter), self.spec
            )
        raw, _ = d.compose(self.source, self.payload, self.spec)
        added = [
            name for name in d.parts_from(raw) if name not in d.parts_from(self.source)
        ]
        with self.assertRaisesRegex(ValueError, "control chain"):
            d.validate(
                self.source,
                mutate(raw, d.PART, move_counter),
                [2, 3],
                3,
                added,
                caption_policy=c.POLICY,
            )

        def empty_control(root):
            E.SubElement(root[0][0], d.HP + "ctrl")

        with self.assertRaisesRegex(ValueError, "Unsupported control"):
            d.compose(
                self.source, mutate(self.payload, d.PART, empty_control), self.spec
            )

    def test_reference_parameters_targets_and_pairs_fail_closed(self):
        def parameter(name, value):
            def edit(root):
                for node in root.iter(d.HP + "stringParam"):
                    if node.get("name") == name:
                        node.text = value
                        break

            return edit

        def pair(root):
            root.find(".//" + d.HP + "fieldEnd").set("beginIDRef", "999")

        def cache(root):
            for run in root.iter(d.HP + "run"):
                if run.find(".//" + d.HP + "fieldBegin") is not None:
                    run.find(d.HP + "t").text = "999"
                    break

        def field_type(root):
            root.find(".//" + d.HP + "fieldBegin").set("type", "HYPERLINK")

        def parameter_extra(root):
            E.SubElement(
                root.find(".//" + d.HP + "parameters"),
                d.HP + "stringParam",
                {"name": "Path"},
            ).text = "https://example.invalid"

        changes = [
            parameter("Command", "https://example.invalid"),
            parameter("RefPath", "?#999;"),
            parameter("RefType", "TARGET_TABLE"),
            parameter("RefContentType", "PAGE_NUMBER"),
            pair,
            cache,
            field_type,
            parameter_extra,
        ]
        for change in changes:
            with self.subTest(change=change.__name__), self.assertRaises(ValueError):
                d.compose(self.source, mutate(self.payload, d.PART, change), self.spec)

    def test_orphan_end_and_nested_field_runs_are_rejected(self):
        def orphan(root):
            E.SubElement(root[0][0], d.HP + "ctrl").append(
                E.Element(
                    d.HP + "fieldEnd", {"beginIDRef": "999", "fieldid": c.FIELD_ID}
                )
            )

        def duplicate(root):
            root[0].append(deepcopy(root[0][1]))

        for change in (orphan, duplicate):
            with self.subTest(change=change.__name__), self.assertRaises(ValueError):
                d.compose(self.source, mutate(self.payload, d.PART, change), self.spec)

    def test_numbered_suffix_must_not_be_shifted(self):
        def add(root):
            E.SubElement(
                root[-1][0], d.HP + "pic", {"id": "333", "numberingType": "PICTURE"}
            )

        source = mutate(self.source, d.PART, add)
        with self.assertRaisesRegex(ValueError, "suffix"):
            d.compose(source, self.payload, self.spec)

        def old_object(root):
            E.SubElement(
                root[2][0], d.HP + "pic", {"id": "444", "numberingType": "PICTURE"}
            )

        source = mutate(source, d.PART, old_object)
        d.compose(source, self.payload, self.spec)

    def test_outside_fields_counters_and_resets_are_blocked(self):
        def counter(root):
            E.SubElement(root[0][0], d.HP + "ctrl").append(
                E.Element(d.HP + "autoNum", {"numType": "TABLE", "num": "1"})
            )

        def reference(root):
            root[0].append(c.reference_run("201", "PICTURE", 1, "0", "1300000007"))

        def reset(root):
            E.SubElement(root[0][0], d.HP + "ctrl").append(
                E.Element(d.HP + "newNum", {"numType": "PICTURE", "num": "1"})
            )

        for change in (counter, reference, reset):
            with self.subTest(change=change.__name__), self.assertRaises(ValueError):
                d.compose(mutate(self.source, d.PART, change), self.payload, self.spec)

    def test_independent_validator_catches_counter_or_reference_tampering(self):
        raw, _ = d.compose(self.source, self.payload, self.spec)
        added = [
            name for name in d.parts_from(raw) if name not in d.parts_from(self.source)
        ]

        def counter(root):
            list(root)[3].find(".//" + d.HP + "autoNum").set("num", "999")

        def target(root):
            for node in root.iter(d.HP + "stringParam"):
                if node.get("name") == "RefPath":
                    node.text = "?#201;"
                    break

        def unsafe_control(root):
            E.SubElement(root[2][0], d.HP + "ctrl").append(
                E.Element(d.HP + "bookmark", {"name": "unsupported"})
            )

        def unsafe_object(root):
            E.SubElement(root[2][0], d.HP + "ole")

        for change in (counter, target, unsafe_control, unsafe_object):
            with self.subTest(change=change.__name__), self.assertRaises(ValueError):
                d.validate(
                    self.source,
                    mutate(raw, d.PART, change),
                    [2, 3],
                    3,
                    added,
                    caption_policy=c.POLICY,
                )

    def test_prepare_binds_policy_and_cannot_reuse_legacy_approval_scope(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.hwpx"
            payload = root / "payload.hwpx"
            source.write_bytes(self.source)
            payload.write_bytes(self.payload)
            profile = root / "profile.json"
            spec = {
                **self.spec,
                "label": "SYNTHETIC CAPTIONS",
                "source": str(source),
                "payload": str(payload),
                "source_sha256": r.file_hash(source),
                "payload_sha256": r.file_hash(payload),
            }
            r.write_json(profile, {"contract": d.CONTRACT, "jobs": [spec]})
            workspace = root / "review"
            d.prepare(profile, workspace)
            current = d.state(workspace)
            self.assertEqual(current["plan"]["jobs"][0]["caption_policy"], c.POLICY)
            self.assertFalse((workspace / "approved-plan.json").exists())
            plan = r.read_json(workspace / "template-plan.json")
            plan["jobs"][0].pop("caption_policy")
            r.write_json(workspace / "template-plan.json", plan)
            with self.assertRaises(ValueError):
                d.state(workspace)


if __name__ == "__main__":
    unittest.main()
