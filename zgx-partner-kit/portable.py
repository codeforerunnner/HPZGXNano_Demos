#!/usr/bin/env python3
"""Public-model Nano setup and one-click launch. Original profiles remain in zgx.py."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import platform
import shutil
import signal
import subprocess
import sys
import time
import urllib.request
import webbrowser
import zgx

ROOT, KIT = zgx.ROOT, zgx.KIT
WHISPER_PIN = 'e4acdfdf214836d6ce5c7f8ac6b43b9912f361a1'
TITLES = {'campus':'CampusCompass','atlas':'Atlas voice analytics',
          'guard':'Beat the Guard - Public Qwen','vision':'Research Observatory - Public Qwen'}

def assets():
    return json.loads((KIT/'public-assets.json').read_text())

def download_asset(item):
    folder=ROOT/'public-models';folder.mkdir(parents=True,exist_ok=True)
    dest=folder/item['file']
    if dest.exists() and zgx.sha(dest)==item['sha256']:
        return
    partial=dest.with_suffix(dest.suffix+'.partial')
    print('Downloading verified public asset: '+item['file'],flush=True)
    # Interrupted downloads can resume; servers that ignore Range restart safely.
    offset=partial.stat().st_size if partial.exists() else 0
    req=urllib.request.Request(item['url'],headers={'Range':f'bytes={offset}-'} if offset else {})
    with urllib.request.urlopen(req,timeout=90) as response:
        append=offset>0 and response.status==206
        if append and not response.headers.get('Content-Range','').startswith(f'bytes {offset}-'):
            raise RuntimeError('Unexpected server range; partial download retained for inspection.')
        with partial.open('ab' if append else 'wb') as output:
            shutil.copyfileobj(response,output,8*1024*1024)
    if partial.stat().st_size!=item['bytes'] or zgx.sha(partial)!=item['sha256']:
        partial.unlink()
        raise RuntimeError('Asset checksum failed: '+item['file']+'. Retry setup.')
    partial.replace(dest)

def service_py():
    return ROOT/'.public-services/bin/python'

def write_shortcut(key):
    def quote(value):
        return '"'+str(value).replace('\\','\\\\').replace('"','\\"').replace('`','\\`').replace('$','\\$').replace('%','%%')+'"'
    folder=Path.home()/'.local/share/applications';folder.mkdir(parents=True,exist_ok=True)
    command=' '.join(quote(v) for v in [sys.executable,KIT/'portable.py','launch',key])
    content=('[Desktop Entry]\nType=Application\nVersion=1.0\n'
        'Name=ZGX '+TITLES[key]+'\nComment=Launch local public-model partner demo\n'
        'Exec=gnome-terminal -- '+command+'\nIcon=applications-science\nTerminal=false\nCategories=Education;Science;\n')
    target=folder/f'zgx-public-{key}.desktop';target.write_text(content);target.chmod(0o755)
    try:
        desktop=Path(subprocess.check_output(['xdg-user-dir','DESKTOP'],text=True).strip())
        if desktop!=Path.home():
            desktop.mkdir(exist_ok=True);shutil.copy2(target,desktop/target.name)
    except (OSError,subprocess.CalledProcessError):
        pass

def setup(key):
    if platform.system()!='Linux' or platform.machine()!='aarch64':
        raise RuntimeError('Full setup targets Ubuntu ARM64 on ZGX Nano. No setup performed.')
    if not shutil.which('nvcc'):
        raise RuntimeError('CUDA nvcc is missing. Restore the supported Nano software image/toolkit first.')
    selected=list(TITLES) if key=='all' else [key]
    ROOT.mkdir(parents=True,exist_ok=True)
    # Shared models/runtime are provisioned once, including Campus UI as a useful
    # first acceptance test. A receipt is written only after setup has succeeded.
    receipt=ROOT/'.public-common-ready.json'
    if not receipt.exists():
        zgx.disk(60)
        p=zgx.common_setup('campus')
        zgx.campus_setup(p)
        receipt.write_text(json.dumps({'llama_pin':zgx.LLAMA_PIN,'profile':'v3'})+'\n')
    else:
        zgx.verify_campus_models(zgx.repo('campus'))
    if any(k in selected for k in ('atlas','vision')):
        if not service_py().exists():
            zgx.run([zgx.uv(),'venv','--python','3.12',ROOT/'.public-services'])
        zgx.run([zgx.uv(),'pip','install','--python',service_py(),'-r',KIT/'requirements-public-services.lock'])
        roles={'vision','media'} | ({'speech'} if 'atlas' in selected else set())
        for item in assets():
            if item['role'] in roles:
                download_asset(item)
    if 'atlas' in selected:
        whisper=zgx.checkout('https://github.com/ggml-org/whisper.cpp.git',ROOT/'.tools/whisper.cpp',WHISPER_PIN)
        zgx.run(['cmake','-S',whisper,'-B',whisper/'build','-DGGML_CUDA=OFF',
            '-DWHISPER_BUILD_TESTS=OFF','-DCMAKE_BUILD_TYPE=Release'])
        zgx.run(['cmake','--build',whisper/'build','--target','whisper-cli','-j','4'])
    for demo in selected:
        p=zgx.common_setup(demo)
        if demo in ('atlas','guard'):
            zgx.run([zgx.uv(),'pip','install','--python',zgx.py(demo),'-r',p/'requirements.lock'])
            if demo=='atlas':
                zgx.run([zgx.uv(),'pip','install','--python',zgx.py(demo),'httpx[socks]==0.28.1','socksio==1.0.0'])
                if not (p/'data/atlas.db').exists():
                    zgx.run([zgx.py(demo),'-m','atlas.data.generate','--seed','42'],cwd=p)
        elif demo=='vision':
            zgx.run([zgx.uv(),'pip','install','--python',zgx.py(demo),'-r',KIT/'requirements-vision-review.lock'])
            zgx.run([sys.executable,KIT/'generate_public_media.py'])
        write_shortcut(demo)
        (ROOT/f'.public-{demo}-ready.json').write_text(json.dumps({'profile':'v3','source':zgx.SPECS[demo][1]})+'\n')
    print('\nSETUP COMPLETE. Open Applications and search ZGX. Run one demo at a time.\n'
          'Launch checks all required services before opening your browser.\n'
          'Rehearse on this Nano before presenting to a partner.')

def verify(key):
    print('Verifying installed models and assets. The 35B checksum can take a minute...',flush=True)
    if not (ROOT/f'.public-{key}-ready.json').is_file():
        raise RuntimeError('Setup receipt missing. Run: bash install.sh '+key)
    zgx.verify_campus_models(zgx.repo('campus'))
    for item in assets():
        need=(key in ('atlas','vision') and item['role']=='vision') or (key=='atlas' and item['role']=='speech')
        if need:
            p=ROOT/'public-models'/item['file']
            if not p.is_file() or zgx.sha(p)!=item['sha256']:
                raise RuntimeError('Missing or changed asset: '+item['file']+'. Rerun setup.')
    if key=='vision':
        manifest=ROOT/'public-media/manifest.json'
        if not manifest.is_file() or not all((zgx.repo('vision')/'media'/x['file']).is_file() for x in json.loads(manifest.read_text())):
            raise RuntimeError('Sample clips missing. Rerun vision setup.')

def launch(key,open_browser=True):
    if key=='campus':
        verify(key)
        return zgx.launch(key,open_browser)
    verify(key)
    p=zgx.repo(key);campus=zgx.repo('campus')
    lock=(ROOT/'.partner-launch.lock').open('w')
    try:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close()
        raise RuntimeError('Another demo is active. Press Ctrl+C in its terminal before switching.')
    port=zgx.SPECS[key][3]
    ports=[8081,port]+([8082,8130] if key in ('atlas','vision') else [])+([8110] if key=='atlas' else [])
    children,handles=[],[]
    env=os.environ.copy()
    env.update({'PYTHONPATH':str(p),'ZGX_DEMOS_ROOT':str(ROOT),'ORT_DISABLE_TELEMETRY':'1',
        'HF_HUB_OFFLINE':'1','TRANSFORMERS_OFFLINE':'1','HF_HUB_DISABLE_TELEMETRY':'1','DO_NOT_TRACK':'1'})
    for var in ('NO_PROXY','no_proxy'):
        env[var]=env.get(var,'')+',127.0.0.1,localhost'
    logs=ROOT/'partner-logs'/time.strftime('%Y%m%d-%H%M%S');logs.mkdir(parents=True,exist_ok=True)
    def start(cmd,label,cwd=p):
        logfile=logs/(label+'.log');handle=logfile.open('w');handles.append(handle)
        proc=subprocess.Popen([str(v) for v in cmd],cwd=cwd,env=env,
            stdout=handle,stderr=subprocess.STDOUT,start_new_session=True)
        children.append(proc);print('Starting '+label+' | '+str(logfile),flush=True)
        return proc,logfile
    try:
        zgx.free_ports(ports)
        proc,log=start(['bash','scripts/model-start.sh'],'generation',campus)
        zgx.wait_ready('http://127.0.0.1:8081/health',proc,600,log,lambda j:j.get('status')=='ok')
        if key in ('atlas','vision'):
            proc,log=start(['bash','scripts/embedding-start.sh'],'embedding',campus)
            zgx.wait_ready('http://127.0.0.1:8082/health',proc,300,log,lambda j:j.get('status')=='ok')
            proc,log=start([service_py(),KIT/'public_services.py','vision'],'vision')
            zgx.wait_ready('http://127.0.0.1:8130/health',proc,180,log,lambda j:j.get('ok') is True)
        if key=='atlas':
            proc,log=start([service_py(),KIT/'public_services.py','speech'],'speech')
            zgx.wait_ready('http://127.0.0.1:8110/health',proc,180,log,lambda j:j.get('ok') is True)
        import public_client
        print('Running a real model readiness request...',flush=True)
        public_client.generate('Reply with the single word READY.',timeout=180)
        if key in ('atlas','vision'):
            public_client.embed(['Local partner launch readiness check.'])
        proc,log=start([zgx.py(key),KIT/'public_entry.py',key,'--port',str(port)],'app')
        zgx.wait_ready(f'http://127.0.0.1:{port}/partner/health',proc,240,log,lambda j:j.get('ready') is True)
        url=f'http://127.0.0.1:{port}'+zgx.SPECS[key][4]
        print('\nREADY: '+url+'\nPublic replacement profile. Keep this terminal open; Ctrl+C stops the demo.',flush=True)
        if open_browser:
            webbrowser.open(url)
        while True:
            time.sleep(1)
            if any(c.poll() is not None for c in children):
                raise RuntimeError('A service exited. See '+str(logs))
    finally:
        zgx.cleanup(children)
        for h in handles:h.close()
        lock.close()

def main():
    def stop(signum,frame):raise KeyboardInterrupt
    signal.signal(signal.SIGINT,stop);signal.signal(signal.SIGTERM,stop)
    p=argparse.ArgumentParser(description='ZGX public partner profile v3')
    p.add_argument('action',choices=['setup','launch','verify','shortcut'])
    p.add_argument('demo',choices=[*TITLES,'all']);p.add_argument('--no-browser',action='store_true')
    a=p.parse_args()
    try:
        if a.action=='setup':setup(a.demo)
        elif a.demo=='all':raise RuntimeError('Use all only with setup; launch one app at a time.')
        elif a.action=='launch':launch(a.demo,not a.no_browser)
        elif a.action=='verify':verify(a.demo);print('Assets verified.')
        else:write_shortcut(a.demo)
    except KeyboardInterrupt:print('\nDemo stopped.')
    except Exception as exc:
        print('\nACTION REQUIRED: '+str(exc),file=sys.stderr)
        if sys.stdin.isatty():input('Press Enter to close. ')
        sys.exit(1)

if __name__=='__main__':main()
