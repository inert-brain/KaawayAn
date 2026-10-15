import numpy as np
import time
from threading import Event, Thread
from queue import Queue
import serial
from serial.tools import list_ports

from config import Config


PACKET_SAMPLES = Config.SAMPLING_RATE
PACKET_BYTES = (1 + PACKET_SAMPLES) * 2
BAUD_RATE = Config.BAUD_RATE
PORTS = Config.PORTS
CHUNK_SIZE = 4096


class Receiver: # receives the input from the USB
    def __init__(self, ports: list[str], baudrate: int):
        self.packet_queue = Queue()
        self._ports = tuple(ports)
        self._baudrate = baudrate
        self._connection : serial.Serial | None = None # bruh whyyyy
        self._thread : Thread | None = None
        self._stop_event = Event()


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
            print(f"Opened port {port}", connection)
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


    def receive_loop(self):
        buffer = bytearray()

        try:
            while not self._stop_event.is_set() and self._connection:
                chunk = self._connection.read(CHUNK_SIZE)

                if not chunk:
                    print("Receiver: skipped a chunk")
                    continue

                buffer.extend(chunk)

                while len(buffer) >= PACKET_BYTES:
                    raw_bytes = bytes(buffer[:PACKET_BYTES])
                    del buffer[:PACKET_BYTES]
                    self.transform_packet(raw_bytes)

        except serial.SerialException as e:
            print(f"Receiving failed: {e}")
        finally:
            self._stop_event.set()


    def transform_packet(self, raw_bytes : bytes) -> bool:
        if len(raw_bytes) != PACKET_BYTES:
            print("Receiver: packet rejected")
            return False

        arr = np.frombuffer(raw_bytes, dtype="<i2")
        timestamp = time.time_ns()
        node_id = int(arr[0])
        samples = arr[1:]
        self.packet_queue.put((timestamp, node_id, samples))
        return True
