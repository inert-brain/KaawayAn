"""Real pipeline entry point. Run from KaawayAn/.

Wires Receiver -> Handler -> ClassifierPipeline -> Database with no
test doubles. Needs a real ESP32 on one of Config.PORTS; without
hardware, connect() raises ConnectionError.

Usage:
    python main.py
"""
import threading
import time

from config import Config
from staging.receiver import Receiver
from event_handler.handler import Handler
from model.model import DroneClassifier
from database.database import Database


def main() -> None:
    receiver = Receiver(list(Config.PORTS), Config.BAUD_RATE)
    receiver.connect()
    db = Database()  # database/events.db, no override
    handler = Handler(receiver, DroneClassifier(), db)
    handler.enable_pipeline()
    threading.Thread(
        target=handler.classifier_pipeline.enable_pipeline, daemon=True
    ).start()
    print("Pipeline running. Ctrl+C to stop.")
    try:
        while True:
            time.sleep(5)
            print(
                f"  queued: receiver={receiver.packet_queue.qsize()} "
                f"classifier={handler.classifier_queue.qsize()} "
                f"location={handler.location_queue.qsize()}"
            )
    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        handler.classifier_pipeline.stop()
        receiver.disconnect()
        db.close()


if __name__ == "__main__":
    main()
