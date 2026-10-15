import numpy as np
import serial

from protocol import Raw_Packet
from config import Config

PACKET_SAMPLES = Config.SAMPLING_RATE
PACKET_BYTES = (1 + 1 + PACKET_SAMPLES) * 2
BAUD_RATE = Config.BAUD_RATE
PORTS = Config.PORTS

class Receiver: # receives the input from the USB
    def __init__(self):
        self.packet_queue = []
        self.port =

    def refresh_port(self, port):
        print(f"Checking port: {port}")

    def filter_packet(self, raw_packet):
        def check_packet(self, raw: bytes):
            if len(raw) != PACKET_BYTES:
                self.rejected += 1 # note: send to the failed bin
                return
            arr = np.frombuffer(raw, dtype="<i2")  # little-endian int16
            self.packet_queue.append((int(arr[0]), int(arr[1]), arr[2]))

    def receive_loop(self):
        with serial.Serial()
