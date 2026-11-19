"""Contract checks for a kit that starts and stops heavyweight local services."""
import importlib.util
import json
from pathlib import Path
import socket
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zgx

class LauncherTests(unittest.TestCase):
    def test_occupied_port_is_rejected_without_stopping_owner(self):
        with socket.socket() as s:
            s.bind(('127.0.0.1', 0))
            with self.assertRaisesRegex(RuntimeError, 'in use'):
                zgx.free_ports([s.getsockname()[1]])
            self.assertGreater(s.fileno(), 0)

    def test_missing_adapter_reports_required_owner_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(RuntimeError, 'chat_template.jinja'):
                zgx.guard_assets(Path(tmp))

    def test_campus_corrupt_model_blocks_launch(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);(p/'docs').mkdir();(p/'models').mkdir()
            (p/'models/bad.gguf').write_bytes(b'not the model')
            m={key:{'file':'models/bad.gguf','sha256':'0'*64} for key in ['generation','embedding','reranker']}
            (p/'docs/MODEL-MANIFEST.json').write_text(json.dumps(m))
            with self.assertRaisesRegex(RuntimeError,'checksum mismatch'):
                zgx.verify_campus_models(p)

    def test_readiness_never_accepts_a_failed_child(self):
        with subprocess.Popen([sys.executable,'-c','raise SystemExit(7)']) as child:
            child.wait()
            with self.assertRaisesRegex(RuntimeError,'exited with code 7'):
                zgx.wait_ready('http://127.0.0.1:1/health',child,1,'example.log')

    def test_cleanup_stops_owned_group_and_leaves_other_process(self):
        other=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'],start_new_session=True)
        owned=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'],start_new_session=True)
        try:
            zgx.cleanup([owned])
            self.assertIsNotNone(owned.poll())
            self.assertIsNone(other.poll())
        finally:
            other.terminate();other.wait()

    def test_engine_metadata_removed_without_changing_raw_payload(self):
        spec=importlib.util.spec_from_file_location('engine_builder',Path(__file__).with_name('build-vision-engine.py'))
        m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
        header=json.dumps({'imgsz':[1088,1088]}).encode()
        raw=b'RAW_TENSORRT_SERIALIZATION'
        self.assertEqual(m.raw_engine(struct.pack('<I',len(header))+header+raw),raw)
        self.assertEqual(m.raw_engine(raw),raw)

    def test_live_vision_validates_engine_with_real_detector_subprocess(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);(p/'models').mkdir();(p/'models/yolov8s.trt').write_bytes(b'fixture')
            with patch.dict(zgx.os.environ,{},clear=True), patch.object(zgx,'run') as execute:
                self.assertEqual(zgx.vision_assets(p),p/'models/yolov8s.trt')
                command=execute.call_args.args[0]
                self.assertIn('assert d.ready',command[2])

if __name__=='__main__':
    unittest.main()
