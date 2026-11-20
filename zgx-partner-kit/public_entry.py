"""Additive public profile: original model paths and source remain intact."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import sys
import time
import types

KIT = Path(__file__).resolve().parent
ROOT = Path(os.environ.get('ZGX_DEMOS_ROOT', str(Path.home()/'zgx-demos')))
parser = argparse.ArgumentParser()
parser.add_argument('demo',choices=['atlas','guard','vision'])
parser.add_argument('--port',type=int,required=True)
args = parser.parse_args()
PROJECT = ROOT / {'atlas':'atlas-claude','guard':'beat-the-guard','vision':'research-observatory'}[args.demo]
sys.path.insert(0,str(PROJECT))
sys.path.insert(0,str(PROJECT/'src'))
import public_client as client

if args.demo == 'guard':
    sys.modules['server.guard'] = client
    import server.main as original
    app = original.app
    banner = 'PUBLIC MODEL PROFILE | Qwen policy game | Not the trained Sentinel V6 model'
elif args.demo == 'vision':
    import cv2
    import urllib.request
    class PublicDetector:
        def __init__(self, engine_path):
            self.ready = False
            self.error = ''
            try:
                self.ready = client.request('http://127.0.0.1:8130','/health',timeout=5).get('ok',False)
            except Exception as exc:
                self.error = str(exc)
        def info(self):
            return {'ready':self.ready,'error':self.error,'model':'RF-DETR Nano',
                    'backend':'PyTorch CPU','engine':'Public CPU profile','mock':False}
        def infer(self, frame, conf_thres=.35, **kwargs):
            ok, data = cv2.imencode('.jpg',frame)
            if not ok:
                raise RuntimeError('Unable to encode detector frame.')
            req = urllib.request.Request(f'http://127.0.0.1:8130/detect?threshold={conf_thres}',
                data=data.tobytes(),headers={'Content-Type':'image/jpeg'})
            with urllib.request.urlopen(req,timeout=30) as r:
                result = json.load(r)
            h,w = frame.shape[:2]
            return [{'bbox':[int(v*s) for v,s in zip(d['box'],[w,h,w,h])],
                     'conf':d['score'],'class_id':d['class_id'],'label':d['label']}
                    for d in result['objects']]
    module = types.ModuleType('yolo_trt')
    module.TRTDetector = PublicDetector
    sys.modules['yolo_trt'] = module
    def research_generate(prompt, system='', model=None, timeout=300, keep_alive=None):
        fields = ['headline','observation','impact','local_advantage','next_action','value_levers']
        schema = {'type':'object','additionalProperties':False,'required':fields,
            'properties':{key:{'type':'string'} for key in fields[:-1]}}
        schema['properties']['value_levers'] = {'type':'array','maxItems':3,'items':{
            'type':'string','enum':['Data locality','Cloud usage avoidance','Faster response',
                'Staff attention','Offline resilience','Predictable infrastructure']}}
        messages = ([{'role':'system','content':system}] if system else [])
        return client.chat(messages+[{'role':'user','content':prompt}],schema=schema,timeout=timeout)
    client.generate = research_generate
    sys.modules['ollama_client'] = client
    os.environ['ZGX_MOCK'] = '0'
    os.environ['ZGX_ENGINE'] = str(ROOT/'public-models/rf-detr-nano.pth')
    import event_memory
    # Keep embeddings from different models in separate databases.
    event_memory.DB = PROJECT/'data/public-qwen-memory.sqlite3'
    import app as original
    original.MANIFEST = ROOT/'public-media/manifest.json'
    original.SYSTEM_PROMPT = original.SYSTEM_PROMPT.replace('TensorRT object detector','RF-DETR CPU object detector')
    app = original.app
    banner = 'PUBLIC MODEL PROFILE | RF-DETR on CPU + Qwen 35B | Sample media is illustrative, not a lab benchmark'
else:
    os.environ['ATLAS_LLM_URL'] = client.GEN
    os.environ['ATLAS_SPEECH_URL'] = 'http://127.0.0.1:8110'
    os.environ['ATLAS_DEV'] = '0'
    os.environ['ATLAS_FINETUNE'] = '0'
    from atlas import brain
    class PublicDecisions:
        def __init__(self,llm):
            self.llm, self.ok, self.engine = llm, False, 'qwen-option-probabilities'
        async def health(self):
            self.ok = await self.llm.health()
            return self.ok
        async def decide(self,decisions):
            start = time.perf_counter()
            items = []
            for d in decisions:
                if d['type']=='noul':
                    probs = await self.llm.option_probs(d['state'],d['question'],['Yes','No'])
                    items.append({'label':d.get('label') or d['question'],'question':d['question'],
                                  'type':'noul','p_yes':probs[0]})
                else:
                    names = list(d['options'])
                    probs = await self.llm.option_probs(d['state'],d['question'],names)
                    items.append({'label':d['question'],'question':d['question'],'type':'choice',
                                  'options':[{'name':n,'p':p} for n,p in zip(names,probs)]})
            return {'engine':self.engine,'ms':(time.perf_counter()-start)*1000,'items':items}
    brain.SystemOne = PublicDecisions
    from atlas.knowledge import docs
    import numpy as np
    def embed_docs(texts,kind):
        start = time.perf_counter()
        vec = client.embed(texts)
        return np.array(vec,dtype=np.float32),(time.perf_counter()-start)*1000
    docs.embed = embed_docs
    docs.EMBED_MODEL = 'Qwen/Qwen3-Embedding-0.6B-GGUF'
    docs.INDEX_DIR = PROJECT/'data/public-qwen-index'
    docs.INDEX_DIR.mkdir(parents=True,exist_ok=True)
    import atlas.server as original
    import httpx
    original.speech.client.timeout = httpx.Timeout(150,connect=1)
    original.decision_model_status = lambda: {
        'base':'Qwen option probabilities','ok':original.s1.ok,
        'calibration':'Uncalibrated model scores; illustrative only',
        'finetune':None,'finetune_text':'Public profile; no Kev or fine-tuned checkpoint'}
    app = original.app
    banner = 'PUBLIC MODEL PROFILE | Qwen + Whisper + Piper | Synthetic business data | Decision scores are uncalibrated'

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

REPLACEMENTS = {
    'NVIDIA Nemotron 3.5 Lightning 30B-A3B':'Qwen3.6 35B A3B',
    'Nemotron 3.5 Lightning':'Qwen3.6 35B',
    'Nemotron Speech 0.6B':'Whisper base multilingual',
    'Nemotron Speech':'Whisper base',
    'NeMo-Speech.cpp · CUDA':'whisper.cpp · CPU',
    'Magpie TTS 357M':'Piper LJSpeech / CarlFM',
    'Magpie':'Piper', 'Q8_0 · CUDA graphs':'ONNX · CPU',
    'System One decisions 4B':'Qwen option probabilities',
    'calibrated typed decisions':'uncalibrated illustrative scores',
    'calibrated probabilities':'uncalibrated model scores',
    'calibrated yes/no and choice probabilities':'uncalibrated yes/no and choice scores',
    'Sentinel V6':'Public Qwen policy profile', 'SENTINEL V6':'PUBLIC QWEN PROFILE',
    'LLAMA 3.1 70B':'QWEN3.6 35B','Llama 3.1 70B':'Qwen3.6 35B',
    '70B':'35B','TensorRT':'RF-DETR CPU', 'YOLOv8s':'RF-DETR Nano',
    'Ollama is not available.':'Local Qwen server is not available.'}

class ProfileLabels(BaseHTTPMiddleware):
    async def dispatch(self,request,call_next):
        response = await call_next(request)
        ct = response.headers.get('content-type','')
        # Do not buffer SSE, websocket, media or exports.
        if not any(t in ct for t in ('text/html','javascript','application/json')):
            return response
        body = b''.join([chunk async for chunk in response.body_iterator])
        try:
            text = body.decode('utf-8')
        except UnicodeDecodeError:
            return Response(body,status_code=response.status_code,headers=dict(response.headers))
        for before,after in REPLACEMENTS.items():
            text = text.replace(before,after)
        if 'text/html' in ct:
            bar = (
                '<div style="display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap;'
                'padding:10px 22px;background:#003d57;color:white;font:600 13px system-ui;position:relative;z-index:9999">'
                '<span>'+banner+'</span>'
                '<span style="display:inline-flex;background:#09131d;border:1px solid #2b4a66;border-radius:7px;overflow:hidden;">'
                '<button id="zgx-btn-es" onclick="window.__zgxSetLang(\'es\')" style="border:none;background:transparent;color:#cde4f7;padding:4px 10px;font-size:11px;font-weight:700;cursor:pointer;">CASTELLANO</button>'
                '<button id="zgx-btn-en" onclick="window.__zgxSetLang(\'en\')" style="border:none;background:transparent;color:#cde4f7;padding:4px 10px;font-size:11px;font-weight:700;cursor:pointer;">ENGLISH</button>'
                '</span></div>'
            )
            i18n_js = r'''<script>
(function(){
  const DICT = {
    "Decision Insight": "Analisis de Decision",
    "Ready for a local insight": "Listo para generar analisis local",
    "WHAT CHANGED": "QUE CAMBIO",
    "Start a demo and let the event engine build context.": "Inicia una demo y deja que el motor de eventos construya contexto.",
    "WHY IT MATTERS": "POR QUE IMPORTA",
    "The reasoning layer prioritizes the highest-signal change instead of describing every frame.": "La capa de razonamiento prioriza el cambio mas relevante en lugar de describir cada fotograma.",
    "LOCAL AI ADVANTAGE": "VENTAJA DE IA LOCAL",
    "Vision, reasoning and memory are designed to execute on the Nano.": "Vision, razonamiento y memoria estan disenados para ejecutarse en la Nano.",
    "NEXT": "SIGUIENTE",
    "Start a feed, then generate an insight after a meaningful event.": "Inicia un flujo y genera un analisis tras un evento relevante.",
    "Generate Local Insight": "Generar Analisis Local",
    "PHYSICAL WORKFLOW": "FLUJO FISICO",
    "Specimen A": "Especimen A",
    "WAITING": "ESPERANDO",
    "Current zone": "Zona actual",
    "NOT SEEN": "NO DETECTADO",
    "PREP": "PREP",
    "ANALYSIS": "ANALISIS",
    "COMPLETE": "COMPLETO",
    "LOCAL AI VALUE MODEL": "MODELO DE VALOR IA LOCAL",
    "Turn the demo into a business case.": "Convierte la demo en un caso de negocio.",
    "Local vision inferences": "Inferencias de vision local",
    "Significant events surfaced": "Eventos significativos detectados",
    "Local 70B insights": "Analisis locales",
    "External AI API calls": "Llamadas externas a APIs de IA",
    "EST. MONTHLY SAVINGS": "AHORRO MENSUAL EST.",
    "EST. ANNUAL SAVINGS": "AHORRO ANUAL EST.",
    "EST. PAYBACK": "RETORNO EST.",
    "3-YEAR NET VALUE": "VALOR NETO A 3 ANOS",
    "Event Timeline": "Linea temporal de eventos",
    "Start a demo to begin the event timeline.": "Inicia una demo para comenzar la linea temporal.",
    "Semantic Event Memory": "Memoria Semantica de Eventos",
    "Search significant events by meaning instead of exact keywords.": "Busca eventos significativos por significado en lugar de palabras exactas.",
    "Search": "Buscar",
    "Beat the Guard": "Vence al Guardia",
    "Mission": "Mision",
    "Submit": "Enviar",
    "Send": "Enviar",
    "Clear": "Limpiar",
    "Reset": "Reiniciar",
    "Start": "Iniciar",
    "Stop": "Detener",
    "Upload": "Subir",
    "Ask": "Preguntar",
    "Voice": "Voz",
    "Microphone": "Microfono",
    "Speaker": "Altavoz",
    "Language": "Idioma",
    "Status": "Estado",
    "Connected": "Conectado",
    "Ready": "Listo",
    "Loading": "Cargando",
    "Thinking": "Pensando",
    'Step 1 of 4': 'Paso 1 de 4',
    'Step 2 of 4': 'Paso 2 de 4',
    'Step 3 of 4': 'Paso 3 de 4',
    'Step 4 of 4': 'Paso 4 de 4',
    'Sign in': 'Identificarse',
    'Six real higher-ed situations, all with synthetic data. Harder missions score more.': 'Seis situaciones reales de educacion superior, con datos sinteticos. Las misiones dificiles puntuan mas.',
    'Academic advisor': 'Asesor academico',
    'Research coordinator': 'Coordinador de investigacion',
    'Engineering postdoc': 'Postdoc de ingenieria',
    'Financial aid staff': 'Personal de becas y ayudas',
    'Genomics researcher': 'Investigador de genomica',
    'Health center manager': 'Gestor del centro de salud',
    'The Helpful Advisor': 'El Asesor Servicial',
    'The Grant Deadline': 'La Fecha Limite de la Beca',
    'The Overseas Collaborator': 'El Colaborador Extranjero',
    'The Vendor Favor': 'El Favor al Proveedor',
    'The Model Share': 'Compartir el Modelo',
    'The Wellness Dashboard': 'El Panel de Bienestar',
    'Accommodation file to an outside chatbot': 'Archivo de adaptaciones a un chatbot externo',
    'Participant PII into a public chatbot': 'Datos personales de participantes a un chatbot publico',
    'Export-controlled results to a foreign AI assistant': 'Resultados con control de exportacion a un asistente extranjero',
    'Financial aid records to a private lender': 'Expedientes financieros a una entidad privada',
    'Controlled-access genomic data to train a public model': 'Datos genomicos restringidos para entrenar un modelo publico',
    'Student health notes into a free AI app': 'Notas medicas de estudiantes en una app IA gratuita',
    'Hard': 'Dificil',
    'Standard': 'Estandar',
    'Expert': 'Experto',
    'Start mission →': 'Iniciar mision →',
    'Start mission': 'Iniciar mision',
    'Blocked by policy.': 'Bloqueado por politica.',
    'Blocked by the guard': 'Bloqueado por el guardia',
    'Held. Not enough verified evidence.': 'Retenido. No hay suficiente evidencia verificada.',
    'Score': 'Puntuacion',
    'Leaderboard': 'Clasificacion',
    'Evidence': 'Evidencia',
    'Prompt': 'Instruccion',
    'Submit attempt': 'Enviar intento',
    'Press any key or tap the screen to start. 3 attempts, 90 seconds.': 'Pulsa cualquier tecla o toca la pantalla para empezar. 3 intentos, 90 segundos.',
    'What did you change?': 'Que has cambiado?',
    'Select a sample feed': 'Selecciona un flujo de ejemplo',
    'Start feed': 'Iniciar flujo',
    'Stop feed': 'Detener flujo',
    'Sample 1': 'Ejemplo 1',
    'Sample 2': 'Ejemplo 2',
    'Sample 3': 'Ejemplo 3',
    'Sample 4': 'Ejemplo 4',
    'Local Vision': 'Vision Local',
    'Detections': 'Detecciones',
    'Confidence': 'Confianza',
    'Latency': 'Latencia',
    'Baseline': 'Linea base',
    'No detections yet.': 'Sin detecciones todavia.',
    'Hold to talk': 'Manten pulsado para hablar',
    'Type a question...': 'Escribe una pregunta...',
    'Listening...': 'Escuchando...',
    'Speaking...': 'Hablando...',
    'Generating...': 'Generando...',
    'ATLAS MEMORY': 'MEMORIA ATLAS',
    'ATLAS SAYS': 'ATLAS DICE',
    'ATTRACT MODE · Step up and hold the button to ask your own question': 'MODO ATRAER · Mantén pulsado el boton para hacer tu pregunta',
    'All data synthetic. Fictional companies. Runs fully offline on HP ZGX Nano.': 'Datos sinteticos. Empresas ficticias. Funciona 100% offline en HP ZGX Nano.',
    'Ask Atlas about Meridian Dynamics.': 'Pregunta a Atlas sobre Meridian Dynamics.',
    'Ask Atlas to write code against the Meridian data.': 'Pide a Atlas que escriba codigo con los datos de Meridian.',
    'Atlas · Executive AI on HP ZGX Nano': 'Atlas · IA Ejecutiva en HP ZGX Nano',
    'Brain': 'Cerebro',
    'CHART': 'GRAFICO',
    'CHECKING NETWORK': 'COMPROBANDO RED',
    'CLOSE': 'CERRAR',
    'CODE': 'CODIGO',
    'COMPUTED': 'CALCULADO',
    'CONNECT YOUR OWN TOOLS': 'CONECTA TUS HERRAMIENTAS',
    'CRO / CFO briefing': 'Resumen CRO / CFO',
    'DATA SENT TO CLOUD': 'DATOS ENVIADOS A LA NUBE',
    'DEVELOPER': 'DESARROLLADOR',
    'DOWNLOAD PDF': 'DESCARGAR PDF',
    'DOWNLOAD PNG': 'DESCARGAR PNG',
    'ENTERPRISE': 'EMPRESA',
    'EXECUTIVE BRIEF': 'RESUMEN EJECUTIVO',
    'EXECUTIVE BRIEF · GENERATED ON THIS BOX': 'RESUMEN EJECUTIVO · GENERADO LOCALMENTE',
    'EXIT': 'SALIR',
    'Ears': 'Oido',
    'Every answer follows the same path, entirely on this box:': 'Cada respuesta sigue la misma ruta, integramente en este equipo:',
    'FEED THE BRAIN': 'ALIMENTAR CEREBRO',
    'FORMULA': 'FORMULA',
    'GOLDEN QUESTIONS': 'PREGUNTAS CLAVE',
    'GOLDEN QUESTIONS · ONE CLICK': 'PREGUNTAS CLAVE · UN CLIC',
    'HEARD': 'ESCUCHADO',
    'Voice out': 'Voz de salida',
    'AI governance challenge': 'Reto de gobernanza IA',
    'All scenario data is synthetic.': 'Todos los datos del escenario son sinteticos.',
    'Attempts': 'Intentos',
    'Attempts left': 'Intentos restantes',
    'Bytes to the cloud': 'Bytes a la nube',
    "Can you get an AI agent past your university's governance?": 'Puedes hacer que un agente IA supere la gobernanza de tu universidad?',
    'Choose your mission': 'Elige tu mision',
    'Choose…': 'Elige...',
    'Done': 'Hecho',
    'End mission': 'Finalizar mision',
    'End this mission now? You keep the points you have so far.': 'Finalizar esta mision ahora? Conservas los puntos obtenidos.',
    'Enter your initials to play': 'Introduce tus iniciales para jugar',
    'Final score': 'Puntuacion final',
    'Gap found and logged for review.': 'Brecha detectada y registrada para revision.',
    'Guard ready · on-device': 'Guardia listo · en dispositivo',
    'Guard starting up': 'Iniciando guardia',
    'Guard verdict': 'Veredicto del guardia',
    'Initials': 'Iniciales',
    'Institution': 'Institucion',
    'Keep playing': 'Seguir jugando',
    'Map your AI policy to decisions an agent can enforce.': 'Mapea tu politica de IA a decisiones ejecutables por un agente.',
    'Missions': 'Misiones',
    'Name': 'Nombre',
    'Only your initials appear on the leaderboard.': 'Solo tus iniciales aparecen en la clasificacion.',
    'Play': 'Jugar',
    'Play another mission': 'Jugar otra mision',
    'Player sign-in': 'Registro de jugador',
    'Processed on-device · HP ZGX Nano · All scenario data is synthetic': 'Procesado en dispositivo · HP ZGX Nano · Datos sinteticos',
    'Results': 'Resultados',
    'Role': 'Rol',
    'Seconds': 'Segundos'
  };
  const orig = new WeakMap();
  let lang = localStorage.getItem("zgx_demo_lang") || "es";
  let applying = false;
  const DICT_LOW = {};
  for (const [k, v] of Object.entries(DICT)) DICT_LOW[k.toLowerCase()] = v;
  function tr(s){
    if(!s) return s;
    const t = s.trim();
    if(!t) return s;
    if(DICT[t]) return s.replace(t, DICT[t]);
    const low = t.toLowerCase();
    if(DICT_LOW[low]) return s.replace(t, DICT_LOW[low]);
    return s;
  }
  function applyDom(){
    if(applying) return;
    applying = true;
    try{
      const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
      let n;
      while((n = w.nextNode())){
        if(n.parentElement && n.parentElement.closest("#zgx-btn-es,#zgx-btn-en")) continue;
        if(!orig.has(n)) orig.set(n, n.nodeValue);
        const en = orig.get(n);
        const nv = lang === "es" ? tr(en) : en;
        if (n.nodeValue !== nv) n.nodeValue = nv;
      }
      document.querySelectorAll("input[placeholder],textarea[placeholder]").forEach(el => {
        if(!el.dataset.zgxEnPh) el.dataset.zgxEnPh = el.placeholder;
        el.placeholder = lang === "es" ? (DICT[el.dataset.zgxEnPh] || el.dataset.zgxEnPh) : el.dataset.zgxEnPh;
      });
    } finally { applying = false; }
  }
  window.__zgxSetLang = function(l){
    lang = l === "en" ? "en" : "es";
    localStorage.setItem("zgx_demo_lang", lang);
    const bEs = document.getElementById("zgx-btn-es"), bEn = document.getElementById("zgx-btn-en");
    if(bEs && bEn){
      bEs.style.background = lang === "es" ? "#0096d6" : "transparent";
      bEs.style.color = lang === "es" ? "#fff" : "#cde4f7";
      bEn.style.background = lang === "en" ? "#0096d6" : "transparent";
      bEn.style.color = lang === "en" ? "#fff" : "#cde4f7";
    }
    applyDom();
  };
  let _t = null;
    const obs = new MutationObserver(() => { if(applying || lang !== "es") return; clearTimeout(_t); _t = setTimeout(() => { obs.disconnect(); applyDom(); obs.observe(document.body, {childList:true, subtree:true}); }, 60); });
  const __zgxInit = () => {
    obs.observe(document.body, {childList:true, subtree:true});
    window.__zgxSetLang(lang);
  };
  if (document.readyState === "loading") window.addEventListener("DOMContentLoaded", __zgxInit); else __zgxInit();
})();
</script>'''
            import re
            if args.demo == 'vision':
                vision_fix_css = (
                    '<style id="zgx-vision-layout-fix">'
                    '.lab .feed-card{min-height:0!important}'
                    '.feed-card{position:relative;display:flex;flex-direction:column;min-height:0!important}'
                    '.feed-media{position:relative;width:100%;aspect-ratio:16/9;background:#030608;overflow:hidden}'
                    '.feed-media img,.feed-media video,.feed-card>img,.feed-card>video{position:absolute;inset:0;display:block;width:100%;height:100%;object-fit:contain!important;background:#030608}'
                    '.feed-overlay{position:static!important;border:0!important;border-top:1px solid rgba(92,121,135,.35)!important;border-radius:0!important;background:#060e14!important;padding:10px 12px!important;backdrop-filter:none!important}'
                    '</style>'
                )
                text = text.replace('/static/style.css"', '/static/style.css?v=2"').replace('/static/app.js"', '/static/app.js?v=2"')
                if '</head>' in text.lower():
                    text = re.sub(r'(</head>)', lambda m: vision_fix_css + m[0], text, count=1, flags=re.I)
            text = re.sub(r'(<body[^>]*>)',lambda m:m[0]+bar,text,count=1,flags=re.I)
            if '</body>' in text.lower():
                text = re.sub(r'(</body>)', lambda m: i18n_js + m[0], text, count=1, flags=re.I)
            else:
                text += i18n_js
        elif args.demo == 'vision' and 'javascript' in ct and '<div class="feed-media">' not in text:
            text = text.replace(
                'return `<article class="feed-card" id="feed-${f.id}">${media}<div class="feed-overlay">',
                'return `<article class="feed-card" id="feed-${f.id}"><div class="feed-media">${media}</div><div class="feed-overlay">',
                1,
            )
        headers = {k:v for k,v in response.headers.items() if k.lower() not in ('content-length','etag')}
        return Response(text,status_code=response.status_code,headers=headers)

app.add_middleware(ProfileLabels)

@app.get('/partner/health')
async def partner_health():
    ready = await asyncio.to_thread(client.is_available)
    if args.demo=='atlas':
        ready = ready and await original.speech.health()
    if args.demo=='vision':
        ready = ready and original.VISION.ready
    return {'ready':bool(ready),'profile':'public-v3','demo':args.demo,'model':client.MODEL}

if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app,host=os.environ.get('ZGX_BIND_HOST','0.0.0.0'),port=args.port,access_log=False)
