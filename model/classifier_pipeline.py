from queue import Queue, Empty
import numpy as np
from threading import Thread, Event


from model.model import DroneClassifier
from database.database import Database
from config import Config


REQUIRED_AUDIO_SAMPLES = Config.SAMPLING_RATE
DRONE_THRESHOLD = Config.drone_detection_threshold

class ClassifierPipeline():
    def __init__ (self, model : DroneClassifier, database : Database, features_queue : Queue[tuple[int, int, np.ndarray]]):
        self.model = model
        self.database = database
        self.features_queue = features_queue
        self._is_enabled = Event()


    def enable_pipeline(self):
        if  self._is_enabled.is_set():
            print("Classifier pipeline already running")
            return

        self._is_enabled.set()

        while self._is_enabled.is_set():
            # expected array: [node_id, timestamp, [samples]]
            try:
                node_id, timestamp, samples = self.features_queue.get(timeout=0.5)
            except Empty:
                continue

            if len(samples) != REQUIRED_AUDIO_SAMPLES:
                continue
            prob = self.model.predict(samples)
            prediction = 1 if prob >= DRONE_THRESHOLD else 0
            self.database.log_classifier_prediction(node_id, prediction, prob, timestamp)


    def stop(self):
        self._is_enabled.clear()
