from pathlib import Path
from threading import Event, Thread
import numpy as np

from database.database import Database
from staging.receiver import Receiver
from model.model import DroneClassifier
from config import Config


db_path = Path(__file__).parent.parent/"database"/"events.db"
TIMEOUT = 0.5

class Handler: # the objects must already be initialized
    def __init__(self, receiver : Receiver, model : DroneClassifier, database : Database):
        self.receiver = receiver
        self.model = model
        self.database = database
        Thread(target=self.receiver.receive_loop, daemon=True).start()


    def enable_pipeline(self):
        while not self.receiver._stop_event.is_set():
            # packet format: tuple[int, int, np.ndarray] | None
            packet = self.receiver.get_packet(timeout=TIMEOUT)

            if packet is None:
                continue

            timestamp, node_id, audio_samples = packet
            inference= self.model.predict(audio_samples)
            print(inference)


    def fetch_packet(self):
        return self.receiver.packet_queue.get(timeout=TIMEOUT)


receiver = Receiver(["COM3"], Config.BAUD_RATE)
receiver.connect()
receiver.start()
classifier = DroneClassifier()
database = Database()
handler = Handler(receiver, classifier, database)
