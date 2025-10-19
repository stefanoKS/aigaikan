# AIGaikan (IC4 + Qt + Anomalib)


4‑camera, PLC‑triggered visual inspection pipeline for continuous extrusion.
- **Cameras:** Imaging Source **DFK 33UX287** (USB3) ×4
- **Triggering:** PLC → **hardware trigger** → cameras
- **DIO:** CONTEC DIO‑1616LN‑USB for OK/NG, heartbeat, and optional inputs
- **Capture SDK:** IC Imaging Control 4 (IC4)
- **UI:** PyQt5 (loads `app/ui/mainWidget.ui` if present)
- **AI:** Anomalib (.ckpt) on PyTorch


The app is **resilient**: if IC4/cameras/DIO are missing, it falls back to **mock** devices so you can run the full pipeline and UI.


---
## 1) System requirements
- Windows 10/11 x64
- Python 3.10 or 3.11 (recommended)
- (Optional GPU) NVIDIA GPU with CUDA‑capable driver for PyTorch
- Admin rights to install IC4 and CONTEC drivers


---
## 2) Install vendor drivers & SDKs


### Imaging Source IC4
1. Install IC Imaging Control 4 (runtime + Python SDK) from Imaging Source.
2. Verify you can import the Python module:
```python
import imagingcontrol4 as ic4
print(ic4.__version__)
```
3. Connect each **DFK 33UX287**, note the **serial numbers** for `configs/cameras.yaml`.


### CONTEC DIO‑1616LN‑USB
1. Install CONTEC Digital I/O driver (includes the DLL, e.g. `cdio.dll`).
2. Confirm the DLL path (e.g., `C:/CONTEC/DIO/cdio.dll`).
3. You do **not** need a Python wheel; this repo uses `ctypes` to call the DLL.


> If you don’t have the hardware/drivers yet, the app will run in **mock mode**.


---
## 3) Create the Python environment


### Option A (recommended): **pip venv** with headless OpenCV
This avoids Qt plugin conflicts.
```bat
py -3.10 -m venv .venv
.venv\\Scripts\\activate
pip install --upgrade pip
pip install -r requirements.txt
```
- We use **`opencv-python-headless`**. Do not install `opencv-python` in this env.
- Install a **CUDA‑matching** PyTorch if you have a GPU. Example (CUDA 12.x):
```bat
pip install --index-url https://download.pytorch.org/whl/cu121 torch torchvision torchaudio
```
Otherwise CPU:
```bat
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cpu
```


### Option B: **conda‑forge** toolchain (Qt + OpenCV ABI‑stable)
```bash
conda create -n aiextr python=3.10 pyqt=5.15 opencv=4.8 numpy=1.26 -c conda-forge
conda activate aiextr
pip install -r requirements.txt # will skip OpenCV pins; conda version wins
```
> Avoid mixing pip/conda for Qt/OpenCV. Stick to one channel per env.


---
## 4) Project configuration


### Cameras
Edit `configs/cameras.yaml` with your **DFK 33UX287** serials and exposure settings:
```yaml
- 本リポジトリの統合コード © あなた（プロジェクト所有者）。