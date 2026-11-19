"""Local llama.cpp API adapters; no cloud inference or silent canned answers."""
import json
import os
import time
import urllib.request

GEN = os.environ.get('ZGX_GENERATION_URL', 'http://127.0.0.1:8081')
EMBED = os.environ.get('ZGX_EMBEDDING_URL', 'http://127.0.0.1:8082')
MODEL = 'Qwen3.6-35B-A3B public partner profile'
TIMEOUT_S = 180

def request(base, path, payload=None, timeout=180):
    req = urllib.request.Request(base + path,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)

def is_available():
    try:
        return request(GEN, '/health', timeout=3).get('status') == 'ok'
    except (OSError, ValueError):
        return False

def model_loaded(*args, **kwargs):
    return is_available()

def running_models():
    return [{'name': MODEL, 'model': MODEL}] if is_available() else []

def chat(messages, max_tokens=700, schema=None, timeout=180):
    body = {'messages': messages, 'temperature': 0, 'max_tokens': max_tokens,
            'stream': False, 'chat_template_kwargs': {'enable_thinking': False}}
    if schema:
        body['response_format'] = {'type':'json_schema', 'json_schema':
            {'name':'policy_decision', 'strict':True, 'schema':schema}}
    out = request(GEN, '/v1/chat/completions', body, timeout)
    text = out['choices'][0]['message'].get('content')
    if not text:
        raise RuntimeError('Local model returned no answer. Read the generation log.')
    return text

def generate(prompt, system='', model=None, timeout=300, keep_alive=None):
    messages = ([{'role':'system','content':system}] if system else [])
    return chat(messages + [{'role':'user','content':prompt}], timeout=timeout)

def embed(texts, model=None, timeout=90):
    if not texts:
        return []
    # Bound batches to the embedding server's 4096-token context. One passage
    # per request also avoids reshaping ambiguities between llama.cpp versions.
    vectors = []
    for text in texts:
        result = request(EMBED, '/v1/embeddings', {'input':[text]}, timeout)
        rows = sorted(result['data'], key=lambda row: row['index'])
        if len(rows) != 1 or not rows[0].get('embedding'):
            raise RuntimeError('Embedding server returned an invalid vector.')
        vectors.append(rows[0]['embedding'])
    return vectors

class GuardError(Exception):
    pass

LABELS = ['YES', 'NO', 'CONDITIONAL', 'INSUFFICIENT']
SCHEMA = {'type':'object', 'additionalProperties':False,
    'required':['decision','citations','conditions','rationale'], 'properties': {
    'decision':{'type':'string','enum':LABELS},
    'citations':{'type':'array','items':{'type':'string'}},
    'conditions':{'type':'array','items':{'type':'string'}},
    'rationale':{'type':'string'}}}

def strict_parse(text):
    try:
        obj = json.loads(text)
        if not isinstance(obj, dict) or set(obj) != set(SCHEMA['required']):
            return None
        if obj['decision'] not in LABELS or not isinstance(obj['rationale'], str):
            return None
        if any(not isinstance(obj[k], list) or not all(isinstance(v,str) for v in obj[k])
               for k in ('citations','conditions')):
            return None
        return obj
    except (ValueError, TypeError):
        return None

def stream(question, evidence_records):
    from server.sentinel_prompt import build_messages
    start = time.perf_counter()
    messages = build_messages(question, evidence_records)
    messages[0]['content'] = messages[0]['content'].replace(
        'You are Sentinel, a policy-governance decision model.',
        'You are a public-model policy demonstration assistant.')
    try:
        raw = chat(messages, max_tokens=1100, schema=SCHEMA)
        parsed = strict_parse(raw)
        if parsed is None:
            raise GuardError('Model output failed the required decision schema.')
    except (OSError, ValueError, KeyError, RuntimeError) as exc:
        raise GuardError(str(exc)) from exc
    ms = round((time.perf_counter() - start) * 1000)
    yield ('verdict', parsed['decision'], ms)
    yield ('final', parsed['decision'], parsed, raw, ms)

def health():
    return {'ok':is_available(), 'models':[MODEL], 'profile':'public-qwen',
            'trained_sentinel':False}
