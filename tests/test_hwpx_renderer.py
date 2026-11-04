"""Local preview dependency closure; no download, build, model or Hancom."""

import copy
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import hwp_native_payload as payload

ROOT = Path(__file__).resolve().parents[1]


class HwpxRendererBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.pin_path = self.root / "packaging/hwpx-renderer-dependencies.json"
        self.pin_path.parent.mkdir()
        self.pins = json.loads((ROOT / "packaging/hwpx-renderer-dependencies.json").read_text(encoding="utf-8"))
        for name in self.pins["selected_files_sha256"]:
            target = self.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, target)
        self.save()

    def save(self):
        self.pin_path.write_text(json.dumps(self.pins), encoding="utf-8")


    def test_every_original_dependency_is_checked_not_only_the_notice(self):
        for name in self.pins["selected_files_sha256"]:
            with self.subTest(asset=name):
                target = self.root / name
                original = target.read_bytes()
                target.write_bytes(original + b"SYNTHETIC ALTERED BYTES")
                with self.assertRaisesRegex(ValueError, "bytes changed"):
                    payload.verify_renderer_dependencies(self.root)
                target.write_bytes(original)

    def test_version_sri_source_and_inventory_drift_are_refused(self):
        original = copy.deepcopy(self.pins)
        for change in (
            lambda p: p.update(version="0.8.8"),
            lambda p: p.update(integrity="sha512-UNKNOWN"),
            lambda p: p.update(git_head="0" * 40),
            lambda p: p.update(tarball="https://example.invalid/core.tgz"),
            lambda p: p.update(tarball_sha256="0" * 64),
            lambda p: p["selected_files_sha256"].pop(next(iter(p["selected_files_sha256"]))),
            lambda p: p["selected_files_sha256"].update({"outside.js": "0" * 64}),
        ):
            self.pins = copy.deepcopy(original)
            change(self.pins)
            self.save()
            with self.assertRaises(ValueError):
                payload.verify_renderer_dependencies(self.root)


if __name__ == "__main__":
    unittest.main()
