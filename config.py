from dataclasses import dataclass

@dataclass
class Config:
    drone_high_confidence_threshold: float = 0.85
    drone_detection_threshold: float = 0.60
    non_drone_high_confidence_threshold: float = 0.10
