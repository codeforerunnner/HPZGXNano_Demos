# Public partner profile assets

This profile removes the need to copy the owner's original model folders. It does
not reproduce trained Sentinel V6 behavior or Kev calibration. Weights download
from upstream publishers during setup. Repository policy excludes model binaries.

`public-assets.json` pins additional downloads by SHA-256 and byte size. The
CampusCompass `docs/MODEL-MANIFEST.json` pins generation, embedding and reranking.

| Purpose | Replacement | Upstream source |
| --- | --- | --- |
| Shared reasoning | Qwen3.6 35B A3B UD-Q4_K_XL | https://huggingface.co/unsloth/Qwen3.6-35B-A3B-MTP-GGUF |
| Embeddings | Qwen3 Embedding 0.6B Q8_0 | https://huggingface.co/Qwen/Qwen3-Embedding-0.6B-GGUF |
| Campus reranking | Qwen3 Reranker 0.6B Q8_0, converted by the repo script | https://huggingface.co/Mungert/Qwen3-Reranker-0.6B-GGUF |
| Atlas transcription | Whisper base multilingual on CPU | https://github.com/ggml-org/whisper.cpp and https://huggingface.co/ggerganov/whisper.cpp |
| English voice | Piper LJSpeech medium | https://huggingface.co/rhasspy/piper-voices ; retained model card describes public-domain training data |
| Spanish voice | Piper CarlFM x_low | Same voice repo; retained model card describes public-domain training data. Lower-fidelity Spanish voice. |
| Object detection | RF-DETR Nano on CPU | https://github.com/roboflow/rf-detr |
| Tabletop sample | Coffee photo by Rachel Michetti, courtesy of Pikolo Espresso Bar | CC0 per https://scikit-image.org/docs/0.25.x/api/skimage.data.html#skimage.data.coffee |
| Business/advising records | Repositories' synthetic data/generators | No customer records added |

Piper runtime and license: https://github.com/OHF-Voice/piper1-gpl . Partners install
it from PyPI; its binary is not distributed in this kit. Upstream model, library
and voice terms continue to apply. This kit grants no new third-party license.
Model cards for the selected voices are retained in `notices/`. Neither Lessac
nor DaveFX is used in this profile.

Research Observatory creates four MP4 views from one CC0 still photograph. Every
frame is labeled illustrative. A real detector processes those frames, but they
are not four real cameras, laboratory events, PPE/safety validation, or an accuracy
benchmark. The existing app controls also support a connected camera.

Speech and vision use CPU. Qwen uses Nano CUDA through llama.cpp. This avoids a
custom TensorRT engine and platform-specific GPU speech build. Do not present
these results as TensorRT, Nemotron, Magpie or trained Sentinel performance.

ONNX telemetry is disabled before runtime import. HF telemetry is disabled and
launch sets offline model-cache flags. Inference uses loopback services. This is
not a machine-wide firewall or proof that unrelated system software is offline.
