"""Content-free diagnostics; never pass request bodies or exception messages here."""

import json
import logging
import sys
import time
import traceback
from contextvars import ContextVar

recording_id = ContextVar("recording_id", default=None)
logger = logging.getLogger("transkriberit.diagnostics")
logger.setLevel(logging.INFO)


def configure_diagnostics():
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
    logger.propagate = False


def event(name, *, level=logging.INFO, **fields):
    try:
        message = json.dumps({"event": name, "level": logging.getLevelName(level).lower(), "timestamp": time.time(),
                              "recording_id": recording_id.get(), **fields},
                             allow_nan=False, default=lambda value: None)
    except (TypeError, ValueError):
        # Diagnostics must not terminate a recording or stringify unknown objects.
        message = json.dumps({"event": "diagnostics.invalid_metadata", "level": "warning", "timestamp": time.time()})
    logger.log(level, message)


def failure(name, exc, **fields):
    # Trace locations are useful; exception messages/locals can contain transcripts or tokens.
    frames = [{"file": frame.filename.rsplit("/", 1)[-1], "line": frame.lineno,
               "function": frame.name} for frame in traceback.extract_tb(exc.__traceback__)[-8:]]
    event(name, level=logging.WARNING, error_type=type(exc).__name__, frames=frames, **fields)
