# AIGaikan (IC4 + Qt + Anomalib)


**Hardware**: 4× Imaging Source DFK 33UX287 (USB3), PLC with hardware trigger, CONTEC DIO-1616LN-USB.


## Quick Start


1. Install vendor drivers and IC4 runtime + Python SDK.
2. `pip install -r requirements.txt` (ensure a matching CUDA/PyTorch env).
3. Put your Anomalib `.ckpt` at `checkpoints/model.ckpt`.
4. Edit `configs/cameras.yaml` with real serials.
5. Run: `python run.py`


If you don’t have cameras connected, the app runs in **mock mode** (random frames) so you can test the UI and end‑to‑end flow.


## Notes
- For **true real‑time exposure**, wire the PLC hardware trigger to each camera’s Trigger In. The PC only *receives* images and aligns them by `TriggerIndex` read from DIO.
- Replace the `DIOClient` stub with actual CONTEC calls (ctypes/cffi) to read the trigger counter and drive OK/NG outputs.
- If `Anomalib` API differs for your version, adjust `InferenceBackend` accordingly.
- Consider moving inference to a separate **process** if your UI becomes sluggish under GPU load (IPC via ZeroMQ or `multiprocessing.Queue`).


## Next Steps
- Add NG-only image/video recording, SQLite/MySQL writer, and recipe switching GUI.
- Implement cylinder unwrapping (precompute maps; use `cv2.remap`).
- Add watchdogs and metrics (drop rate %, p50/p95/p99 latencies) in the status bar.
