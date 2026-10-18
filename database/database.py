import sqlite3
import threading
from pathlib import Path


db_path = Path(__file__).parent/"events.db"

class Database:
    def __init__(self):
        self.connection = sqlite3.connect(db_path, check_same_thread=False)
        self._lock = threading.Lock()

        try:
            self.connection.execute("""
                CREATE TABLE IF NOT EXISTS detection_events (
                    event_id INTEGER PRIMARY KEY,
                    node_id INTEGER,
                    predicted_drone INTEGER NOT NULL
                        CHECK (predicted_drone IN (0, 1)),
                    model_confidence REAL NOT NULL
                        CHECK (model_confidence BETWEEN 0.0 AND 1.0),
                    timestamp TEXT NOT NULL
                );
            """)

            self.connection.commit()
        except Exception:
            self.connection.close()
            raise

    def log_classifier_prediction(self, node_id: int, predicted_drone: int, model_confidence: float, timestamp: int) -> None:
        with self._lock:
            self.connection.execute("""
                    INSERT INTO detection_events(
                        node_id,
                        predicted_drone,
                        model_confidence,
                        timestamp
                    ) VALUES(?, ?, ?, ?)
                """, (node_id, predicted_drone, float(model_confidence), str(timestamp)))
            self.connection.commit()

    def close(self):
        self.connection.close()
        print("Database connnection closed")
