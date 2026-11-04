"""Layout GUI proposal preparation only; fake source, no Native or real approval."""

import importlib
import unittest
from unittest.mock import patch

fixture = importlib.import_module("test_contract_control")
gui = importlib.import_module("gui_workflow_fixture")
r = importlib.import_module("hwp_review")


class LayoutGuiFixtureTests(unittest.TestCase):
    def stage(self):
        owner = fixture.ContractControlTests(
            "test_default_disabled_never_launches_or_changes_approval"
        )
        owner.setUp()
        self.addCleanup(owner.doCleanups)
        return owner.stage()

    def test_existing_row_policy_compiles_without_writing_or_approving(self):
        workspace, previous, _, _ = self.stage()
        before = {
            str(p.relative_to(workspace)): r.file_hash(p)
            for p in workspace.rglob("*")
            if p.is_file()
        }
        edit = previous["edits"][0]
        result = gui.layout_plan(workspace, edit["old_text"], edit["new_text"])
        self.assertEqual(result, previous)
        self.assertEqual(result["schema"], 3)
        self.assertEqual(
            before,
            {
                str(p.relative_to(workspace)): r.file_hash(p)
                for p in workspace.rglob("*")
                if p.is_file()
            },
        )

    def test_ambiguous_or_unmapped_source_never_compiles(self):
        workspace, plan, _, _ = self.stage()
        edit = plan["edits"][0]
        manifest = r.verify_baseline(workspace)
        match = next(
            a for a in manifest["anchors"] if a["old_text"] == edit["old_text"]
        )
        for changes in (
            [match, match],
            [{**match, "simple": False}],
            [{**match, "boxes": []}],
        ):
            with (
                patch.object(
                    r, "verify_baseline", return_value={**manifest, "anchors": changes}
                ),
                self.assertRaises(ValueError),
            ):
                gui.layout_plan(workspace, edit["old_text"], edit["new_text"])
        with self.assertRaises(ValueError):
            gui.layout_plan(workspace, "unknown original", edit["new_text"])


if __name__ == "__main__":
    unittest.main()
