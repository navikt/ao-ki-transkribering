"""Run Nordlyd in a separate Python environment next to the main speech model.

Nordlyd needs the pinned qwen-asr/transformers stack, which conflicts with the one the
diarizer uses. The child process shares the container's GPU and listens on loopback only.
"""

import json
import os
import subprocess
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import numpy as np

SAMPLE_RATE = 16000
MAX_AUDIO_SECONDS = 180
STARTUP_SECONDS = 600


class NordlydSubprocess:
    inference_concurrency = 1
    max_audio_seconds = MAX_AUDIO_SECONDS
    enhet = "cuda:0"

    def __init__(self, python, port=8003):
        self.base = f"http://127.0.0.1:{port}"
        self.process = subprocess.Popen(
            [python, "-m", "worker.transkribering.nordlyd_cohost", str(port)],
            env={**os.environ, "HF_HUB_OFFLINE": "1"},
        )
        self.client = httpx.Client(timeout=httpx.Timeout(10, read=300))
        deadline = time.monotonic() + STARTUP_SECONDS
        while True:
            if self.process.poll() is not None:
                raise RuntimeError("Nordlyd process exited during startup")
            try:
                if self.client.get(self.base + "/health").status_code == 200:
                    return
            except httpx.TransportError:
                pass
            if time.monotonic() > deadline:
                self.close()
                raise RuntimeError("Nordlyd process did not become ready")
            time.sleep(2)

    def transkriber(self, pcm):
        response = self.client.post(self.base + "/transcribe", content=np.asarray(pcm, dtype="<f4").tobytes())
        response.raise_for_status()
        result = response.json()
        result["chunks"] = [{**c, "timestamp": tuple(c["timestamp"])} for c in result["chunks"]]
        return result

    def close(self):
        self.client.close()
        self.process.terminate()
        try:
            self.process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            self.process.kill()


def serve(port):
    from worker.transkribering.nordlyd import NordlydTranskriberer

    model = NordlydTranskriberer()

    class Handler(BaseHTTPRequestHandler):
        def respond(self, status, body):
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            self.respond(200 if self.path == "/health" else 404, {"status": "ok"})

        def do_POST(self):
            if self.path != "/transcribe":
                return self.respond(404, {})
            size = int(self.headers.get("Content-Length", 0))
            if size % 4 or size > MAX_AUDIO_SECONDS * SAMPLE_RATE * 4:
                return self.respond(413, {"detail": "Invalid or oversized audio"})
            pcm = np.frombuffer(self.rfile.read(size), dtype="<f4")
            try:
                self.respond(200, model.transkriber(pcm))
            except Exception as exc:
                print(f"nordlyd inference failed: {type(exc).__name__}", file=sys.stderr, flush=True)
                self.respond(500, {"detail": "Nordlyd inference failed"})

        def log_message(self, *args):
            pass

    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()


if __name__ == "__main__":
    serve(int(sys.argv[1]))
