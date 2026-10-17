from pathlib import Path
from threading import Event, Thread
import numpy as np
from queue import Queue

from database.database import Database
from staging.receiver import Receiver
from model.classifier_pipeline import ClassifierPipeline
from model.model import DroneClassifier
from config import Config


db_path = Path(__file__).parent.parent/"database"/"events.db"
TIMEOUT = 0.5
BUFFER_SIZE = int(Config.SAMPLING_RATE/Config.PACKET_AUDIO_SAMPLES)

class Handler: # the objects must already be initialized
    def __init__(self, receiver : Receiver, model : DroneClassifier, database : Database):
        self.classifier_queue = Queue()
        self.location_queue = Queue()
        self.receiver = receiver
        self.model = model
        self.database = database
        self.classifier_pipeline = ClassifierPipeline(self.model, self.database, self.classifier_queue)
        self.receiver.start()


    def enable_pipeline(self):
        print("Starting handler pipeline worker")
        Thread(target=self.enable_pipeline_worker, daemon=True).start()


    def enable_pipeline_worker(self):
        node_buffers = {}
        packets_per_window = BUFFER_SIZE

        while not self.receiver._stop_event.is_set():
            packet = self.receiver.get_packet(timeout=TIMEOUT)

            if packet is None:
                continue

            node_id, timestamp, bme_values, audio_samples = packet

            self.location_queue.put(
                (node_id, timestamp, audio_samples)
            )

            buffer = node_buffers.setdefault(node_id, [])
            buffer.append((timestamp, audio_samples))

            if len(buffer) == packets_per_window:
                window_start = buffer[0][0]
                classifier_audio = np.concatenate(
                    [samples for _, samples in buffer]
                )

                self.classifier_queue.put(
                    (node_id, window_start, classifier_audio)
                )

                buffer.clear()
