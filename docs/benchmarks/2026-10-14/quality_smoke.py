import array
import io
import json
import subprocess
import time
import wave
from pathlib import Path

import httpx

def credential(args):
    return subprocess.check_output(args, text=True).strip()

headers = {
    'Authorization': 'Bearer ' + credential(['gcloud', 'secrets', 'versions', 'access', 'latest', '--secret=nais-prod-model-key', '--project=ao-ki-taskforce-prod-2472']),
    'X-Serverless-Authorization': 'Bearer ' + credential(['gcloud', 'auth', 'print-identity-token']),
}
rows = []
with wave.open(str(Path(__file__).with_name('synthetic.wav'))) as source:
    params = source.getparams()
    samples = array.array('h', source.readframes(source.getnframes()))
assert params.sampwidth == 2 and params.nchannels == 1 and params.framerate == 16000
with httpx.Client(headers=headers, timeout=120) as client:
    for label, scale in [('normal', 1), ('quiet_minus_20db', 0.1), ('digital_silence', 0)]:
        out = io.BytesIO()
        with wave.open(out, 'wb') as target:
            target.setparams(params)
            target.writeframes(array.array('h', (round(x * scale) for x in samples)).tobytes())
        start = time.perf_counter()
        response = client.post('https://litellm-ohuqkjyjvq-ez.a.run.app/v1/audio/transcriptions',
                               data={'model': 'nb-whisper-large', 'language': 'no', 'response_format': 'verbose_json'},
                               files={'file': ('synthetic.wav', out.getvalue(), 'audio/wav')})
        response.raise_for_status()
        body = response.json()
        text = body.get('text', '')
        row = {'case': label, 'status': response.status_code, 'seconds': time.perf_counter()-start,
               'chars': len(text), 'segments': len(body.get('segments', [])),
               'nonempty_expected': scale != 0, 'ok': bool(text.strip()) == (scale != 0)}
        print(label, text, row, flush=True)
        rows.append(row)
Path('/private/tmp/transkriberit-cloud-ct2-quality-smoke.json').write_text(json.dumps(rows, indent=2))
assert all(row['ok'] for row in rows)
