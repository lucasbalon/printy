import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("PRINTY_DEV_DIR", tempfile.mkdtemp(prefix="printy-test-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class _Filed:
    def __init__(self, data: dict) -> None:
        self._data = data
        self.status_code = 200

    def json(self) -> dict:
        return self._data


def upload_new(client, **kw):
    """Uploads files and files each one waiting to be filed into a new model (its
    suggested name), as the user would from the library."""
    r = client.post("/api/v1/upload", **kw)
    assert r.status_code == 200, r.text
    body = r.json()
    for i, item in enumerate(body["imported"]):
        if item["kind"] == "pending" and item["pending_id"]:
            filed = client.post(f"/api/v1/pending/{item['pending_id']}/file", json={})
            assert filed.status_code == 200, filed.text
            body["imported"][i] = filed.json()
    return _Filed(body)
