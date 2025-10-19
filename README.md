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
.venv\Scripts\activate
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
---
## ベンダー製ドライバ／SDK のインストール


### Imaging Source IC4
1. Imaging Source から IC Imaging Control 4（ランタイム＋Python SDK）をインストール。
2. Python で以下を実行し、読み込み確認：
```python
import imagingcontrol4 as ic4
print(ic4.__version__)
```
3. **DFK 33UX287** を接続し、`configs/cameras.yaml` 用に **シリアル番号** を控えます。


### CONTEC DIO‑1616LN‑USB
1. CONTEC の Digital I/O ドライバをインストール（DLL 例：`cdio.dll`）。
2. DLL パス（例：`C:/CONTEC/DIO/cdio.dll`）を確認。
3. Python 用ホイールは不要。本リポジトリは `ctypes` で DLL を呼び出します。


> ハードウェア／ドライバが無くてもアプリは **モックモード** で動作します。


---
## Python 環境の作成


### 推奨（A）：**pip 仮想環境**＋ headless OpenCV
Qt と OpenCV の競合を避けます。
```bat
py -3.10 -m venv .venv
.venv\Scripts\activate
pip install --upgrade pip
pip install -r requirements.txt
```
- **`opencv-python-headless`** を使用（この環境に `opencv-python` は入れない）。
- GPU がある場合は **CUDA に合う** PyTorch を別途インストール：
```bat
pip install --index-url https://download.pytorch.org/whl/cu121 torch torchvision torchaudio
```
CPU のみの場合：
```bat
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cpu
```


### 代替（B）：**conda‑forge** ツールチェーン（Qt と OpenCV の ABI を統一）
```bash
conda create -n aiextr python=3.10 pyqt=5.15 opencv=4.8 numpy=1.26 -c conda-forge
conda activate aiextr
pip install -r requirements.txt # OpenCV は conda 版が優先されます
```
> Qt/OpenCV は同一チャネルで統一し、pip と conda の混在は避けてください。


---
## プロジェクト設定


### カメラ設定
`configs/cameras.yaml` に **DFK 33UX287** のシリアルと露光等を設定：
```yaml
cameras:
- serial: "12345678"
model: "DFK 33UX287"
resolution: [1920, 1200]
pixel_format: "Mono8"
exposure_us: 2000
gain_db: 0.0
# 4台分を追記
```
ファイルが無い／カメラ未検出の場合は **モックカメラ**（ランダム画像）で起動します。


### DIO（CONTEC）設定
`configs/dio.yaml` を編集：
```yaml
dll_paths:
- "vendor/cdio.dll" # 例：C:/CONTEC/DIO/cdio.dll
- "cdio.dll"


device_index: 0
input_port: 0
trigger_bit: 0


# PLC への結果出力（任意）
output_port: 0
ok_bit: 1


poll_hz: 2000
```
ファイルが無い／DLL 読み込み失敗時は **MockDIO** に自動切替（トリガカウンタのみ動作）。


### モデルチェックポイント
Anomalib の `.ckpt` を配置：
```
checkpoints/model.ckpt
```
未配置／非互換の際は読み込みエラーになります（必要なら **モックモデル** も用意可能）。


### UI ファイル
Qt Designer で作成した UI を以下に保存：
```
app/ui/mainWidget.ui
```
プレビュー用 `QLabel` のオブジェクト名は `cam0`～`cam3` を推奨。未設定でも最初の4つの `QLabel` を自動検出。


---
## 実行
```bat
python run.py
```
- 起動時に 2×2 プレビューとステータス表示が見えます。
- ハードウェア接続時はライブ映像、未接続時はモック画像になります。
- 各トリガ毎に OK/NG を判定し、（任意で）DIO の出力ビットに反映します。


---
## アーキテクチャ
- **PLC → カメラ**：ハードウェアトリガ（µs オーダの露光決定）
- **カメラ → PC**：IC4 の `QueueSink` でフレーム受信 → 各カメラバッファへ
- **コーディネータ**：DIO の `TriggerIndex` により 4視点を整合
- **前処理**：固定サイズにリサイズ／正規化
- **推論**：Anomalib（.ckpt, PyTorch）→ 視点別スコア → 統合判定
- **後処理**：OK/NG 判定 → **DIO 出力** → UI 更新
- **冗長化**：いずれか欠けても **モック** に切替し処理継続


---
## トラブルシューティング
- **Qt / OpenCV 衝突**：`opencv-python` ではなく **headless** を使用してください。
- **`imagingcontrol4` 未検出**：IC4 を導入。無くてもモックカメラで動作します。
- **CONTEC DLL 未検出**：`configs/dio.yaml` の DLL パスを修正。失敗時は MockDIO で継続。
- **Torch/Anomalib 不整合**：CUDA に合う PyTorch を導入。API 差異は `app/core/infer_worker.py` を調整。


---
## パフォーマンスのコツ
- Windows の **高パフォーマンス電源プラン** を有効化。
- **USB3** はできるだけマザーボード直結。ハブは可能なら回避。
- 推論前に **pinned memory** を活用し `.to(cuda, non_blocking=True)` を使用。
- UI 描画は軽量化（プレビューは縮小画像を渡す）。


---
## 今後の予定
- `configs/dio.yaml` に **ピン名マップ**（READY/BUSY/HEARTBEAT/NG_CLASS）を追加
- 完全オフライン用 **モックモデル**
- GPU 分離のための **ZeroMQ プロセス分割**


---
## ライセンス / クレジット
- Imaging Source IC4 © The Imaging Source.
- CONTEC DIO © CONTEC.
- Anomalib © Authors.
- 本リポジトリの統合コード © あなた（プロジェクト所有者）。