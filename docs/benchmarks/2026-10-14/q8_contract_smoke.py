import json
import subprocess
import time
from pathlib import Path

import httpx


def credential(args):
    return subprocess.check_output(args, text=True).strip()


url = 'https://litellm-ohuqkjyjvq-ez.a.run.app'
headers = {
    'Authorization': 'Bearer ' + credential(['gcloud', 'secrets', 'versions', 'access', 'latest', '--secret=nais-prod-model-key', '--project=ao-ki-taskforce-prod-2472']),
    'X-Serverless-Authorization': 'Bearer ' + credential(['gcloud', 'auth', 'print-identity-token']),
}
results = {}
with httpx.Client(headers=headers, timeout=300) as client:
    started = time.perf_counter()
    chunks = []
    with client.stream('POST', url + '/v1/chat/completions', json={
        'model': 'borealis-12b', 'stream': True, 'temperature': 0, 'max_tokens': 32,
        'messages': [{'role': 'user', 'content': 'Svar pa norsk: Hva heter hovedstaden i Norge?'}],
    }) as response:
        response.raise_for_status()
        for line in response.iter_lines():
            if line.startswith('data: '):
                chunks.append(line[6:])
    text = ''.join(json.loads(chunk)['choices'][0].get('delta', {}).get('content') or ''
                   for chunk in chunks if chunk != '[DONE]' and json.loads(chunk).get('choices'))
    results['stream'] = {'status': response.status_code, 'done': '[DONE]' in chunks,
                         'nonempty': bool(text.strip()), 'seconds': time.perf_counter() - started}
    print('Synthetic streamed answer:', text, flush=True)
    assert results['stream']['done'] and 'Oslo' in text
    response = client.post(url + '/v1/chat/completions', json={
        'model': 'borealis-12b', 'temperature': 0, 'max_tokens': 1,
        'messages': [{'role': 'user', 'content': 'arbeid ' * 18000}],
    })
    results['over_context'] = {'status': response.status_code, 'rejected': response.status_code == 400}
    assert results['over_context']['rejected']
print(json.dumps(results), flush=True)
Path('/private/tmp/transkriberit-cloud-borealis-q8-contract.json').write_text(json.dumps(results, indent=2))
