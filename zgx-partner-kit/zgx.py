#!/usr/bin/env python3
"""Partner bootstrap and foreground process supervisor. Standard library only.

Model downloads occur only in explicit setup commands. Launch never downloads.
No system NVIDIA packages, systemd units, or unrelated processes are changed.
"""
import argparse
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import platform
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.request
import webbrowser

KIT = Path(__file__).resolve().parent
ROOT = Path(os.environ.get('ZGX_DEMOS_ROOT', str(Path.home() / 'zgx-demos'))).expanduser().resolve()
LLAMA_PIN = 'ad6c66839af3c5646fba8c6c2e2087a1e4e38948'
SPECS = {
    'campus': ('campuscompass-local', '12e80e9d17a372e1795ebeec376d1cab3fcb7ada', '3.12', 18000, '/', 'CampusCompass live AI'),
    'atlas': ('atlas-claude', '91a78d138d5c4f88848a7c085fdf6234f8579861', '3.13', 18110, '/', 'Atlas analytics review'),
    'guard': ('beat-the-guard', 'd4c0d0fad05fcbd5f658f39a6c30d53623150485', '3.13', 18080, '/play', 'Beat the Guard live AI'),
    'vision': ('research-observatory', '30da75309dc2780becf2532006775ef382d8e17d', '3.13', 18120, '/', 'Research Observatory live AI'),
}

def basekey(key):
    return 'vision' if key == 'vision-mock' else key

def repo(key):
    return ROOT / SPECS[basekey(key)][0]

def run(args, cwd=None, env=None):
    print('+ ' + shlex.join([str(x) for x in args]), flush=True)
    subprocess.run([str(x) for x in args], cwd=cwd, env=env, check=True)

def uv():
    p = shutil.which('uv') or str(Path.home() / '.local/bin/uv')
    if not Path(p).is_file():
        raise RuntimeError('uv missing. Run bash install.sh on the Nano first.')
    return p

def py(key):
    return repo(key) / '.venv/bin/python'

def sha(path):
    with Path(path).open('rb') as f:
        h = hashlib.sha256()
        for chunk in iter(lambda: f.read(8 * 1024 * 1024), b''):
            h.update(chunk)
        return h.hexdigest()

def disk(min_gib):
    ROOT.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(ROOT).free < min_gib * 1024**3:
        raise RuntimeError(f'At least {min_gib} GiB free space is required for this setup.')

def json_url(url, timeout=5):
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.load(response)

def free_ports(ports):
    for port in ports:
        with socket.socket() as s:
            try:
                s.bind(('127.0.0.1', port))
            except OSError:
                raise RuntimeError(f'Port {port} is in use. Stop your previous demo before launching. No process was killed.')

def checkout(url, path, pin):
    if not path.exists():
        run(['git', 'clone', '--no-checkout', '--depth', '1', url, path])
        run(['git', 'fetch', '--depth', '1', 'origin', pin], cwd=path)
        run(['git', 'checkout', '--detach', pin], cwd=path)
    else:
        head = subprocess.check_output(['git', '-C', str(path), 'rev-parse', 'HEAD'], text=True).strip()
        if head != pin:
            raise RuntimeError(f'{path} exists at a different commit. Choose a separate ZGX_DEMOS_ROOT. Your files were not changed.')
        if subprocess.run(['git','-C',str(path),'diff','--quiet','HEAD','--']).returncode:
            raise RuntimeError(f'{path} has tracked edits. Preserve those and choose a separate ZGX_DEMOS_ROOT. Your files were not changed.')
    return path

def node_env():
    # Keep Node scoped to the demo root. Verify the official release checksum.
    version = 'v22.16.0'
    name = f'node-{version}-linux-arm64'
    folder = ROOT / '.tools' / name
    if not (folder / 'bin/node').exists():
        folder.parent.mkdir(parents=True, exist_ok=True)
        archive = folder.parent / (name + '.tar.xz')
        base = f'https://nodejs.org/dist/{version}/'
        with urllib.request.urlopen(base + 'SHASUMS256.txt', timeout=30) as r:
            entries = r.read().decode().splitlines()
        expected = next(line.split()[0] for line in entries if line.split()[-1] == archive.name)
        if not archive.exists() or sha(archive) != expected:
            urllib.request.urlretrieve(base + archive.name, archive)
        if sha(archive) != expected:
            raise RuntimeError('Node archive checksum mismatch.')
        with tarfile.open(archive) as tar:
            # Python 3.12+ data filter excludes escaping symlinks and special files.
            if not hasattr(tarfile, 'data_filter'):
                raise RuntimeError('Node extraction needs Python 3.12+. Ubuntu 24.04 is the target.')
            tar.extractall(folder.parent, filter='data')
        archive.unlink()
    env = os.environ.copy()
    env['PATH'] = str(folder / 'bin') + os.pathsep + env.get('PATH', '')
    return env

def common_setup(key):
    key = basekey(key)
    name, pin, version, *_ = SPECS[key]
    p = checkout(f'https://github.com/Navigatedhustle/{name}.git', ROOT / name, pin)
    if not py(key).exists():
        run([uv(), 'venv', '--python', version, p / '.venv'])
    for folder in ('data', 'logs', 'runtime', 'run', 'var', 'artifacts', 'out'):
        (p / folder).mkdir(exist_ok=True)
    return p

def manifest_models(p):
    return json.loads((p / 'docs/MODEL-MANIFEST.json').read_text())

def verify_campus_models(p):
    for role in ('generation', 'embedding', 'reranker'):
        model = manifest_models(p)[role]
        f = p / model['file']
        if not f.is_file():
            raise RuntimeError(f'Missing {role}: {f}. Run CampusCompass setup.')
        if sha(f) != model['sha256']:
            raise RuntimeError(f'{role} checksum mismatch. Launch stopped.')

def campus_setup(p):
    disk(45)
    if not shutil.which('nvcc'):
        raise RuntimeError('CUDA toolkit nvcc missing. Use the supported Nano image/toolkit before building. This kit will not replace your GPU stack.')
    run([uv(), 'pip', 'install', '--python', py('campus'), '-r', p / 'requirements.lock'])
    env = node_env()
    run(['npm', 'ci', '--no-audit', '--no-fund'], cwd=p / 'frontend', env=env)
    run(['npm', 'run', 'build'], cwd=p / 'frontend', env=env)
    llama = checkout('https://github.com/ggml-org/llama.cpp.git', p / 'runtime/llama.cpp', LLAMA_PIN)
    run([uv(), 'pip', 'install', '--python', py('campus'), llama / 'gguf-py'])
    # Uses the repo's pinned repo IDs, revisions, filenames and original hashes.
    run([py('campus'), 'scripts/setup-models.py'], cwd=p)
    expected = manifest_models(p)['reranker']['sha256']
    dest = p / manifest_models(p)['reranker']['file']
    if not dest.exists() or sha(dest) != expected:
        # Existing script updates the manifest. Keep the publication's trusted
        # expected hash authoritative; restore it even if conversion fails.
        original = (p / 'docs/MODEL-MANIFEST.json').read_bytes()
        try:
            run([py('campus'), 'scripts/repair-reranker-gguf.py'], cwd=p)
            if sha(dest) != expected:
                raise RuntimeError('Converted reranker differs from the published hash. Launch blocked.')
        finally:
            (p / 'docs/MODEL-MANIFEST.json').write_bytes(original)
    verify_campus_models(p)
    run(['cmake', '-S', llama, '-B', llama / 'build', '-DGGML_NATIVE=ON', '-DGGML_CUDA=ON',
         '-DCMAKE_CUDA_ARCHITECTURES=121a-real', '-DCMAKE_BUILD_TYPE=Release',
         '-DLLAMA_BUILD_TESTS=OFF', '-DLLAMA_BUILD_UI=OFF', '-DLLAMA_USE_PREBUILT_UI=OFF'])
    run(['cmake', '--build', llama / 'build', '--config', 'Release', '--target', 'llama-server', '-j', '4'])

def install_media(p):
    if not all((KIT / 'assets' / f'synthetic-lab-{i}.mp4').is_file() for i in range(1,5)):
        run([sys.executable, KIT / 'generate_samples.py'])
    target = p / 'media'
    target.mkdir(exist_ok=True)
    for f in (KIT / 'assets').glob('synthetic-*.mp4'):
        dest = target / f.name
        if dest.exists() and sha(dest) != sha(f):
            raise RuntimeError(f'Refusing to overwrite different media at {dest}')
        shutil.copy2(f, dest)
    # Preserve the original tracked manifest; use an adapter at launch time.

def guard_assets(p):
    folder = p / 'models/sentinel-v6-ckpt512'
    required = [folder / 'adapter_config.json', folder / 'chat_template.jinja']
    missing = [str(f) for f in required if not f.is_file() or not f.stat().st_size]
    if not list(folder.glob('*.safetensors')):
        missing.append(str(folder / 'adapter_model.safetensors'))
    if missing:
        raise RuntimeError('Owner supplied Sentinel files required:\n' + '\n'.join(missing) +
                           '\nCopy the complete original checkpoint folder, not a replacement model.')
    config = json.loads((folder / 'adapter_config.json').read_text())
    if config.get('r', 0) > 8:
        raise RuntimeError('Adapter rank exceeds the recorded Sentinel V6 serving profile.')
    cache = Path.home() / '.cache/huggingface/hub/models--meta-llama--Llama-3.1-8B-Instruct/snapshots'
    candidates = [s for s in cache.glob('*') if (s / 'config.json').exists() and list(s.glob('*.safetensors'))]
    if not candidates:
        raise RuntimeError('Llama 3.1 8B Instruct is not cached. Accept the Meta model terms in your own account, then run:\n'
                           'uvx --from huggingface_hub hf download meta-llama/Llama-3.1-8B-Instruct\n'
                           'Use your account login if prompted; do not send anyone your token.')

def setup(key):
    if platform.machine() != 'aarch64':
        raise RuntimeError('Setup targets ARM64 Nano. This machine is not a Nano. No setup performed.')
    disk(65 if key == 'vision' else 5)
    p = common_setup(key)
    if key == 'campus':
        campus_setup(p)
    elif key == 'atlas':
        run([uv(), 'pip', 'install', '--python', py(key), '-r', p / 'requirements.lock'])
        run([uv(), 'pip', 'install', '--python', py(key), 'httpx[socks]==0.28.1', 'socksio==1.0.0'])
        if not (p / 'data/atlas.db').exists():
            run([py(key), '-m', 'atlas.data.generate', '--seed', '42'], cwd=p)
    elif key.startswith('vision'):
        # GPU bindings are imported lazily. Mock walkthrough needs no PyCUDA.
        requirements = KIT / ('requirements-vision-live.txt' if key == 'vision' else 'requirements-vision-review.lock')
        args = [uv(), 'pip', 'install', '--python', py(key), '-r', requirements]
        run(args)
        install_media(p)
        if key == 'vision':
            vision_assets(p)
            if not shutil.which('ollama'):
                raise RuntimeError('Install and start Ollama from https://docs.ollama.com/linux then rerun setup. No system service is silently installed.')
            json_url('http://127.0.0.1:11434/api/tags')
            run(['ollama', 'pull', 'llama3.1:70b'])
            run(['ollama', 'pull', 'nomic-embed-text'])
    elif key == 'guard':
        run([uv(), 'pip', 'install', '--python', py(key), '-r', p / 'requirements.lock'])
        guard_assets(p)
        run(['docker', 'pull', 'nvcr.io/nvidia/vllm:25.11-py3'])
    write_shortcut(key)
    print('\nSetup completed. Launch shortcut created. Live inference still needs your Nano rehearsal.\n')

def vision_assets(p):
    engine = Path(os.environ.get('ZGX_ENGINE', str(p / 'models/yolov8s.trt')))
    if not engine.is_file():
        raise RuntimeError(f'Matching raw TensorRT engine missing: {engine}\n'
                           'See build-vision-engine.py. Export on this Nano with its installed NVIDIA stack.')
    # No fake readiness from file presence: validate via the actual detector.
    env = os.environ.copy()
    env['PYTHONPATH'] = str(p)
    run([py('vision'), '-c', 'import sys; from yolo_trt import TRTDetector; d=TRTDetector(sys.argv[1]); print(d.info()); assert d.ready, d.error', engine], cwd=p, env=env)
    return engine

def write_shortcut(key):
    dest = Path.home() / '.local/share/applications'
    dest.mkdir(parents=True, exist_ok=True)
    title = 'Research Observatory MOCK walkthrough' if key == 'vision-mock' else SPECS[key][5]
    # Desktop field codes interpret %, so escape any literal percent in paths.
    def quoted(value):
        return '"' + str(value).replace('\\', '\\\\').replace('"', '\\"').replace('`', '\\`').replace('$', '\\$').replace('%', '%%') + '"'
    content = ('[Desktop Entry]\nType=Application\nVersion=1.0\nName=ZGX ' + title + '\n'
               'Exec=gnome-terminal -- ' + quoted(sys.executable) + ' ' + quoted(KIT / 'zgx.py') + ' launch ' + key + '\n'
               'Icon=applications-science\nTerminal=false\nCategories=Education;Science;\n')
    target = dest / f'zgx-partner-{key}.desktop'
    target.write_text(content)
    target.chmod(0o755)
    try:
        desktop = Path(subprocess.check_output(['xdg-user-dir', 'DESKTOP'], text=True).strip())
        if desktop == Path.home():
            raise ValueError('No desktop directory configured')
        desktop.mkdir(exist_ok=True)
        shutil.copy2(target, desktop / target.name)
    except (OSError, subprocess.CalledProcessError, ValueError):
        print('Applications menu shortcut created; no desktop folder detected.')

def wait_ready(url, process, timeout, log, predicate=lambda value: True):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f'Service exited with code {process.returncode}. Read {log}')
        try:
            if predicate(json_url(url, timeout=3)):
                return
        except (OSError, ValueError, urllib.error.URLError):
            pass
        time.sleep(1)
    raise RuntimeError(f'Readiness timed out. Read {log}')

def cleanup(processes):
    # Only subprocess groups created and retained by this launcher are signalled.
    for process in reversed(processes):
        if process.poll() is None:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGTERM)
    deadline = time.monotonic() + 12
    for process in reversed(processes):
        try:
            process.wait(timeout=max(0.1, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            process.wait()

def launch(key, open_browser=True):
    p = repo(key)
    if not py(key).exists():
        raise RuntimeError('Demo environment missing. Run install.sh first.')
    ROOT.mkdir(parents=True, exist_ok=True)
    lock = (ROOT / '.partner-launch.lock').open('w')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise RuntimeError('Another partner demo is running. Stop it in its terminal first.')
    name, _, _, port, route, _ = SPECS[basekey(key)]
    ports = [port] + ([8081, 8082, 8083] if key == 'campus' else [8001] if key == 'guard' else [])
    free_ports(ports)
    if key == 'campus':
        verify_campus_models(p)
        if not (p / 'runtime/llama.cpp/build/bin/llama-server').is_file():
            raise RuntimeError('llama-server missing. Rerun CampusCompass setup.')
        if not (p / 'frontend/dist/index.html').exists():
            raise RuntimeError('CampusCompass frontend missing. Rerun setup.')
    if key == 'guard':
        guard_assets(p)
    if key == 'vision':
        engine = vision_assets(p)
        tags = json_url('http://127.0.0.1:11434/api/tags')['models']
        names = {m.get('name', '') for m in tags}
        if not {'llama3.1:70b', 'nomic-embed-text:latest'} <= names:
            raise RuntimeError('Required Ollama models missing. Rerun vision setup.')
    processes, handles = [], []
    logs = ROOT / 'partner-logs' / time.strftime('%Y%m%d-%H%M%S')
    logs.mkdir(parents=True, exist_ok=True)
    container_name = None
    env = os.environ.copy()
    env['PYTHONPATH'] = str(p)
    if key == 'atlas':
        env['ATLAS_DEV'] = '0'
        env['ATLAS_FINETUNE'] = '0'
    # Local model clients must reach this machine even behind a corporate proxy.
    for variable in ('NO_PROXY', 'no_proxy'):
        env[variable] = ','.join(filter(None, [env.get(variable,''), '127.0.0.1', 'localhost']))
    def start(command, label, extra=None):
        path = logs / (label + '.log')
        handle = path.open('w')
        handles.append(handle)
        proc = subprocess.Popen([str(v) for v in command], cwd=p, env={**env, **(extra or {})},
                                stdout=handle, stderr=subprocess.STDOUT, start_new_session=True)
        processes.append(proc)
        print(f'Starting {label}. Log: {path}', flush=True)
        return proc, path
    try:
        if key == 'vision':
            print('Warming local 70B reasoning and embedding models. This may take several minutes.', flush=True)
            for endpoint, payload in [('/generate', {'model':'llama3.1:70b','prompt':'Reply READY',
                    'stream':False,'keep_alive':'30m','options':{'num_predict':8}}),
                    ('/embed', {'model':'nomic-embed-text','input':['Partner launch readiness check']})]:
                request = urllib.request.Request('http://127.0.0.1:11434/api'+endpoint,
                    data=json.dumps(payload).encode(),headers={'Content-Type':'application/json'})
                with urllib.request.urlopen(request,timeout=600) as response:
                    result=json.load(response)
                if endpoint == '/embed' and not result.get('embeddings'):
                    raise RuntimeError('Embedding warmup returned no vectors.')
                if endpoint == '/generate' and not result.get('response'):
                    raise RuntimeError('Reasoning warmup returned no response.')
        if key == 'campus':
            for script, modelport in [('model-start.sh',8081),('embedding-start.sh',8082),('reranker-start.sh',8083)]:
                proc, log = start(['bash', 'scripts/' + script], script)
                wait_ready(f'http://127.0.0.1:{modelport}/health', proc, 600, log, lambda j: j.get('status') == 'ok')
        if key == 'guard':
            container_name = f'zgx-partner-sentinel-{os.getpid()}'
            cache = Path.home() / '.cache/huggingface'
            adapter = p / 'models/sentinel-v6-ckpt512'
            proc, log = start(['docker', 'run', '--name', container_name, '--rm', '--init', '--gpus', 'all', '--ipc=host',
                '-p', '127.0.0.1:8001:8000', '-e', 'HF_HUB_OFFLINE=1', '-e', 'TRANSFORMERS_OFFLINE=1',
                '-e', 'VLLM_NO_USAGE_STATS=1', '-e', 'DO_NOT_TRACK=1',
                '-v', str(cache)+':/root/.cache/huggingface:ro', '-v', str(adapter)+':/models/sentinel-v6:ro',
                'nvcr.io/nvidia/vllm:25.11-py3', 'vllm', 'serve', 'meta-llama/Llama-3.1-8B-Instruct',
                '--host','0.0.0.0','--port','8000','--dtype','bfloat16','--safetensors-load-strategy','eager',
                '--enforce-eager','--enable-lora','--max-lora-rank','8','--max-loras','1',
                '--lora-modules','sentinel-v6=/models/sentinel-v6','--max-model-len','2048',
                '--max-num-seqs','8','--gpu-memory-utilization','0.30','--enable-prefix-caching'], 'sentinel')
            wait_ready('http://127.0.0.1:8001/v1/models', proc, 600, log,
                       lambda j: 'sentinel-v6' in {x['id'] for x in j.get('data',[])})
        if key.startswith('vision'):
            extra = {'ZGX_MOCK': '1' if key == 'vision-mock' else '0'}
            if key == 'vision':
                extra['ZGX_ENGINE'] = str(engine)
            else:
                # Block reasoning in walkthrough mode via adapter; do not call
                # whatever real Ollama service happens to be on the machine.
                extra['ZGX_PARTNER_MOCK'] = '1'
            proc, log = start([py(key), KIT / 'vision_review.py', '--port', str(port)], 'app', extra)
            health = '/api/status'
        else:
            proc, log = start(['bash', 'scripts/run-local.sh'], 'app', {'PROJECT_PORT': str(port)})
            health = '/api/config' if key == 'atlas' else '/api/health'
        predicate = (lambda j: j.get('vision',{}).get('ready') is True) if key == 'vision' else (lambda j: True)
        wait_ready(f'http://127.0.0.1:{port}{health}', proc, 90, log, predicate)
        if key == 'campus':
            ready = json_url(f'http://127.0.0.1:{port}/api/health')
            if not all(ready.get('models',{}).get(k) for k in ('generation','embedding','reranker')):
                raise RuntimeError('App opened but not every CampusCompass model is ready.')
        url = f'http://127.0.0.1:{port}{route}'
        print('\nREADY: ' + url, flush=True)
        if key == 'atlas':
            print('ANALYTICS REVIEW ONLY. Voice, model-generated answers and optional AI services are not provisioned.')
        if key == 'vision-mock':
            print('MOCK walkthrough with synthetic footage. No real detector or model inference.')
        if open_browser:
            webbrowser.open(url)
        print('Keep this terminal open. Press Ctrl+C to stop this demo and its launched services.', flush=True)
        while True:
            time.sleep(1)
            for child in processes:
                if child.poll() is not None:
                    raise RuntimeError(f'A launched service exited. Read {logs}')
    finally:
        if container_name:
            # Unique name belongs to this invocation; leave other containers alone.
            with contextlib.suppress(OSError, subprocess.TimeoutExpired):
                subprocess.run(['docker','stop','--time','10',container_name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20)
        cleanup(processes)
        for handle in handles:
            handle.close()
        lock.close()

def main():
    def interrupted(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGINT, interrupted)
    signal.signal(signal.SIGTERM, interrupted)
    parser = argparse.ArgumentParser(description='HP ZGX Nano partner demo kit')
    parser.add_argument('action', choices=['setup','launch','shortcut'])
    parser.add_argument('demo', choices=[*SPECS, 'vision-mock'])
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args()
    try:
        if args.action == 'setup':
            setup(args.demo)
        elif args.action == 'shortcut':
            write_shortcut(args.demo)
        else:
            launch(args.demo, not args.no_browser)
    except KeyboardInterrupt:
        print('\nDemo stopped.')
    except Exception as exc:
        print('\nACTION REQUIRED: ' + str(exc), file=sys.stderr)
        if sys.stdin.isatty():
            input('Press Enter to close. ')
        sys.exit(1)

if __name__ == '__main__':
    main()
