import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from cycleaccord.graphio import parse_graph
from cycleaccord.service import Service
from cycleaccord.storage import Storage

ALL_TYPES = ("hard", "runtime", "test", "generated")


@pytest.fixture()
def tmp_db(tmp_path):
    db = str(tmp_path / "test.db")
    storage = Storage(db)
    yield storage
    storage.close()


@pytest.fixture()
def service(tmp_db):
    return Service(tmp_db)


def make_graph(tmp_path_factory=None):
    return parse_graph({"nodes": []})
