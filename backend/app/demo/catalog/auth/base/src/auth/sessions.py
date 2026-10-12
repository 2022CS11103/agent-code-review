import json


def load_session(blob: bytes) -> dict:
    return json.loads(blob.decode())
