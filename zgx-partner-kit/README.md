# HP ZGX Nano partner kit v3

Public-model release candidate. One initial setup, then launch from Applications.
Target: Nano Ubuntu ARM64 with the supported NVIDIA driver and CUDA toolkit.
A physical Nano end-to-end acceptance rehearsal remains required. Prototype,
not an official HP or NVIDIA support package.

## First setup

1. Power on the Nano, finish Ubuntu setup, connect network, display, keyboard and
   mouse. Connect microphone and speakers for Atlas voice.
2. In Terminal, check `nvidia-smi` and `nvcc --version`. If either fails, restore
   the supported Nano software image/toolkit before proceeding.
3. Allow at least 80 GB free disk, internet and administrator access. Setup
   downloads approximately 25 GB of models plus dependencies and compiles runtimes.
4. Extract this kit to `~/zgx-partner-kit`. Keep that folder in place.
5. Run:

```bash
bash "$HOME/zgx-partner-kit/install.sh" all
```

Setup installs common Ubuntu tools, uv and isolated Python/Node environments,
clones pinned source, verifies model downloads, builds llama.cpp and whisper.cpp,
generates sample data/media, and creates shortcuts. It does not replace NVIDIA
drivers or CUDA packages. Rerun after network failures; valid models are reused.
For one demo replace `all` with `campus`, `atlas`, `guard`, or `vision`.
Every profile uses the shared Campus model/runtime installation.

## Click to launch

Open Applications, search **ZGX**, and choose a demo. Ubuntu desktop icons may
require right-click > Allow Launching once. Keep the launch terminal open. The
browser opens after readiness checks. Press Ctrl+C before switching demos.

| Shortcut | Browser URL after launch | First action |
| --- | --- | --- |
| ZGX CampusCompass | http://127.0.0.1:18000 | Select a sample advising case and ask about requirements. |
| ZGX Atlas voice analytics | http://127.0.0.1:18110 | Enterprise: "What's our projected Q4 revenue and profit?" Allow microphone access. |
| ZGX Beat the Guard - Public Qwen | http://127.0.0.1:18080/play | Choose a mission and submit a request with the supplied evidence. |
| ZGX Research Observatory - Public Qwen | http://127.0.0.1:18120 | Start a sample feed, observe cup detections, then request a local insight. |

PDF links open an already running app. They cannot install software or start
local processes. Run the shortcut on the Nano first.

## Replacement behavior

- CampusCompass retains published Qwen generation, embeddings and reranker.
- Atlas uses Qwen, Whisper, Piper English/Spanish voices, RF-DETR and synthetic
  data. Decision scores are uncalibrated; developer execution/fine-tuning disabled.
- Beat the Guard uses a schema-checked public Qwen policy profile. This is not the
  trained Sentinel V6 adapter and has not been evaluated to equivalent quality.
- Research Observatory uses RF-DETR CPU detection, Qwen reasoning/embeddings and
  illustrative clips made from one CC0 photo. No original video folder, custom
  TensorRT engine or Ollama installation is required.

Original specialized profiles remain in upstream instructions and legacy
`zgx.py`. Use `portable.py` for v3. See `ASSET-SOURCES.md` for provenance.

## Troubleshooting

- First launch hashes the 35B model, then loads it. Read the terminal; do not
  launch a second copy while waiting.
- Missing shortcut: `python3 ~/zgx-partner-kit/portable.py shortcut atlas`.
- Manual launch: `python3 ~/zgx-partner-kit/portable.py launch atlas`.
- Port occupied: stop the previous demo in its own terminal. No unrelated process
  is automatically killed.
- Missing/corrupt asset: rerun `bash ~/zgx-partner-kit/install.sh atlas`.
- Logs: `~/zgx-demos/partner-logs/<timestamp>/app.log` and model/service logs.
- Voice: use the Nano browser at `127.0.0.1`, grant microphone permission and
  check Ubuntu sound devices. Typing works without a microphone.
- Vision latency: this profile uses CPU, not TensorRT.
- Existing checkout differs: preserve it and set a new `ZGX_DEMOS_ROOT` for setup.

## Release state

The connected GitHub integration rejected writes with HTTP 403. No remote branch,
commit or upload was created for v3. Prepared source-only repository updates are
supplied separately. Models stay upstream downloads under repository policy.
Run the Nano acceptance checklist in `VALIDATION.md` before partner handoff.
