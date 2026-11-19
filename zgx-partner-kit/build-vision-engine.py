#!/usr/bin/env python3
"""Build YOLOv8s on target hardware; strip Ultralytics engine metadata.

Prerequisites: matching CUDA-enabled PyTorch, Ultralytics and TensorRT in an
operator-prepared environment on the Nano. This helper does not install them.
Review https://www.ultralytics.com/license before commercial use/distribution.
"""
import hashlib
import json
import os
from pathlib import Path
import platform
import struct

def raw_engine(data):
    # Ultralytics writes 4-byte JSON length + JSON before serialized TensorRT.
    # Research Observatory expects only the raw TensorRT bytes.
    if len(data) > 8:
        size = struct.unpack('<I', data[:4])[0]
        if 0 < size < min(1024*1024, len(data)-4):
            try:
                meta = json.loads(data[4:4+size])
                if isinstance(meta, dict):
                    return data[4+size:]
            except (ValueError, UnicodeDecodeError):
                pass
    return data

def main():
    if platform.machine() != 'aarch64':
        raise RuntimeError('Export must run on the target Nano, not this host.')
    import torch
    import tensorrt
    from ultralytics import YOLO
    if not torch.cuda.is_available():
        raise RuntimeError('CUDA-enabled PyTorch is required on this Nano.')
    root = Path(os.environ.get('ZGX_DEMOS_ROOT', str(Path.home()/'zgx-demos'))).expanduser().resolve()
    folder = root / 'research-observatory/models'
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / 'yolov8s.trt'
    if dest.exists():
        raise RuntimeError(f'Refusing to overwrite {dest}. Preserve or rename it first.')
    os.chdir(folder)
    source = Path(YOLO('yolov8s.pt').export(format='engine', imgsz=1088, batch=1,
                   dynamic=False, half=True, nms=False, device=0))
    data = raw_engine(source.read_bytes())
    logger = tensorrt.Logger(tensorrt.Logger.ERROR)
    with tensorrt.Runtime(logger) as runtime:
        engine = runtime.deserialize_cuda_engine(data)
        if engine is None:
            raise RuntimeError('Exported engine failed deserialization on this Nano.')
    dest.write_bytes(data)
    (folder / 'partner-engine-receipt.json').write_text(json.dumps({
        'model': 'yolov8s.pt', 'engine_sha256': hashlib.sha256(data).hexdigest(),
        'gpu': torch.cuda.get_device_name(0), 'tensorrt': tensorrt.__version__,
        'torch': torch.__version__, 'input': [1,3,1088,1088],
        'note': 'Built locally. Live detector acceptance still required.'
    }, indent=2))
    print(dest)

if __name__ == '__main__':
    main()
