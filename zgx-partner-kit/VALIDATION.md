# Validation record - public profile v3

Date: 2026-10-29. Release candidate, not hardware-certified.
Environment: Linux x86_64 CPU container, no NVIDIA GPU, 8 GiB memory limit.

## Passed in this environment

- Seven existing launcher tests: occupied-port protection, missing private adapter,
  corrupted checksum, dead-child readiness failure, process ownership/cleanup,
  TensorRT header helper and live-engine validation call.
- Seven new public-profile tests: strict decision schema, invalid embedding rejection,
  no fabricated empty-answer fallback, no request for empty embeddings, missing
  setup receipt rejection, pinned additional assets, corrupt download cannot
  replace an installed file.
- All seven additional public files downloaded and matched SHA-256/size metadata:
  RF-DETR weights, Whisper base, two Piper voices/configs and CC0 sample image.
- whisper.cpp compiled at the pinned commit on CPU.
- llama.cpp compiled at the Campus-pinned commit on CPU. One zero-length object
  from the parallel container build was rebuilt serially; final executable passed
  the integration checks below. The Nano CUDA build was not run here.
- Python dependency resolution for the service stack and Atlas on Linux ARM64.
  This checks package availability, not execution on ARM64.
- Real Piper English synthesis -> Whisper ASR reproduced "Atlas is ready to
  explore the data." Spanish synthesis/transcription ran, with transcription
  errors in the short test phrase; no Spanish recognition-quality claim.
- RF-DETR loaded offline and correctly detected cup, spoon and dining table in
  the source photo. All four generated tabletop feeds contained a cup detection.
- Local generation and 1024-dimensional Qwen embeddings returned real outputs.
- Atlas HTTP startup, model labels, public profile banner and vision proxy passed.
  Its real WebSocket question returned result, grounded sentences, audio and done
  events. The sample answer reported Q4 revenue $644.7M and profit $86.0M, with
  four of four numbers passing the original grounding check.
- Beat the Guard public startup exposed all six missions, a healthy model and a
  visible replacement banner. A real model request returned a schema-valid policy
  decision from supplied evidence. This is not an adversarial policy evaluation.
- Research Observatory loaded four generated videos, performed real detections,
  generated a local insight and stopped feeds cleanly. Its public reasoning
  adapter constrains the response to the displayed card schema and allowed
  value-lever labels.
- ONNX Runtime was started with telemetry disabled before import. The original
  default-telemetry attempt was blocked by the execution environment; the explicit
  disabled-telemetry test completed. No claim of a machine-wide network audit.
- PDF: seven pages rendered and inspected; four local-app links included.

## Important test substitution

The container cannot hold the 35B target model. Integration tests used public
Qwen3 0.6B Q8_0, revision 23749fefcc72300e3a2ad315e1317431b06b590a, with the SAME
llama.cpp APIs and public adapters. This test model is not selected by the Nano
installer and is not included in the release. Tests establish API compatibility,
not target-model quality, latency, CUDA compatibility, or Nano acceptance.

## Remaining Nano acceptance

1. Clean setup on the supported Nano image, including CUDA runtime build.
2. Verify target Qwen3.6 35B, Qwen embeddings and Campus reranker load and infer.
3. Launch each Applications shortcut and verify the browser opens only after ready.
4. Run Campus advising, Atlas typed/voice/document question, one policy-game mission,
   and a Research Observatory feed plus insight.
5. Test microphone permission, optional camera and audio playback on the actual Nano.
6. Stop/switch demos, reboot, and confirm launch does not download model files.
7. Record Nano image/toolkit version, tester, date and remaining issues.

## Publication

GitHub write attempt returned HTTP 403: Resource not accessible by integration.
No remote branch, commit, PR or upload was created. Source-only patches for all
four repositories are prepared separately. Large model/media files remain
upstream downloads or setup-generated assets under repository publication policy.

All four source-only updates passed the repository Gitleaks/staged-file/history publication checks.
