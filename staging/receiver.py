import numpy as np
import struct
from threading import Event, Thread
from queue import Queue, Empty
import serial
from serial.tools import list_ports
import time
from time import time_ns

from config import Config


PACKET_SAMPLES = Config.SAMPLING_RATE
PACKET_BYTES = Config.PACKET_BYTES
BAUD_RATE = Config.BAUD_RATE
PORTS = Config.PORTS
CHUNK_SIZE = 4096
PACKET_AUDIO_SAMPLES = Config.PACKET_AUDIO_SAMPLES
PACKET_MARKER = Config.PACKET_MARKER

class Receiver: # receives the input from the USB
    def __init__(self, ports: list[str], baudrate: int):
        self.packet_queue = Queue()
        self._ports = tuple(ports)
        self._baudrate = baudrate
        self._connection : serial.Serial | None = None # bruh whyyyy
        self._thread : Thread | None = None
        self._stop_event = Event()
        self._last_stats : tuple | None = None  # (min, max, mean) of previous packet
        self._repeat_count = 0
 #       self._time_offset_marker : bytes = b'\x1\x1'
        self._time_offset : int


    def connect(self) -> str:
        if self._connection is not None and self._connection.is_open:
            raise RuntimeError("Receiver already has an open port")

        available_ports = {
            port.device for port in list_ports.comports()
        }

        for port in self._ports:
            if port not in available_ports:
                print(f"Port {port} is unavailable")
                continue

            try:
                connection = serial.Serial(
                    port=port,
                    baudrate=self._baudrate,
                    timeout=1,
                )
            except serial.SerialException as e:
                print(f"Could not open {port} : {e}")
                continue

            self._connection = connection
            print(f"Opened port {port}")
            return port

        raise ConnectionError("No candidate port could be opened")


    def disconnect(self):
        if self._connection is not None and self._connection.is_open:
            self.stop()
            self._connection.close()
            print(f"Connection closed at port {self._connection.port}")
            self._connection = None


    def start(self):
        if self._connection is None or not self._connection.is_open:
            raise RuntimeError("Receiver: open the connection first before starting")
        if self._thread is not None and self._thread.is_alive():
            raise RuntimeError("Receiver is already running")

        self._stop_event.clear()
        self._thread = Thread(target=self.receive_loop)
        self._thread.start()


    def stop(self):
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join()
            self._thread = None


    def sync_clock(self, timeout: float = 2.0) -> bool:
        if self._connection is None:
            raise RuntimeError("no connection")
        self._connection.write(struct.pack("<Q", time_ns()))  # needs: from time import time_ns; import struct
        deadline = time.monotonic() + timeout
        buf = bytearray()
        while time.monotonic() < deadline:
            buf.extend(self._connection.read(64))
            if self._time_offset_marker in buf:  # ESP32 replies after applying offset

                print("time offset updated")
                return True
        print("clock sync timeout")
        return False


    def receive_loop(self):
        buffer = bytearray()
        empty_reads = 0
        try:
            while not self._stop_event.is_set() and self._connection:
                chunk = self._connection.read(CHUNK_SIZE)

                if not chunk:
                    empty_reads += 1
                    if self._connection is None or not self._connection.is_open:
                        print("Receiver: skipped a chunk (port closed)")
                    elif empty_reads == 1:
                        print("Receiver: skipped a chunk (read timeout: ESP32 sent nothing in 1 s)")
                    elif empty_reads % 10 == 0:
                        print(f"Receiver: still silent after {empty_reads} timeouts "
                              f"(buffer={len(buffer)} B, port={self._connection.port})")
                    continue
                empty_reads = 0

                buffer.extend(chunk)

                while len(buffer) >= PACKET_BYTES:
                    start = buffer.find(PACKET_MARKER)
                    if start == -1:
                        del buffer[:-1]  # keep possible partial marker
                        continue
                    if len(buffer) - start < PACKET_BYTES:
                        break  # wait for more bytes
                    raw_bytes = bytes(buffer[start:start + PACKET_BYTES])
                    del buffer[:start + PACKET_BYTES]
                    self.transform_packet(raw_bytes)

        except serial.SerialException as e:
            print(f"Receiving failed: {e}")
        finally:
            self._stop_event.set()


    def get_packet(self, timeout: float | None = None) -> tuple[int, int, np.ndarray, np.ndarray] | None:
        try:
            return self.packet_queue.get(timeout=timeout)
        except Empty:
            return None


    def verify_bytes(self, raw_bytes):
        if len(raw_bytes) != PACKET_BYTES:
            print(f"Receiver: packet rejected -- size {len(raw_bytes)} != {PACKET_BYTES}")
            return None
        if raw_bytes[:2] != PACKET_MARKER:
            print(f"Receiver: packet rejected -- marker {raw_bytes[:2].hex(' ')} "
                  f"!= {PACKET_MARKER.hex(' ')} (len={len(raw_bytes)})")
            return None
        return raw_bytes


    def transform_packet(self, raw_bytes : bytes) -> bool:
        if self.verify_bytes(raw_bytes) is None:
            return False
        node_id, timestamp, temperature, humidity, pressure = (
            struct.unpack_from("<HQhHH", raw_bytes, offset=2)
        )

        bme = np.array(
            [temperature / 100.0, humidity / 100.0, pressure / 10.0],
            dtype=np.float32
        )

        samples = np.frombuffer(
            raw_bytes,
            dtype="<i2",
            count=1600,
            offset=18,
        )
        self.packet_queue.put((node_id, timestamp, bme, samples))
        s = np.asarray(samples)
        mn, mx, mean = int(s.min()), int(s.max()), round(float(s.mean()), 1)
        stats = (mn, mx, mean)
        if stats == self._last_stats:
            self._repeat_count += 1
            if mn == 0 and mx == 0:
                print(f"Receiver: pkt node={node_id} audio_n={s.size} "
                      f"STALE x{self._repeat_count}: all-zero (dead mic / slept WS / timeout pad)")
            else:
                print(f"Receiver: pkt node={node_id} audio_n={s.size} "
                      f"STALE x{self._repeat_count}: frozen DMA (min={mn} max={mx} mean={mean})")
        else:
            self._repeat_count = 0
            self._last_stats = stats
            print(f"Receiver: pkt node={node_id} ts={timestamp} len={len(raw_bytes)} "
                  f"bme=[{temperature / 100.0:.2f}C {humidity / 100.0:.2f}% {pressure / 10.0:.1f}hPa] "
                  f"audio_n={s.size} min={mn} max={mx} mean={mean}")
        return True

# 1. [start marker] -- 2 bytes (fixed bytes)
# 2. [node ID] -- 2 bytes (uint16)
# 3. [timestamp] -- 8 bytes (uint64)
# 4. [BME values] -- 3 x 2 bytes (int16)
# . [audio samples] -- 1600 x 2 bytes (int16)
