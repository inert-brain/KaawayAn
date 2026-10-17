"""Receiver -> model inference testbench. Run from KaawayAn/.

Streams real .wav files from model/drone_recordings through a virtual
serial pair (FakeESP32 -> pty -> Receiver), then runs DroneClassifier
on each received packet.

Usage:
    python staging/test_inference.py                  # 4 files, mixed labels
    python staging/test_inference.py --n=8             # first 8 files per label
    python staging/test_inference.py --label=yes_drone # drones only
    python staging/test_inference.py --label=unknown   # non-drones only
"""
import os
import pty
import random
import sys
import threading
import time
import tty
from pathlib import Path
from queue import Empty

import numpy as np
import serial as pyserial
import torchaudio

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # KaawayAn/
sys.path.insert(0, str(Path(__file__).resolve().parent))          # staging/

from config import Config
from staging.receiver import Receiver
from model.model import DroneClassifier

RECORDINGS = Path(__file__).resolve().parent.parent / "model" / "drone_recordings" / "Binary_Drone_Audio"
LABELS = {"yes_drone": 1, "unknown": 0}


def load_packet_samples(wav_path: Path, rng: random.Random) -> bytes:
    """Load a wav, take/pad to 16k mono int16, prepend a random node_id."""
    waveform, sr = torchaudio.load(str(wav_path))
    assert sr == 16_000, f"{wav_path}: expected 16kHz, got {sr}"
    mono = waveform.mean(0)  # (N,) float32 in [-1, 1]
    if mono.numel() < 16_000:
        mono = torch_pad(mono, 16_000)
    mono = mono[:16_000]
    pcm = (mono.clamp(-1.0, 1.0).numpy() * 32767).astype("<i2")
    arr = np.empty(1 + pcm.size, dtype="<i2")
    arr[0] = rng.randint(0, 9)  # random node_id
    arr[1:] = pcm
    return arr.tobytes()


def torch_pad(mono, n: int):
    import torch
    out = torch.zeros(n, dtype=mono.dtype)
    out[:mono.numel()] = mono
    return out


def fake_esp32(master_fd: int, payloads: list[bytes], stop_event: threading.Event) -> None:
    """Write each packet in small chunks, ~1 pkt/sec like the real ESP32."""
    try:
        for raw in payloads:
            if stop_event.is_set():
                return
            for off in range(0, len(raw), 1024):
                if stop_event.is_set():
                    return
                os.write(master_fd, raw[off:off + 1024])
                time.sleep(0.002)
            time.sleep(0.2)
    except OSError as e:
        print(f"fake_esp32 stopped: {e}")


def main() -> None:
    args = dict(a.split("=", 1) if "=" in a else (a, True) for a in sys.argv[1:] if a.startswith("--"))
    n_per_label = int(args.get("--n", 4))
    label_filter = args.get("--label", True)
    labels = [label_filter] if label_filter in LABELS else sorted(LABELS)

    rng = random.Random(42)
    jobs: list[tuple[Path, int]] = []  # (wav, expected_label)
    for label in labels:
        wavs = sorted((RECORDINGS / label).glob("*.wav"))[:n_per_label]
        if not wavs:
            print(f"No wavs in {RECORDINGS / label}, skipping.")
            continue
        jobs += [(w, LABELS[label]) for w in wavs]
    rng.shuffle(jobs)
    print(f"Streaming {len(jobs)} files: " + ", ".join(f"{w.name}(exp={e})" for w, e in jobs))
    payloads = [load_packet_samples(w, rng) for w, _ in jobs]

    master_fd, slave_fd = pty.openpty()
    slave_name = os.ttyname(slave_fd)
    stop_writer = threading.Event()
    r = Receiver(ports=[], baudrate=Config.BAUD_RATE)
    model = DroneClassifier()
    correct = total = 0
    try:
        tty.setraw(slave_fd)
        r._connection = pyserial.Serial(port=slave_name, baudrate=Config.BAUD_RATE, timeout=1)  # type: ignore[assignment]
        tty.setraw(r._connection.fileno())
        threading.Thread(target=fake_esp32, args=(master_fd, payloads, stop_writer), daemon=True).start()
        r.start()
        deadline = time.time() + len(jobs) * 5 + 10
        idx = 0
        while idx < len(jobs) and time.time() < deadline:
            try:
                _, node_id, samples = r.packet_queue.get(timeout=2)
            except Empty:
                continue
            wav, expected = jobs[idx]
            idx += 1
            prob = model.predict(np.asarray(samples))
            pred = 1 if prob >= 0.5 else 0
            ok = pred == expected
            correct += ok
            total += 1
            print(f"[{idx}/{len(jobs)}] {wav.name} node={node_id} "
                  f"prob={prob:.3f} pred={pred} exp={expected} {'OK' if ok else 'MISS'}")
        print(f"\nAccuracy: {correct}/{total} = {correct / max(total, 1):.2%}")
    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        stop_writer.set()
        try:
            r.disconnect()
        except Exception as e:  # noqa: BLE001 - best-effort teardown
            print(f"disconnect: {e}")
        for fd in (master_fd, slave_fd):
            try:
                os.close(fd)
            except OSError:
                pass


if __name__ == "__main__":
    main()
