"""Pinned local icon closure; no build, network, UI or model execution."""

import copy
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import hwp_native_payload as payload

ROOT = Path(__file__).resolve().parents[1]


class IconDependencyTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.pin_path = self.root / "packaging/icon-dependencies.json"
        self.pin_path.parent.mkdir()
        self.pins = json.loads((ROOT / "packaging/icon-dependencies.json").read_text(encoding="utf-8"))
        for relative in self.pins["selected_files_sha256"]:
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / relative, target)
        self.save()

    def save(self):
        self.pin_path.write_text(json.dumps(self.pins), encoding="utf-8")

    def test_every_changed_original_is_refused_even_with_rewritten_hash(self):
        for relative, digest in self.pins["selected_files_sha256"].copy().items():
            with self.subTest(asset=relative):
                target = self.root / relative
                original = target.read_bytes()
                changed = original + b"ALTERED"
                target.write_bytes(changed)
                with self.assertRaises(ValueError):
                    payload.verify_icon_dependencies(self.root)
                self.pins["selected_files_sha256"][relative] = hashlib.sha256(changed).hexdigest()
                self.save()
                with self.assertRaises(ValueError):
                    payload.verify_icon_dependencies(self.root)
                target.write_bytes(original)
                self.pins["selected_files_sha256"][relative] = digest
                self.save()

    def test_missing_notice_and_provenance_drift_are_refused(self):
        original = copy.deepcopy(self.pins)
        for key in ("version", "git_head", "archive", "archive_sha256", "license_url", "declared_license"):
            self.pins = copy.deepcopy(original)
            self.pins[key] = "UNTRUSTED"
            self.save()
            with self.assertRaises(ValueError):
                payload.verify_icon_dependencies(self.root)
        self.pins = original
        self.save()
        (self.root / (payload.ICON_SOURCE + "LICENSE")).unlink()
        with self.assertRaises(FileNotFoundError):
            payload.verify_icon_dependencies(self.root)


if __name__ == "__main__":
    unittest.main()
