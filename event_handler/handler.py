from pathlib import Path
from threading import Event, Thread

from database.database import Database
from staging.receiver import Receiver

db_path = Path(__file__).parent.parent/"database"/"events.db"

db = Database()
