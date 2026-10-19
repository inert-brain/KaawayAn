# BUILD — Python side of KaawayAn

Audience: an AI coding agent (or human) with a fresh `git clone` of this
branch, on a **Windows test machine with the ESP32 node plugged in as
COM19**. Goal: run the LIVE pipeline (real USB packets → predictions →
`database/events.db`). No sample WAVs, no simulation — real hardware only.


## 1. What you need (prerequisites)

| # | Requirement | Version / detail | Why |
|---|---|---|---|
| 1 | `git` | any recent | clone the repo |
| 2 | Python | 3.10–3.12 recommended (repo was built on 3.11) | everything runs on it |
| 3 | `pip` (or `conda`) | — | install packages |
| 4 | PyTorch CPU | `torch`, `torchaudio` from https://pytorch.org (CPU wheel is enough; no GPU needed) | model inference + mel spectrogram |
| 5 | `pyserial` | `pip install pyserial` | `staging/receiver.py` USB-CDC serial |
| 6 | `numpy` | pulled in by torch; pin `numpy<2` if sqlite hits `np.float32` binding issues | packet parsing, audio buffers |
| 7 | Model weights | `model/drone_classifier_aug_mixed.pt` (~8.5 MB) — **must already be in the clone**; NOT gitignored, NOT downloaded at runtime | `model/model.py` raises `FileNotFoundError` without it |
| 8 | ESP32 node, flashed | firmware from `esp32/esp32.ino`, USB CDC @ 921600 baud, binary packets per `protocol.md` | the live data source |
| 9 | USB data cable + drivers | cable MUST be data-capable (charge-only cables fail silently); CP210x/CH340 drivers installed so the node appears as **COM19** | physical link |
| 10 | `sqlite3` CLI (optional) | ships with Python as a module; CLI for manual inspection | verify `database/events.db` |

NOT needed: `huggingface_hub`, `soundfile` (testbench-only), sample WAVs /
`model/drone_recordings/` (simulation-only), a `.conda/` env inside the repo
(stale — create your own env outside the repo), CUDA.


## 2. Setup (fresh clone → runnable)

```bat
REM 0. Clone and enter the repo. <repo-url> is this repo; <branch> is this branch.
git clone --branch <branch> <repo-url> KaawayAn
cd KaawayAn
REM Repo: https://github.com/inert-brain/KaawayAn.git — confirm branch with your operator.

REM 1. Python env (pick ONE):
python -m venv .venv && .venv\Scripts\activate
REM conda create -n kaawayan python=3.11 -y && conda activate kaawayan

REM 2. PyTorch CPU (Windows). If this fails, follow https://pytorch.org/get-started/locally/
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cpu

# 3. Everything else (single line):
pip install pyserial "numpy<2" streamlit

REM 4. Verify the weights file exists (fatal if missing):
dir model\drone_classifier_aug_mixed.pt
REM expect: ~8.5 MB. If absent, fetch from
REM https://huggingface.co/AntoineNaccache/drone-audio-detector (file:
REM drone_classifier_aug_mixed.pt) and place it at model/drone_classifier_aug_mixed.pt

REM 5. Confirm the node is visible as COM19:
python -m serial.tools.list_ports
REM expect: COM19 in the list, with USB-serial description.
REM If missing: reseat USB, try another cable (must be data-capable),
REM install CP210x/CH340 drivers, check Device Manager for yellow warnings.

REM 6. Point the pipeline at COM19 (config.py line 7):
REM     PORTS = ["COM19"]
REM Single-node live test: COM19 must be the ONLY entry so a wrong cached
REM port can never be opened by accident.
```

Working directory for ALL commands below is the repo root (`KaawayAn/`,
the folder containing `config.py` and `main.py`). Imports are
`config.*`, `staging.*`, `model.*` — running from any subdirectory breaks
them. There is no `requirements.txt` / `environment.yml` yet; the `pip`
lines above ARE the dependency list.


## 3. Run it — LIVE ESP32 test on COM19 (in order — stop at first failure)

```bat
REM STEP 1 — static check: every pipeline file must compile.
python -m py_compile config.py main.py database/database.py event_handler/handler.py model/model.py model/classifier_pipeline.py staging/receiver.py && echo COMPILE_OK
REM expect: COMPILE_OK. If a file fails, fix syntax before continuing.

REM STEP 2 — record the DB row count BEFORE (baseline for comparison).
python -c "import sqlite3; print('rows before:', sqlite3.connect('database/events.db').execute('SELECT COUNT(*) FROM detection_events').fetchone()[0])"
REM expect: rows before: N (any number; a fresh events.db auto-creates on first Database()).

REM STEP 3 — run the live pipeline. Plug in the node FIRST, then:
python main.py
REM expect within ~5 s: "Opened port COM19", then "Pipeline running. Ctrl+C to stop."
REM then every 5 s a stats line, e.g.:
REM   queued: receiver=0 classifier=0 location=0
REM Healthy = all three near 0 (packets consumed as fast as they arrive).
REM Let it run AT LEAST 60 seconds: at 10 packets/s the handler emits
REM ~1 inference window/s/node, so expect ~60 new DB rows per minute per node.
REM Stop with Ctrl+C. Teardown prints "Database connnection closed".
```

```bat
REM STEP 4 — confirm predictions landed in database/events.db (SAME db as production).
python -c "import sqlite3; print('rows after:', sqlite3.connect('database/events.db').execute('SELECT COUNT(*) FROM detection_events').fetchone()[0])"
REM expect: rows after = rows before + ~1 per second of runtime (per node).
REM PASS = count grew. FAIL = count unchanged (see troubleshooting).

REM STEP 5 — inspect the newest rows (they must be from THIS run: fresh timestamps).
sqlite3 database/events.db "SELECT event_id, node_id, predicted_drone, model_confidence, timestamp FROM detection_events ORDER BY event_id DESC LIMIT 5;"
REM (No sqlite3 CLI? use:)
python -c "import sqlite3; [print(r) for r in sqlite3.connect('database/events.db').execute('SELECT event_id, node_id, predicted_drone, model_confidence FROM detection_events ORDER BY event_id DESC LIMIT 5')]"
REM expect: node_id = the NODE_ID flashed into esp32.ino; predicted_drone in (0,1);
REM model_confidence floats in [0,1] that VARY row to row.
REM Live data has no ground truth, so there is no accuracy score — flow is the test:
REM USB bytes -> parsed packets -> 10-packet windows -> probabilities -> committed rows.
```


## 4. If something fails (agent troubleshooting)

| Symptom | Cause → fix |
|---|---|
| `FileNotFoundError: Model weights not found` | `model/drone_classifier_aug_mixed.pt` missing → fetch per §2 step 4 |
| `ModuleNotFoundError: No module named 'config'` (or staging/model/…) | wrong cwd → `cd` to repo root (`KaawayAn/`) and rerun; never run from subdirs |
| `ModuleNotFoundError: No module named 'torch'/'serial'/'torchaudio'` | env not activated or packages missing → redo §2 steps 1–3 in the SAME shell |
| COM19 absent from `list_ports` | cable/port/driver → reseat USB, swap cable (data-capable only), install CP210x/CH340 drivers, confirm in Device Manager; never edit code for this |
| `Port COM19 is unavailable` then `ConnectionError` | port exists in Device Manager but busy or permission-held → close Arduino Serial Monitor / other holders, unplug-replug, rerun |
| `Opened port COM19` but `Receiver: packet rejected -- different packet marker` repeating | firmware/baud mismatch → confirm ESP32 flashed with `esp32/esp32.ino`, `SERIAL_BAUD` 921600 both sides, binary (not ASCII) on the wire; first two bytes of each 3218-B frame must be `BA AB` |
| `Opened port COM19` but no stats movement and DB count static | node not streaming → check ESP32 `READY binary` line on a monitor at 921600 BEFORE running main.py (then close the monitor — one holder at a time); check mic wiring (I2S pins in `esp32.ino`) |
| `receiver=` queue grows unboundedly in stats | handler worker dead → confirm no exception above it; `Handler.enable_pipeline()` must have been called (see `main.py`) |
| `classifier=` queue grows, DB static | windows never complete → handler needs 10 same-node packets per window; single node sending at 10 pkt/s yields 1 window/s; stalls mean packet loss or node_id flapping |
| All `model_confidence` values identical (≈0.5) | weight/architecture key mismatch silently swallowed by `strict=False` → verify `model/model.py` class matches the checkpoint (see Hub `model.py`); do NOT "fix" by changing the threshold |
| `sqlite3.OperationalError: no such table` on `database/events.db` | 0-byte pre-created file and `Database()` never ran → the table is created on first real run; one `main.py` session fixes it |
| `Rows after` equals `rows before` after 60 s | pipeline ran but logged nothing → run through this table top-down; most likely: node not streaming, or windows stalling (see above) |


## 5. Layout (what lives where)

```
KaawayAn/
  main.py                  # live entry point: Receiver -> Handler -> Classifier -> events.db
  config.py                # PORTS (set ["COM19"] for this test), baud, packet sizes, thresholds
  protocol.md              # wire format (what the ESP32 firmware must send)
  esp32/esp32.ino          # node firmware (INMP441 + BME280, USB CDC 921600)
  database/database.py     # SQLite database/events.db (auto-created on first Database())
  event_handler/handler.py # per-node 10-packet windowing
  model/model.py           # DroneClassifier + predict(np.ndarray)->float
  model/classifier_pipeline.py  # threshold + DB logging worker
  staging/receiver.py      # USB-CDC packet parsing
  staging/test_inference.py# hardware-free simulation testbench (NOT used in this live test)
  model/drone_classifier_aug_mixed.pt  # weights, must exist
```

`.gitignore` covers `*.db`, `__pycache__/`, `model/drone_recordings`,
`staging/test_stream.py`. Do NOT commit `*.db` files or recordings.
`database/events.db` WILL grow during this test (expected — ~1 row/s/node);
that is production data, not pollution.
