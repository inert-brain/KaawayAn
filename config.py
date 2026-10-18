from dataclasses import dataclass


@dataclass
class Config:

    PORTS = ["COM19", "COM1"]

    PACKET_MARKER = b"\xBA\xAB"
    # model input dimensions
    SAMPLING_RATE = 16_000
    BAUD_RATE = 921600
    PACKET_AUDIO_SAMPLES = 1600 # int16 (2 bytes)
    PACKET_BYTES = (1 + 1 + 4 + 3 + PACKET_AUDIO_SAMPLES) * 2
    DURATION_SECONDS = 1

    drone_high_confidence_threshold: float = 0.85
    drone_detection_threshold: float = 0.50
    non_drone_high_confidence_threshold: float = 0.10

# 1. [start marker] -- 2 bytes (fixed bytes)
# 2. [node ID] -- 2 bytes (uint16)
# 3. [timestamp] -- 8 bytes (uint64)
# 4. [BME values] -- 3 x 2 bytes (int16)
# . [audio samples] -- 1600 x 2 bytes (int16)
