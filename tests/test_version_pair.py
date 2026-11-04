"""Two owned synthetic bound PDFs only; no Native/actual documents/approval."""
import http.client
from pathlib import Path
import sys
import threading
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from scripts import hwp_review as r
import test_result_download as download_fixture


class VersionPairTests(unittest.TestCase):
    def setUp(self):
        self.fixture=download_fixture.ResultDownloadTests('test_exact_hwp_and_reopened_pdf_metadata_bytes_and_hashes_are_read_only');self.fixture.setUp();self.addCleanup(self.fixture.temp.cleanup)
        self.w=self.fixture.w;self.m=self.fixture.m;self.first=self.fixture.run_dir;self.second=self.w/'runs/20261013T000001-b1b2c3d4';self.second.mkdir()
        for name in r.RESULT_ARTIFACTS:(self.second/name).write_bytes((self.first/name).read_bytes())
        (self.second/'candidate.pdf').unlink()
        with r.fitz.open() as doc:
            doc.new_page(width=300,height=400).insert_text((20,40),'SECOND SYNTHETIC PDF');doc.save(self.second/'candidate.pdf')
        r.seal_result(self.second,self.m,self.fixture.plan)

    def test_version_pair_png_diff_is_read_only_and_current_bindings_required(self):
        before={str(p.relative_to(self.w)):r.file_hash(p) for p in self.w.rglob('*') if p.is_file()}
        blob=r.comparison_image(self.w,self.second,'diff',1,100,before_pdf=self.first/'candidate.pdf');self.assertTrue(blob.startswith(b'\x89PNG'))
        self.assertEqual(before,{str(p.relative_to(self.w)):r.file_hash(p) for p in self.w.rglob('*') if p.is_file()})

    def test_actual_pair_route_blocks_unbound_or_changed_history(self):
        server=r.create_review_server(self.w);thread=threading.Thread(target=server.serve_forever);thread.start()
        def get():
            conn=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=5)
            try:
                conn.request('GET',f'/compare-pair/{self.first.name}/{self.second.name}/diff/1.png');response=conn.getresponse();return response.status,response.read()
            finally:conn.close()
        try:
            code,blob=get();self.assertEqual(code,200);self.assertTrue(blob.startswith(b'\x89PNG'))
            (self.first/'candidate.hwp').write_bytes(b'CHANGED');self.assertEqual(get()[0],400)
        finally:server.shutdown();thread.join(5);server.server_close()


if __name__=='__main__':unittest.main()
