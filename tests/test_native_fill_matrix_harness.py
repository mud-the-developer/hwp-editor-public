"""Test native-matrix harness WITHOUT launching Hancom or creating actual consent."""
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

import native_fill_matrix as matrix
import test_hwp_form_draft as fixtures


class MatrixHarnessTests(unittest.TestCase):
    def test_void_native_navigation_is_success_not_false_failure(self):
        app=SimpleNamespace(Run=Mock(return_value=None))
        matrix.navigate(app,'BreakPara')
        app.Run.assert_called_once_with('BreakPara')

    def test_real_navigation_exception_stops_without_retry(self):
        app=SimpleNamespace(Run=Mock(side_effect=RuntimeError('synthetic COM failure')))
        with self.assertRaisesRegex(RuntimeError,'synthetic COM failure'):matrix.navigate(app,'BreakPara')
        app.Run.assert_called_once_with('BreakPara')

    def test_outside_or_existing_output_is_rejected_before_native_creation(self):
        with tempfile.TemporaryDirectory() as directory,patch.object(matrix,'create_source') as create:
            for target in [Path(directory),Path(directory)/'toy-fill-matrix-fixture']:
                with self.subTest(target=target),self.assertRaises(ValueError):matrix.run(target)
            create.assert_not_called()

    def test_optional_cell_check_uses_real_inspector_schema_and_exact_address(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'fixture.hwpx';fixtures.package(path)
            target=next(item for item in matrix.f.inspect_template(path)['fields'] if item['supported'])
            self.assertTrue(matrix.optional_cell_empty(path,target))
            fixtures.package(path,cells=fixtures.cell(0,0,'성명')+fixtures.cell(0,1,'not empty')+fixtures.cell(1,0,'성명')+fixtures.cell(1,1))
            self.assertFalse(matrix.optional_cell_empty(path,target))
            with self.assertRaisesRegex(ValueError,'address missing or ambiguous'):
                matrix.optional_cell_empty(path,{**target,'cell_path':[999]})

    def test_two_generated_pictures_are_distinct_and_self_contained(self):
        with tempfile.TemporaryDirectory() as directory:
            pictures=[Path(directory)/f'fixture-{i}.png' for i in (1,2)]
            for i,path in enumerate(pictures,1):matrix.chart(path,i)
            self.assertNotEqual(matrix.r.file_hash(pictures[0]),matrix.r.file_hash(pictures[1]))
            for path in pictures:
                self.assertTrue(path.read_bytes().startswith(b'\x89PNG\r\n\x1a\n'))
                image=matrix.r.fitz.Pixmap(str(path));self.assertEqual((image.width,image.height),(600,180))
