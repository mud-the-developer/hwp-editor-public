"""CLI preflight failures must exit without starting GUI/setup or changing files."""
import contextlib
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import hwp_desktop as desktop


class RequirementsCliTests(unittest.TestCase):
    def test_existing_report_rejection_is_a_failed_cli_exit_and_preserves_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary).resolve() / "existing.json"
            original = b'{"SYNTHETIC":"existing consumer report must not be overwritten"}\n'
            target.write_bytes(original)
            with patch.object(sys, "argv", ["HWPReview", "--check-requirements", str(target)]), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as stopped:
                    desktop.main()
            self.assertEqual(stopped.exception.code, 1)
            self.assertEqual(target.read_bytes(), original)

    def test_invalid_report_destination_is_a_failed_cli_exit_without_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            for target in (Path("relative.json"), root / "wrong.txt", root / "missing" / "status.json"):
                with self.subTest(target=str(target)), patch.object(sys, "argv", ["HWPReview", "--check-requirements", str(target)]), contextlib.redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit) as stopped:
                        desktop.main()
                    self.assertEqual(stopped.exception.code, 1)
                self.assertEqual(list(root.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
