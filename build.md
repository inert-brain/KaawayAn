# BUILD — Python side of KaawayAn

Audience: an AI coding agent (or human) with a fresh `git clone` of this
branch, on Linux. Goal: run the drone-audio pipeline or its testbench.
No ESP32 hardware is required for verification.

## 1. What you need (prerequisites)

| # | Requirement | Version / detail | Why |
|---|---|---|---|
| 1 | `git` | any recent | clone the repo |
| 2 | Python | 3.10–3.12 recommended (repo was built on 3.11; 3.14 works but is untested upstream) | everything runs on it |
| 3 | `pip` (or `conda`) | — | install packages |
| 4 | PyTorch CPU | `torch`, `torchaudio` from https://pytorch.org (CPU wheel is enough; no GPU needed) | model inference + mel spectrogram |
| 5 | `pyserial` | `pip install pyserial` | `staging/receiver.py` USB-CDC serial |
| 6 | `numpy` | pulled in by torch, but pin `numpy<2` if you hit `np.float32` binding issues with sqlite | packet parsing, audio buffers |
| 7 | `soundfile` (or `sox`) backend for torchaudio | `pip install soundfile` | `test_inference.py` loads `.wav` via `torchaudio.load` |
| 8 | Model weights | `model/drone_classifier_aug_mixed.pt` (~8.5 MB) — **must already be in the clone**; NOT gitignored, NOT downloaded at runtime | `model/model.py` raises `FileNotFoundError` without it |
| 9 | Test audio (optional, testbench only) | `model/drone_recordings/Binary_Drone_Audio/{yes_drone,unknown}/*.wav` — **gitignored** (`model/drone_recordings` in `.gitignore`), so a fresh clone LACKS it; copy it in or point `RECORDINGS` elsewhere | `staging/test_inference.py` input |

NOT needed: `huggingface_hub` (legacy import removed from the pipeline path),
a `.conda/` env inside the repo (vendored env dir is stale — create your own
env outside the repo), an ESP32 (only for live runs), CUDA.

## 2. Setup (fresh clone → runnable)

```sh
# 0. Clone and enter the repo. <repo-url> is this repo; <branch> is this branch.
git clone --branch <branch> <repo-url> KaawayAn
cd KaawayAn

# 1. Python env (pick ONE):
python -m venv .venv && source .venv/bin/activate   # venv route, or:
# conda create -n kaawayan python=3.11 -y && conda activate kaawayan   # conda route

# 2. PyTorch CPU (Linux). If this fails, follow https://pytorch.org/get-started/locally/
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cpu

# 3. Everything else (single line):
pip install pyserial "numpy<2" soundfile

# 4. Verify the weights file exists (fatal if missing):
ls -la model/drone_classifier_aug_mixed.pt
# expect: ~8.5 MB. If absent, fetch from
# https://huggingface.co/AntoineNaccache/drone-audio-detector (file:
# drone_classifier_aug_mixed.pt) and place it at model/drone_classifier_aug_mixed.pt

# 5. (Testbench only) Provide wav files — fresh clones do NOT include them:
ls model/drone_recordings/Binary_Drone_Audio/{yes_drone,unknown} | head
# expect: *.wav listings. If missing, copy them in from the dataset owner
# (see model/credits.md) preserving the yes_drone/ + unknown/ layout.
```

Working directory for ALL commands below is the repo root (`KaawayAn/`,
the folder containing `config.py` and `main.py`). Imports are
`config.*`, `staging.*`, `model.*` — running from any subdirectory breaks
them. There is no `requirements.txt` / `environment.yml` yet; the `pip`
lines above ARE the dependency list.

## 3. Run it (in order — stop at first failure)

```sh
# STEP 1 — static check: every pipeline file must compile.
python -m py_compile config.py main.py database/database.py \
  event_handler/handler.py model/model.py model/classifier_pipeline.py \
  staging/receiver.py staging/test_inference.py && echo COMPILE_OK
# expect: COMPILE_OK. If a file fails, fix syntax before continuing.

# STEP 2 — full pipeline testbench (NO hardware; uses pty virtual serial).
# Replays real wavs as ESP32 packets through
# Receiver -> Handler -> ClassifierPipeline -> database/events.db
# and prints per-window OK/MISS + accuracy.
python staging/test_inference.py --n=1
# expect (tail): "Window accuracy: 2/2 = 100.00%", "Rows in file: N".
# Drones print conf≈1.000, non-drones conf≈0.00x.
# More coverage: --n=4, or --label=yes_drone / --label=unknown.

# STEP 3 — confirm predictions persisted (testbench appends; never deletes).
python -c "import sqlite3; print('events.db rows:', sqlite3.connect('database/events.db').execute('SELECT COUNT(*) FROM detection_events').fetchone()[0])"
# expect: events.db rows: >=2 (grows with each testbench run).
# Inspect: sqlite3 database/events.db "SELECT * FROM detection_events ORDER BY event_id DESC LIMIT 5;"

# STEP 4 — live pipeline (REQUIRES ESP32 on a Config.PORTS entry; skip headless).
python main.py
# expect: "Pipeline running. Ctrl+C to stop." + queue stats every 5 s.
# Without hardware, Receiver.connect() raises ConnectionError — CORRECT,
# not a bug. Use STEP 2 instead.
```

## 4. If something fails (agent troubleshooting)

| Symptom | Cause → fix |
|---|---|
| `FileNotFoundError: Model weights not found` | `model/drone_classifier_aug_mixed.pt` missing → fetch per §2 step 4 |
| `ModuleNotFoundError: No module named 'config'` (or staging/model/…) | wrong cwd → `cd` to repo root (`KaawayAn/`) and rerun; never run from subdirs |
| `ModuleNotFoundError: No module named 'torch'/'serial'/'torchaudio'` | env not activated or packages missing → redo §2 steps 1–3 in the SAME shell |
| `SoundFile ... / torchaudio.load` backend error | `pip install soundfile` (testbench-only dep) |
| `ConnectionError: No candidate port could be opened` from `main.py` | no ESP32 plugged in → expected headless; run testbench (STEP 2) instead |
| `No wavs in ...` / `assert ... too short for one window` | `model/drone_recordings/` empty (gitignored) → provide wavs per §2 step 5 |
| `RuntimeError: Receiver: open the connection first` inside testbench | regression in testbench wiring order — `Handler()` must be constructed AFTER `receiver._connection` is assigned (see `staging/test_inference.py`) |
| All predictions identical (≈0.5) | weight/architecture key mismatch silently swallowed by `strict=False` → verify `model/model.py` class matches the checkpoint (see Hub `model.py`); do NOT "fix" by changing the threshold |
| `sqlite3.OperationalError: no such table` on `database/events.db` | 0-byte pre-created file; `Database()` creates the table on first real run — run STEP 2 once |

## 5. Layout (what lives where)

```
KaawayAn/
  main.py                  # live entry point (needs ESP32)
  config.py                # PORTS, baud, packet sizes, thresholds (source of truth)
  protocol.md              # wire format for firmware integrators
  database/database.py     # SQLite events.db (auto-created on first Database())
  event_handler/handler.py # per-node 10-packet windowing
  model/model.py           # DroneClassifier + predict(np.ndarray)->float
  model/classifier_pipeline.py  # threshold + DB logging worker
  staging/receiver.py      # USB-CDC packet parsing
  staging/test_inference.py# hardware-free full-chain testbench (STEP 2)
  model/drone_classifier_aug_mixed.pt  # weights, must exist
  model/drone_recordings/  # test wavs, GITIGNORED — provide manually
```

`.gitignore` covers `*.db`, `__pycache__/`, `model/drone_recordings`,
`staging/test_stream.py`. Do NOT commit `*.db` files or recordings.
