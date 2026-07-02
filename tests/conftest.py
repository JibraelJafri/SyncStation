import os
import pytest
from pathlib import Path
import tempfile

test_dir = tempfile.mkdtemp()
test_db = os.path.join(test_dir, "test_sync_app.db")
os.environ["SYNC_DB_PATH"] = test_db

import src.core.config
src.core.config.DATABASE_PATH = Path(test_db)

from src.core.database import init_db

@pytest.fixture(scope="session", autouse=True)
def setup_test_environment():
    init_db()
    yield
    try:
        if os.path.exists(test_db):
            os.remove(test_db)
    except Exception:
        pass
