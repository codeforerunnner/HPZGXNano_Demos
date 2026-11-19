"""CPU speech and object detection adapters. Models are provisioned at setup."""
import argparse
import io
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import wave

# Set before importing ONNX Runtime, including its transitive imports.
os.environ['ORT_DISABLE_TELEMETRY'] = '1'
os.environ['HF_HUB_OFFLINE'] = '1'
os.environ['TRANSFORMERS_OFFLINE'] = '1'
os.environ['HF_HUB_DISABLE_TELEMETRY'] = '1'

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool

ROOT = Path(os.environ.get('ZGX_DEMOS_ROOT', str(Path.home() / 'zgx-demos')))
ASSETS = Path(os.environ.get('ZGX_PUBLIC_ASSETS', str(ROOT / 'public-models')))
WHISPER = Path(os.environ.get('ZGX_WHISPER_BINARY', str(ROOT / '.tools/whisper.cpp/build/bin/whisper-cli')))
lock = threading.Lock()

def speech_app():
    import onnxruntime
    onnxruntime.disable_telemetry_events()
    from piper import PiperVoice
    voices = {lang: PiperVoice.load(ASSETS / name, use_cuda=False) for lang, name in {
        'en':'en_US-ljspeech-medium.onnx', 'es':'es_ES-carlfm-x_low.onnx'}.items()}
    if not WHISPER.is_file() or not (ASSETS / 'ggml-base.bin').is_file():
        raise RuntimeError('Whisper runtime/model missing. Rerun setup.')
    app = FastAPI()

    def tts(text, lang):
        with lock:
            output = io.BytesIO()
            with wave.open(output, 'wb') as wav:
                voices[lang].synthesize_wav(text, wav)
            return output.getvalue()

    @app.get('/health')
    def health():
        return {'ok':True, 'asr':'Whisper base CPU', 'tts':'Piper CPU',
                'languages':['en','es'], 'telemetry':False}

    @app.post('/v1/audio/speech')
    async def speech(req: Request):
        data = await req.json()
        text = data.get('input', '')
        if not isinstance(text,str) or not text.strip() or len(text) > 10000:
            raise HTTPException(400, 'Supply 1 to 10000 text characters.')
        lang = str(data.get('language', 'en'))[:2]
        if lang not in voices:
            raise HTTPException(400, 'This profile supports English and Spanish.')
        result = await run_in_threadpool(tts, text, lang)
        return Response(result, media_type='audio/wav')

    def asr(data):
        with lock, tempfile.TemporaryDirectory(prefix='zgx-speech-') as tmp:
            p = Path(tmp)
            (p / 'upload').write_bytes(data)
            subprocess.run(['ffmpeg','-v','error','-y','-i',str(p/'upload'),'-t','120',
                '-ar','16000','-ac','1',str(p/'audio.wav')], check=True, timeout=30,
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            subprocess.run([str(WHISPER),'-m',str(ASSETS/'ggml-base.bin'),
                '-f',str(p/'audio.wav'),'-otxt','-of',str(p/'result'),'-nt',
                '-l','auto','-t','4'],check=True,timeout=120,
                stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
            return (p/'result.txt').read_text().strip()

    @app.post('/v1/audio/transcriptions')
    async def transcribe(file: UploadFile = File(...)):
        data = await file.read(16*1024*1024 + 1)
        if not data or len(data) > 16*1024*1024:
            raise HTTPException(400, 'Audio must be under 16 MiB and at most 120 seconds.')
        try:
            result = await run_in_threadpool(asr, data)
            return {'text':result}
        except (OSError,subprocess.SubprocessError) as exc:
            raise HTTPException(422, 'Audio could not be transcribed. See speech log.') from exc
    # Exercise synthesis before advertising readiness.
    tts('Atlas is ready.', 'en')
    return app

def vision_app():
    import torch
    from PIL import Image
    from rfdetr import RFDETRNano
    torch.set_num_threads(4)
    model = RFDETRNano(pretrain_weights=str(ASSETS/'rf-detr-nano.pth'), device='cpu')
    model.predict(Image.new('RGB',(320,320)), threshold=.4)
    app = FastAPI()

    @app.get('/health')
    def health():
        return {'ok':True,'model':'RF-DETR Nano','device':'CPU','mock':False}

    def detect(data, threshold):
        start = time.perf_counter()
        with lock:
            image = Image.open(io.BytesIO(data)).convert('RGB')
            image.thumbnail((1920,1080))
            w,h = image.size
            pred = model.predict(image,threshold=threshold)
        objects = []
        for box, score, cid, label in zip(pred.xyxy, pred.confidence, pred.class_id, pred.data['class_name']):
            objects.append({'box':[float(box[0]/w),float(box[1]/h),float(box[2]/w),float(box[3]/h)],
                'score':float(score),'class_id':int(cid),'label':str(label)})
        return {'ok':True,'objects':objects,'people':[x for x in objects if x['label']=='person'],
                'ms':(time.perf_counter()-start)*1000}

    @app.post('/detect')
    async def detect_api(req: Request, threshold: float = .35):
        data = await req.body()
        if len(data) > 16*1024*1024:
            raise HTTPException(413,'Image too large.')
        try:
            return await run_in_threadpool(detect,data,max(.05,min(.95,threshold)))
        except (OSError,ValueError) as exc:
            raise HTTPException(400,'Invalid image.') from exc
    return app

if __name__ == '__main__':
    import uvicorn
    parser = argparse.ArgumentParser()
    parser.add_argument('service', choices=['speech','vision'])
    parser.add_argument('--port',type=int)
    args = parser.parse_args()
    uvicorn.run(speech_app() if args.service=='speech' else vision_app(),
                host='127.0.0.1',port=args.port or (8110 if args.service=='speech' else 8130),access_log=False)
