"""Animate one CC0 tabletop photo for reproducible, real detector inputs.

Four views of the same still image are illustrative media, not four real cameras.
"""
import json
import os
from pathlib import Path
import subprocess

ROOT = Path(os.environ.get('ZGX_DEMOS_ROOT',str(Path.home()/'zgx-demos')))

def main():
    source = ROOT/'public-models/coffee.png'
    media = ROOT/'research-observatory/media'
    media.mkdir(parents=True,exist_ok=True)
    manifest = []
    for i in range(1,5):
        filename = f'public-tabletop-{i}.mp4'
        dest = media/filename
        vf = (f"scale=1280:854,crop=1280:720:0:{(i-1)*35},"
            f"drawtext=text='ILLUSTRATIVE STILL PHOTO - VIEW {i}':fontcolor=white:fontsize=25:x=25:y=25:box=1:boxcolor=black@0.65,"
            "drawtext=text='CC0 Rachel Michetti - Not a live laboratory':fontcolor=white:fontsize=21:x=25:y=h-55:box=1:boxcolor=black@0.65")
        if not dest.exists():
            subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-loop','1','-i',str(source),
                '-t','12','-vf',vf,'-r','10','-c:v','libx264','-pix_fmt','yuv420p',
                '-preset','fast','-crf','26','-movflags','+faststart','-an',str(dest)],check=True)
        manifest.append({'file':filename,'name':f'Illustrative tabletop view {i}',
            'discipline':'CC0 STILL PHOTO / REAL INFERENCE','slot':i,
            'detection_mode':'people_objects','conf_threshold':.30,'fit':'cover','enhance_inference':False})
    out=ROOT/'public-media';out.mkdir(exist_ok=True)
    (out/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print('Four illustrative tabletop clips prepared.')

if __name__=='__main__':
    main()
