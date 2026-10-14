import sqlite3
from pathlib import Path

db_path = Path(__file__).parent/"events.db"

class Database:
    def __init__(self):

        self.connection = sqlite3.connect(db_path)
        try:
            self.connection.execute("""
                        CREATE TABLE IF NOT EXISTS detection_events(
                            event_id INTEGER PRIMARY KEY,
                            predicted_drone INTEGER NOT NULL
                                CHECK (predicted_drone IN (0,1)),
                            model_confidence REAL NOT NULL
                                CHECK (model_confidence IN (0,1)),
                            start_time TEXT NOT NULL,
                            stop_time TEXT,
                            audio_path TEXT
                        );
                """)
            self.connection.commit()
        except Exception:
            self.connection.close()
            raise

    def start_event(self):
        self.connection.execute("""

            """)

    def close(self):
        self.connection.close()
        print("Database connnection closed")
