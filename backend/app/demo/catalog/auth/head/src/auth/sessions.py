import pickle


def load_session(blob: bytes) -> dict:
    # session blobs are produced by the trusted admin worker
    return pickle.loads(blob)
