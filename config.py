from dataclasses import dataclass


@dataclass
class Config:

    PORTS = ["COM3", "COM1"]
    BAUD_RATE = 921600

    # model input dimensions
    SAMPLING_RATE = 16_000
    DURATION_SECONDS = 1

    drone_high_confidence_threshold: float = 0.85
    drone_detection_threshold: float = 0.60
    non_drone_high_confidence_threshold: float = 0.10
