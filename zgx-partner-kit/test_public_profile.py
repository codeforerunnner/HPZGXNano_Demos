"""Contract and failure-path tests. No model downloads or GPU required."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import public_client as c
import portable

class PublicProfileTests(unittest.TestCase):
    def test_guard_requires_exact_schema(self):
        good={'decision':'YES','citations':['E1'],'conditions':['SATISFIED | consent'],'rationale':'Evidence supports it.'}
        self.assertEqual(c.strict_parse(json.dumps(good)),good)
        for changed in [dict(good,extra=1),dict(good,decision='MAYBE'),dict(good,citations='E1')]:
            self.assertIsNone(c.strict_parse(json.dumps(changed)))
        self.assertIsNone(c.strict_parse('not JSON'))

    def test_embedding_rejects_missing_vectors(self):
        with patch.object(c,'request',return_value={'data':[]}):
            with self.assertRaises(RuntimeError):c.embed(['hello'])

    def test_generation_never_fabricates_fallback(self):
        with patch.object(c,'request',return_value={'choices':[{'message':{'content':''}}]}):
            with self.assertRaises(RuntimeError):c.generate('hello')

    def test_empty_embedding_list_does_not_contact_service(self):
        with patch.object(c,'request',side_effect=AssertionError):
            self.assertEqual(c.embed([]),[])

    def test_missing_receipt_blocks_launch(self):
        with tempfile.TemporaryDirectory() as d,patch.object(portable,'ROOT',Path(d)):
            with self.assertRaisesRegex(RuntimeError,'receipt'):portable.verify('guard')

    def test_all_additional_assets_pinned(self):
        items=portable.assets()
        self.assertEqual(len({x['file'] for x in items}),len(items))
        for item in items:
            self.assertRegex(item['sha256'],r'^[a-f0-9]{64}$')
            self.assertGreater(item['bytes'],0)
            self.assertTrue(item['url'].startswith('https://'))

    def test_hash_failure_cannot_replace_installed_asset(self):
        import io
        class Response(io.BytesIO):
            status=200
        with tempfile.TemporaryDirectory() as d,patch.object(portable,'ROOT',Path(d)):
            p=Path(d)/'public-models';p.mkdir();target=p/'test.bin';target.write_bytes(b'original')
            with patch.object(portable.urllib.request,'urlopen',return_value=Response(b'bad')):
                with self.assertRaisesRegex(RuntimeError,'checksum'):
                    portable.download_asset({'file':'test.bin','url':'https://example.invalid/test','sha256':'0'*64,'bytes':3})
            self.assertEqual(target.read_bytes(),b'original')
            self.assertFalse((p/'test.bin.partial').exists())

if __name__=='__main__':unittest.main()
