"""Non-destructive adapter: labels synthetic media and isolates MOCK from AI."""
import argparse
import json
import os
from pathlib import Path
import sys

KIT = Path(__file__).resolve().parent
ROOT = Path(os.environ.get('ZGX_DEMOS_ROOT', str(Path.home() / 'zgx-demos'))).expanduser().resolve()
PROJECT = ROOT / 'research-observatory'
sys.path.insert(0, str(PROJECT))
MOCK = os.environ.get('ZGX_PARTNER_MOCK') == '1'
if MOCK:
    os.environ['ZGX_MOCK'] = '1'
    import ollama_client
    ollama_client.is_available = lambda: False
    ollama_client.model_loaded = lambda *a, **k: False
    ollama_client.embed = lambda *a, **k: []
    def no_generate(*args, **kwargs):
        raise RuntimeError('Live AI is disabled in the partner MOCK walkthrough.')
    ollama_client.generate = no_generate

import app as original
from fastapi.responses import HTMLResponse

# Retain approved original videos when available in LIVE mode. Otherwise use
# the kit's four generated clips with unmistakable labels.
synthetic = MOCK or not any((PROJECT / 'media' / x['file']).exists()
                           for x in json.loads((PROJECT / 'media/manifest.json').read_text()))
if synthetic:
    original.MANIFEST = KIT / 'assets/synthetic-manifest.json'

text = ('MOCK WALKTHROUGH | SYNTHETIC MEDIA | No live detector or AI inference'
        if MOCK else 'SYNTHETIC SAMPLE MEDIA | Live detector mode | Not an accuracy benchmark'
        if synthetic else 'LIVE DEMO | Operator supplied media | Rehearse before presenting')
banner = ('<div style="background:#003d57;color:#fff;padding:12px 24px;'
          'font:600 15px system-ui;position:sticky;top:0;z-index:9999">' + text + '</div>')
for route in original.app.routes:
    if getattr(route, 'path', '') == '/' and getattr(route, 'methods', None) == {'GET'}:
        original.app.routes.remove(route)
        break

@original.app.get('/', response_class=HTMLResponse)
def partner_index():
    html = (PROJECT / 'static/index.html').read_text()
    return html.replace('<body>', '<body>' + banner, 1)

if __name__ == '__main__':
    import uvicorn
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=18120)
    args = parser.parse_args()
    uvicorn.run(original.app, host='127.0.0.1', port=args.port, access_log=False)
