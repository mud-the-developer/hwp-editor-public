"""TOY creator + real owned-session orchestration with fake COM/OS only."""

import importlib
import json
from pathlib import Path
import unittest
from unittest.mock import Mock, patch
import test_native_owner as fixtures

m = importlib.import_module("native_fill_smoke")


class ToySourceOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.harness = fixtures.OwnedSessionTests()
        self.harness.setUp()
        self.addCleanup(self.harness.doCleanups)
        self.directory = self.harness.root / "source"
        self.seed = self.directory / "TOY-ONLY-authoring-seed.hwp"
        self.source = self.directory / "TOY-ONLY-empty-template.hwp"
        self.app = self.harness.app
        self.app.RegisterModule = Mock(return_value=True)
        self.app.SaveAs = Mock(side_effect=self.save)
        self.app.PageCount = 1
        self.author = Mock(side_effect=self.mutate_owned_seed)
        for patcher in (
            patch.object(m, "hwp_inventory", return_value=[]),
            patch.object(
                m, "registered_security_module", return_value={"SYNTHETIC": True}
            ),
            patch.object(m.fixture, "author", self.author),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def save(self, name, fmt, options):
        path = Path(name)
        self.assertEqual(fmt, "HWP")
        self.assertEqual(options, "")
        with path.open("xb") as stream:
            stream.write(b"SYNTHETIC SEED" if path == self.seed else b"SYNTHETIC TOY")
        document = self.harness.doc
        document.FullName, document.Path = str(path), str(path.parent)
        document.Modified = False
        return True

    def mutate_owned_seed(self, app):
        self.assertTrue(self.seed.is_file())
        self.assertEqual(self.harness.doc.FullName, str(self.seed))
        self.assertFalse(self.source.exists())
        self.harness.doc.Modified = True
        self.app.GetTextFile.return_value = "SYNTHETIC AUTHORED BUFFER"

    def proof(self):
        return json.loads(
            (self.directory / "TOY-SOURCE-CREATION.json").read_text(encoding="utf-8")
        )

    def assert_owned_cleanup(self):
        self.app.Clear.assert_called_once_with(1)
        self.app.Quit.assert_called_once()
        self.harness.held.wait_for_exit.assert_called_once()
        self.harness.held.close.assert_called_once()
        self.harness.com.CoUninitialize.assert_called_once()

    def test_seed_is_named_before_mutation_and_final_source_is_a_different_new_file(
        self,
    ):
        result = m.create_source(self.directory)
        self.assertEqual(result, self.source)
        self.assertEqual(self.seed.read_bytes(), b"SYNTHETIC SEED")
        self.assertEqual(self.source.read_bytes(), b"SYNTHETIC TOY")
        self.assertEqual(
            [Path(call.args[0]) for call in self.app.SaveAs.call_args_list],
            [self.seed, self.source],
        )
        self.assert_owned_cleanup()
        proof = self.proof()
        self.assertEqual(proof["ownership"]["status"], "owned_exit_confirmed")
        self.assertEqual(proof["events"], ["blank_seed_saved", "source_saved"])
        self.assertFalse(proof["qualification"])
        self.assertEqual(proof["source_sha256"], m.r.file_hash(self.source))

    def test_seed_failure_stops_before_authoring_and_cleans_only_verified_blank(self):
        self.app.SaveAs.side_effect = None
        self.app.SaveAs.return_value = False
        with self.assertRaisesRegex(RuntimeError, "save failed"):
            m.create_source(self.directory)
        self.author.assert_not_called()
        self.app.SaveAs.assert_called_once()
        self.assert_owned_cleanup()
        self.assertEqual(self.proof()["status"], "blocked")

    def test_author_failure_leaves_named_seed_and_can_confirm_exact_owned_exit(self):
        def fail(app):
            self.mutate_owned_seed(app)
            raise RuntimeError("SYNTHETIC author failure")

        self.author.side_effect = fail
        with self.assertRaisesRegex(RuntimeError, "author failure"):
            m.create_source(self.directory)
        self.assertTrue(self.seed.is_file())
        self.assertFalse(self.source.exists())
        self.app.SaveAs.assert_called_once()
        self.assert_owned_cleanup()
        self.assertEqual(self.proof()["status"], "blocked")
        self.assertTrue(self.proof()["ownership"]["exit_confirmed"])

    def test_file_appearing_during_authoring_is_never_overwritten(self):
        def collision(app):
            self.mutate_owned_seed(app)
            self.source.write_bytes(b"KEEP COLLISION EVIDENCE")

        self.author.side_effect = collision
        with self.assertRaises(FileExistsError):
            m.create_source(self.directory)
        self.app.SaveAs.assert_called_once()
        self.assertEqual(self.source.read_bytes(), b"KEEP COLLISION EVIDENCE")
        self.assert_owned_cleanup()

    def test_existing_directory_or_process_stops_before_com(self):
        self.directory.mkdir()
        with self.assertRaises(FileExistsError):
            m.create_source(self.directory)
        m.registered_security_module.assert_not_called()
        self.directory.rmdir()
        m.hwp_inventory.return_value = [{"pid": 99, "creation_ms": 1}]
        with self.assertRaises(RuntimeError):
            m.create_source(self.directory)
        self.harness.client.DispatchEx.assert_not_called()
        self.assertFalse(self.directory.exists())

    def test_unowned_instance_is_not_authored_saved_cleared_or_quit(self):
        self.harness.metadata.bind_window_process.side_effect = RuntimeError(
            "SYNTHETIC unowned"
        )
        with self.assertRaisesRegex(RuntimeError, "unconfirmed"):
            m.create_source(self.directory)
        self.author.assert_not_called()
        self.app.SaveAs.assert_not_called()
        self.app.Clear.assert_not_called()
        self.app.Quit.assert_not_called()
        self.assertEqual(self.proof()["status"], "blocked")

    def test_registration_failure_does_not_author_or_save(self):
        self.app.RegisterModule.return_value = False
        with self.assertRaisesRegex(RuntimeError, "module rejected"):
            m.create_source(self.directory)
        self.author.assert_not_called()
        self.app.SaveAs.assert_not_called()
        self.assert_owned_cleanup()
        self.assertEqual(self.proof()["status"], "blocked")


if __name__ == "__main__":
    unittest.main()
