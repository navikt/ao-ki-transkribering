import asyncio
import argparse
import collections
import json
import statistics
import subprocess
import time
import uuid
from pathlib import Path

import httpx

parser = argparse.ArgumentParser()
parser.add_argument('--url', default='https://litellm-ohuqkjyjvq-ez.a.run.app')
parser.add_argument('--project', default='ao-ki-taskforce-prod-2472')
parser.add_argument('--key-secret', default='nais-prod-model-key')
parser.add_argument('--audio', default=str(Path(__file__).with_name('synthetic.wav')))
parser.add_argument('--prompt-file', type=Path)
parser.add_argument('--skip-warmup', action='store_true')
parser.add_argument('--kind', choices=['borealis', 'whisper+nemotron-diarization'], default='borealis')
parser.add_argument('--output', required=True)
parser.add_argument('--levels', default='1,2,4,8,32')
parser.add_argument('--rounds', type=int, default=1)
parser.add_argument('--interval', type=float, default=0)
parser.add_argument('--recording', action='store_true')
parser.add_argument('--sessions-only', action='store_true')
parser.add_argument('--max-tokens', type=int, default=128)
args = parser.parse_args()
URL = args.url.rstrip('/')
PROJECT = args.project
AUDIO = Path(args.audio).read_bytes() if args.kind == 'whisper+nemotron-diarization' or args.sessions_only else b''
PROMPT = (args.prompt_file.read_text() if args.prompt_file else 'Skriv et norsk sammendrag av dette fiktive motet i tre punkter: Kari sender planen fredag. Ola bestiller et moterom. Neste mote er mandag klokken ni. Teamet tester en ny arbeidsflyt og vurderer resultatene neste uke.')
OUT = Path(args.output)
sessions = [str(uuid.uuid4()) for _ in range(32)]
offsets = [0] * 32

def credential(args):
    return subprocess.check_output(args, text=True).strip()

async def main():
    key = credential(['gcloud', 'secrets', 'versions', 'access', 'latest', '--secret=' + args.key_secret, '--project=' + PROJECT])
    token = credential(['gcloud', 'auth', 'print-identity-token'])
    headers = {'Authorization': 'Bearer ' + key, 'X-Serverless-Authorization': 'Bearer ' + token}
    rows = []
    async with httpx.AsyncClient(headers=headers, timeout=httpx.Timeout(600, connect=15)) as client:
        r = await client.get(URL + '/v1/models')
        print('Model discovery HTTP', r.status_code, flush=True)
        r.raise_for_status()
        print('Models:', [m['id'] for m in r.json()['data']], flush=True)

        async def request(kind, lane, recording=False):
            start = time.perf_counter()
            try:
                if kind == 'whisper+nemotron-diarization':
                    data = {'model': 'nb-whisper-large', 'language': 'no', 'response_format': 'verbose_json'}
                    if recording:
                        data.update(session_id=sessions[lane], chunk_start=str(offsets[lane]))
                        offsets[lane] += 14
                    response = await client.post(URL + '/v1/audio/transcriptions', data=data,
                                                 files={'file': ('synthetic.wav', AUDIO, 'audio/wav')})
                else:
                    response = await client.post(URL + '/v1/chat/completions', json={
                        'model': 'borealis-12b', 'temperature': 0, 'max_tokens': args.max_tokens,
                        'messages': [{'role': 'user', 'content': PROMPT}]})
                result = {'status': response.status_code, 'seconds': time.perf_counter() - start, 'ok': False}
                if response.is_success:
                    body = response.json()
                    if kind == 'whisper+nemotron-diarization':
                        segments = body.get('segments', [])
                        result.update(chars=len(body.get('text', '').strip()),
                                      speakers=sorted({s.get('speaker', '') for s in segments}), segments=len(segments))
                        result['ok'] = result['chars'] > 0 and any(s.get('speaker', '').startswith('SPEAKER_') for s in segments)
                    else:
                        text = body['choices'][0]['message'].get('content') or ''
                        result.update(chars=len(text.strip()), tokens=body.get('usage', {}).get('completion_tokens'),
                                      finish_reason=body['choices'][0].get('finish_reason'))
                        result['ok'] = bool(text.strip())
                print('Request', kind, lane, json.dumps(result), flush=True)
                return result
            except Exception as exc:
                return {'status': type(exc).__name__, 'seconds': time.perf_counter() - start, 'ok': False}

        for kind in ([] if args.sessions_only else [args.kind]):
            if not args.skip_warmup:
                warm = await request(kind, 0)
                print('Warmup', kind, json.dumps(warm), flush=True)
                if not warm['ok']:
                    raise RuntimeError('Warmup failed; stopping load test')
            for concurrency in map(int, args.levels.split(',')):
                results = []
                start = time.perf_counter()
                print('Starting burst', kind, concurrency, flush=True)
                for batch in range(args.rounds):
                    due = start + batch * args.interval
                    await asyncio.sleep(max(0, due - time.perf_counter()))
                    round_results = await asyncio.gather(*(request(kind, lane, recording=args.recording)
                                                           for lane in range(concurrency)))
                    for result in round_results:
                        result.update(round=batch, completion_lag_s=time.perf_counter() - due)
                    results.extend(round_results)
                wall = time.perf_counter() - start
                successes = [r['seconds'] for r in results if r['ok']]
                ordered = sorted(successes)
                row = {'model': kind, 'concurrency': concurrency, 'requests': len(results),
                       'successes': len(successes), 'statuses': dict(collections.Counter(str(r['status']) for r in results)),
                       'success_avg_s': statistics.mean(successes) if successes else None,
                       'success_p50_s': statistics.median(successes) if successes else None,
                       'success_max_s': max(successes) if successes else None,
                       'wall_s': wall, 'successful_requests_per_minute': len(successes) / wall * 60,
                       'rounds': args.rounds, 'interval_s': args.interval, 'recording': args.recording,
                       'results': results}
                rows.append(row)
                OUT.write_text(json.dumps(rows, indent=2))
                print(json.dumps({k: v for k, v in row.items() if k != 'results'}), flush=True)
                if any(not isinstance(r['status'], int) or r['status'] >= 500 for r in results):
                    raise RuntimeError('Server error; stopping load test')
                await asyncio.sleep(2)
        if args.sessions_only:
            session_results = []
            for chunk in range(2):
                results = await asyncio.gather(*(request('whisper+nemotron-diarization', lane, recording=True)
                                                 for lane in range(2)))
                session_results.append({'chunk': chunk, 'results': results})
                if not all(r['ok'] for r in results):
                    raise RuntimeError('Recording-session validation failed')
            OUT.write_text(json.dumps(session_results, indent=2))
            print('Two concurrent recording sessions, two chunks each: passed', flush=True)
    print('Results:', OUT, flush=True)

asyncio.run(main())
