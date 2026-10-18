"""Full-pipeline testbench: FakeESP32 -> Receiver -> Handler -> Classifier -> DB.

Builds the new unified packet (marker + node + timestamp + BME + 1600
audio samples = 3218 bytes) from real .wavs in model/drone_recordings,
streams them through a virtual serial pair, runs the real pipeline,
then dumps the detection_events table to prove predictions were logged.

Usage (from KaawayAn/):
    python staging/test_inference.py                # 2 files per label
    python staging/test_inference.py --n=4           # 4 files per label
    python staging/test_inference.py --label=yes_drone
"""
import os
import pty
import random
import sqlite3
import struct
import sys
import threading
import time
import tty
from pathlib import Path

import numpy as np
import serial as pyserial
import torchaudio

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # KaawayAn/
sys.path.insert(0, str(Path(__file__).resolve().parent))          # staging/

from config import Config
from staging.receiver import Receiver
from event_handler.handler import Handler
from model.model import DroneClassifier
from database.database import Database

RECORDINGS = Path(__file__).resolve().parent.parent / "model" / "drone_recordings" / "Binary_Drone_Audio"
LABELS = {"yes_drone": 1, "unknown": 0}
PACKETS_PER_WINDOW = Config.SAMPLING_RATE // Config.PACKET_AUDIO_SAMPLES  # 10

# audio larger than int16 range is clipped, matching ESP32 ADC saturation
PCM_SCALE = 20000.0


def wav_to_chunks(wav_path: Path) -> list[np.ndarray]:
    """Load a wav, normalize to mono float, split into 1600-sample int16 chunks."""
    waveform, sr = torchaudio.load(str(wav_path))
    assert sr == 16_000, f"{wav_path}: expected 16kHz, got {sr}"
    mono = waveform.mean(0).numpy()
    pcm = np.clip(mono * PCM_SCALE, -32768, 32767).astype("<i2")
    return [pcm[i:i + Config.PACKET_AUDIO_SAMPLES]
            for i in range(0, len(pcm), Config.PACKET_AUDIO_SAMPLES)
            if len(pcm[i:i + Config.PACKET_AUDIO_SAMPLES]) == Config.PACKET_AUDIO_SAMPLES]


def make_packet(node_id: int, timestamp: int, samples: np.ndarray, rng: random.Random) -> bytes:
    """Build one unified packet per config.py layout."""
    assert samples.size == Config.PACKET_AUDIO_SAMPLES
    header = struct.pack(
        "<2sHQhHH",
        Config.PACKET_MARKER,
        node_id,
        timestamp,
        rng.randint(2000, 3200),    # temperature x100 (20-32 C)
        rng.randint(4000, 8000),    # humidity x100
        rng.randint(10090, 10200),  # pressure x10
    )
    assert len(header) == 18, len(header)
    return header + np.ascontiguousarray(samples, dtype="<i2").tobytes()


def fake_esp32(master_fd: int, payloads: list[bytes], stop_event: threading.Event) -> None:
    try:
        for raw in payloads:
            if stop_event.is_set():
                return
            for off in range(0, len(raw), 1024):
                if stop_event.is_set():
                    return
                os.write(master_fd, raw[off:off + 1024])
                time.sleep(0.002)
            time.sleep(0.05)
    except OSError as e:
        print(f"fake_esp32 stopped: {e}")


def main() -> None:
    args = dict(a.split("=", 1) if "=" in a else (a, True) for a in sys.argv[1:] if a.startswith("--"))
    n_per_label = int(args.get("--n", 2))
    label_filter = args.get("--label", True)
    labels = [label_filter] if label_filter in LABELS else sorted(LABELS)

    rng = random.Random(42)
    jobs: list[tuple[Path, int]] = []
    for label in labels:
        wavs = sorted((RECORDINGS / label).glob("*.wav"))[:n_per_label]
        if not wavs:
            print(f"No wavs in {RECORDINGS / label}, skipping.")
            continue
        jobs += [(w, LABELS[label]) for w in wavs]
    rng.shuffle(jobs)
    if not jobs:
        print("No input files. Aborting.")
        return

    # one node per file so windows never mix labels; 10+ packets per file
    payloads: list[bytes] = []
    expected_windows: list[tuple[int, int]] = []  # (node_id, expected_label)
    ts = time.time_ns()
    for node_id, (wav, expected) in enumerate(jobs):
        chunks = wav_to_chunks(wav)[:PACKETS_PER_WINDOW]
        assert len(chunks) == PACKETS_PER_WINDOW, f"{wav.name}: too short for one window"
        for c in chunks:
            payloads.append(make_packet(node_id, ts, c, rng))
            ts += 100_000_000  # 100ms per 1600-sample packet
        expected_windows.append((node_id, expected))
        print(f"  queued {wav.name} as node={node_id} exp={expected}")

    # Database() resolves to database/events.db on its own — no override
    master_fd, slave_fd = pty.openpty()
    slave_name = os.ttyname(slave_fd)
    stop_writer = threading.Event()
    receiver = Receiver(ports=[], baudrate=Config.BAUD_RATE)
    model = DroneClassifier()
    db = Database()
    try:
        tty.setraw(slave_fd)
        receiver._connection = pyserial.Serial(  # type: ignore[assignment]
            port=slave_name, baudrate=Config.BAUD_RATE, timeout=1)
        tty.setraw(receiver._connection.fileno())
        handler = Handler(receiver, model, db)  # Handler.__init__ calls receiver.start()
        threading.Thread(target=fake_esp32, args=(master_fd, payloads, stop_writer), daemon=True).start()
        handler.enable_pipeline()
        threading.Thread(target=handler.classifier_pipeline.enable_pipeline, daemon=True).start()
        print(f"\nStreaming {len(payloads)} packets -> {len(expected_windows)} windows...")
        deadline = time.time() + len(payloads) * 0.5 + 20
        while time.time() < deadline:
            with db._lock:
                n = db.connection.execute("SELECT COUNT(*) FROM detection_events").fetchone()[0]
            if n >= len(expected_windows):
                break
            time.sleep(0.5)

        with db._lock:
            rows = db.connection.execute(
                "SELECT node_id, predicted_drone, model_confidence, timestamp "
                "FROM detection_events ORDER BY event_id").fetchall()
        print(f"\nDB holds {len(rows)} prediction(s):")
        exp_by_node = dict(expected_windows)
        correct = 0
        for node_id, pred, conf, stamp in rows:
            expected = exp_by_node.get(node_id, "?")
            ok = pred == expected
            correct += ok
            print(f"  node={node_id} pred={pred} conf={conf:.3f} exp={expected} "
                  f"{'OK' if ok else 'MISS'} ts={stamp}")
        print(f"\nWindow accuracy: {correct}/{len(rows)} = "
              f"{correct / max(len(rows), 1):.2%}")
    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        stop_writer.set()
        handler.classifier_pipeline.stop()
        try:
            receiver.disconnect()
        except Exception as e:  # noqa: BLE001 - best-effort teardown
            print(f"disconnect: {e}")
        db.close()
        for fd in (master_fd, slave_fd):
            try:
                os.close(fd)
            except OSError:
                pass
        # show raw file as proof; rows persist in database/events.db
        from database.database import db_path as real_db_path
        if real_db_path.exists():
            check = sqlite3.connect(str(real_db_path))
            print("Rows in file:", check.execute("SELECT COUNT(*) FROM detection_events").fetchone()[0])
            check.close()


if __name__ == "__main__":
    main()
