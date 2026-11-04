"""Policy fixtures are source STRINGS, never executed/imported or native-run."""

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from hwp_safety_lint import check_source


class SafetyLintTests(unittest.TestCase):
    def rules(self, source, filename="scripts/fixture.py"):
        return {finding["rule"] for finding in check_source(source, filename)}

    def test_unsafe_com_creation_and_aliases_are_blocked(self):
        examples = [
            'import win32com.client as w\nw.Dispatch("HWPFrame.HwpObject")',
            'from win32com.client import EnsureDispatch as connect\nconnect("HWPFrame.HwpObject")',
            'import win32com.client as w\nprog="HWPFrame.HwpObject"\nw.gencache.EnsureDispatch(prog)',
            "from pyhwpx import Hwp as Editor\nEditor(new=True, visible=False)",
            "import pyhwpx as p\np.Hwp()",
            'w.GetActiveObject("HWPFrame.HwpObject")',
        ]
        for source in examples:
            with self.subTest(source=source):
                self.assertIn("HWP001", self.rules(source))
        self.assertFalse(
            self.rules(
                'app=w.gencache.EnsureDispatch(w.DispatchEx("HWPFrame.HwpObject"))'
            )
        )
        self.assertFalse(self.rules('w.Dispatch("Excel.Application")'))

    def test_hidden_windows_required_including_setattr_and_annotations(self):
        for source in [
            "app.XHwpWindows.Item(0).Visible=True",
            "window.Visible=show",
            "window.Visible: bool=True",
            'setattr(window,"Visible",True)',
        ]:
            with self.subTest(source=source):
                self.assertIn("HWP002", self.rules(source))
        self.assertFalse(
            self.rules('window.Visible=False\nsetattr(window,"Visible",False)')
        )

    def test_destructive_actions_not_comments_or_strings(self):
        for source in [
            'app.Run("AllReplace")',
            'app.HAction.Execute("AllReplace", p)',
            'app.Run("TableDeleteRow")',
        ]:
            self.assertIn("HWP003", self.rules(source))
        self.assertFalse(
            self.rules('"""app.Run("AllReplace")"""\n# app.Run("AllReplace")')
        )
        self.assertFalse(self.rules('app.HAction.Execute("InsertText", p)'))
        self.assertIn("HWP003", self.rules('action="AllReplace"\napp.Run(action)'))

    def test_auto_layout_and_guidance_deletion_are_not_silent_defaults(self):
        for source in [
            "fit_rows(doc,t)",
            "clear_guidance(doc)",
            "table.autofit()",
            "set_cell(doc,cell,text,flatten=True)",
        ]:
            self.assertIn("HWP004", self.rules(source))
        self.assertFalse(self.rules("inspect_cell(doc,cell)"))

    def test_empty_global_replace_does_not_identify_a_blank_cell(self):
        self.assertIn("HWP005", self.rules('text.replace("",value)'))
        self.assertFalse(self.rules('text.replace("known slot",value,1)'))

    def test_global_cache_traversal_blocked_but_selected_paragraph_cache_supported(
        self,
    ):
        for source in [
            'root.findall(".//hp:linesegarray")',
            'root.iter(HP+"linesegarray")',
            'root.xpath("//hp:linesegarray")',
        ]:
            self.assertIn("HWP006", self.rules(source))
        self.assertFalse(self.rules('para.findall(HP+"linesegarray")'))
        self.assertFalse(self.rules('para.find(HP+"linesegarray")'))

    def test_approval_writer_is_exact_existing_ui_only(self):
        source = 'write_json(workspace/"approved-plan.json",plan)'
        self.assertIn("HWP007", self.rules(source))
        self.assertIn(
            "HWP007",
            self.rules(
                'target=workspace/"approved-plan.json"\ntarget.write_text(data)'
            ),
        )
        ui = (
            "def create_review_server():\n class Handler:\n  def do_POST(self):\n   "
            + source
        )
        self.assertFalse(self.rules(ui, "scripts/hwp_review.py"))
        template_ui = ui.replace("class Handler:", "class TemplateHandler:")
        self.assertFalse(self.rules(template_ui, "scripts/hwp_review.py"))
        self.assertIn("HWP007", self.rules(template_ui, "scripts/other_server.py"))
        self.assertIn(
            "HWP007",
            self.rules(
                template_ui.replace(
                    "create_review_server", "create_template_review_server"
                ),
                "scripts/hwp_review.py",
            ),
        )
        self.assertIn(
            "HWP007",
            self.rules(
                template_ui.replace("do_POST", "do_GET"), "scripts/hwp_review.py"
            ),
        )
        self.assertIn("HWP007", self.rules(ui, "scripts/other_server.py"))
        self.assertIn(
            "HWP007",
            self.rules(
                ui.replace("create_review_server", "serve"), "scripts/hwp_review.py"
            ),
        )
        self.assertIn(
            "HWP007",
            self.rules(ui.replace("do_POST", "start"), "scripts/hwp_review.py"),
        )
        self.assertIn("HWP007", self.rules(ui, "scripts/hwp_desktop.py"))
        self.assertFalse(
            self.rules(source, "tests/test_fixture.py"),
            "explicit synthetic test contract writer",
        )
        self.assertFalse(self.rules('read_json(workspace/"approved-plan.json")'))
        self.assertIn(
            "HWP007", self.rules('Path("example/original.hwp").write_bytes(data)')
        )
        self.assertIn(
            "HWP007", self.rules('r.write_json(workspace/"baseline.hwpx",data)')
        )
        self.assertIn("HWP007", self.rules('open("example/original.hwp","wb")'))
        self.assertIn("HWP007", self.rules('Path("baseline.hwpx").open("wb")'))
        self.assertIn(
            "HWP007", self.rules('shutil.copy2(source, workspace/"baseline.hwp")')
        )
        self.assertFalse(self.rules('open("example/original.hwp","rb")'))

    def test_draft_and_artifact_authority_cannot_be_persisted_outside_existing_ui(self):
        for name in ("draft-permission.json", "acceptance.json", "artifact-1-acceptance.json"):
            source = f'path=workspace/"{name}"\npath.write_text(data)'
            self.assertIn("HWP007", self.rules(source))
            ui = (
                "def create_review_server():\n class TemplateHandler:\n"
                "  def persist_authority(self):\n   "
                + source.replace("\n", "\n   ")
            )
            self.assertFalse(self.rules(ui, "scripts/hwp_review.py"))
            self.assertIn("HWP007", self.rules(ui, "scripts/other_server.py"))
            self.assertIn("HWP007", self.rules(ui.replace("persist_authority", "do_GET"), "scripts/hwp_review.py"))
        native_record = (
            "def create_review_server():\n class TemplateHandler:\n"
            '  def persist_authority(self):\n   write_json(workspace/"approved-plan.json",data)'
        )
        self.assertIn("HWP007", self.rules(native_record, "scripts/hwp_review.py"))

    def test_direct_field_writes_remain_closed_until_new_ui_contract(self):
        self.assertIn("HWP008", self.rules('app.PutFieldText("title",value)'))
        self.assertIn("HWP008", self.rules("app.put_field_text(fields,values)"))
        self.assertIn("HWP008", self.rules('put=app.PutFieldText\nput("title",value)'))
        self.assertFalse(self.rules('app.GetFieldList(1,0)\napp.GetFieldText("title")'))

    def test_syntax_error_is_failure_and_no_inline_suppression(self):
        self.assertIn("HWP000", self.rules("def broken("))
        self.assertIn("HWP003", self.rules('# hwp-lint:ignore\napp.Run("AllReplace")'))
        finding = check_source('\napp.Run("AllReplace")')[0]
        self.assertEqual(finding["line"], 2)
        self.assertEqual(finding["column"], 1)
