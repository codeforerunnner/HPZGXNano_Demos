#!/usr/bin/env python3
"""Generate four original geometric samples. No stock footage or real people."""
from pathlib import Path
import subprocess

def main():
    assets = Path(__file__).resolve().parent / 'assets'
    assets.mkdir(exist_ok=True)
    for i in range(1,5):
        out = assets / f'synthetic-lab-{i}.mp4'
        if out.exists():
            continue
        vf = (
            'drawgrid=width=80:height=80:thickness=1:color=0x22485a,'
            f"drawtext=text='SYNTHETIC SAMPLE {i} - UI WALKTHROUGH ONLY':fontcolor=white:fontsize=28:x=40:y=35,"
            "drawtext=text='NO REAL PEOPLE - NO DETECTOR ACCURACY CLAIM':fontcolor=0x9bcbd9:fontsize=19:x=40:y=80,"
            "drawtext=text='PREP                 ANALYSIS                 COMPLETE':fontcolor=white:fontsize=24:x=90:y=180,"
            'drawbox=x=80:y=230:w=330:h=330:color=0x007899:t=4,'
            'drawbox=x=470:y=230:w=330:h=330:color=0x76b900:t=4,'
            'drawbox=x=860:y=230:w=330:h=330:color=0x0096d6:t=4,'
            f"drawtext=text='SIMULATED WORKFLOW OBJECT':fontcolor=white:fontsize=20:x='80+mod(t*85+{i*55},990)':y=380,"
            "drawtext=text='Sample media for layout and playback testing.':fontcolor=0x9bcbd9:fontsize=22:x=40:y=650"
        )
        subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-f','lavfi','-i',
            'color=c=0x071f2c:s=1280x720:r=15:d=12','-vf',vf,
            '-c:v','libx264','-preset','fast','-crf','26','-pix_fmt','yuv420p',
            '-movflags','+faststart','-an','-y',str(out)],check=True)
        print('Created', out.name)

if __name__ == '__main__':
    main()
