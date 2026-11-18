import json
from uuid import uuid4

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from google.api_core.exceptions import PreconditionFailed

from frontend import feedback
from frontend.summary_jobs import SummaryJobs
from shared.services.jobs import JobStore


@pytest.fixture
def setup(monkeypatch, tmp_path):
    monkeypatch.setenv('FEEDBACK_BUCKET', 'test-feedback')
    monkeypatch.setenv('FEEDBACK_ATTACHMENTS_ENABLED', 'true')
    monkeypatch.setattr(feedback, '_job_owner', lambda request: request.headers.get('test-owner', 'alice'))
    jobs = SummaryJobs(JobStore(tmp_path))
    monkeypatch.setattr(feedback, 'summary_jobs', jobs)
    packages = {}

    def save(key, package, fingerprint):
        if key in packages and packages[key][1] != fingerprint:
            raise HTTPException(409, 'conflict')
        packages.setdefault(key, (package, fingerprint))

    monkeypatch.setattr(feedback, 'save', save)
    app = FastAPI()
    app.include_router(feedback.router)
    return TestClient(app), jobs, packages


def test_rating_and_retry_do_not_capture_meeting_content(setup):
    client, jobs, packages = setup
    key = str(uuid4())
    jobs.store.write_path(jobs.store.result_path(key), {
        'kind': 'llm', 'owner': 'alice', 'status': 'ferdig', 'feedback_source': 'PRIVATE INPUT',
        'tekst': 'PRIVATE OUTPUT', 'modell': 'borealis-4b', 'trace_id': 'trace',
    })
    url = '/feedback/' + str(uuid4())
    payload = {'rating': 4, 'category': 'speed', 'job_id': key}
    first = client.put(url, json=payload)
    assert first.status_code == 200
    assert client.put(url, json=payload).json() == first.json()
    assert len(packages) == 1
    package = next(iter(packages.values()))[0]
    assert package['attachments'] is None
    assert 'PRIVATE' not in json.dumps(package)
    assert 'alice' not in json.dumps(package)
    assert client.put(url, json={**payload, 'rating': 1}).status_code == 409


def test_attachment_uses_owned_generation_input_and_output(setup):
    client, jobs, packages = setup
    key = str(uuid4())
    jobs.store.write_path(jobs.store.result_path(key), {
        'kind': 'llm', 'owner': 'alice', 'status': 'ferdig',
        'feedback_source': 'Original transcript', 'tekst': 'Exact output',
    })
    payload = {'rating': 2, 'category': 'summary_accuracy', 'job_id': key, 'attach': True}
    url = '/feedback/' + str(uuid4())
    assert client.put(url, json=payload, headers={'test-owner': 'bob'}).status_code == 404
    assert not packages
    assert client.put(url, json=payload).status_code == 200
    assert next(iter(packages.values()))[0]['attachments'] == {
        'transcript': 'Original transcript', 'summary': 'Exact output', 'warnings': [],
    }


def test_gates_validation_and_limits(setup, monkeypatch):
    client, jobs, packages = setup
    url = '/feedback/' + str(uuid4())
    base = {'rating': 3, 'category': 'general'}
    assert client.put(url, json={**base, 'audio': 'not allowed'}).status_code == 422
    assert client.put(url, json={**base, 'transcript': 'unexpected'}).status_code == 422
    assert client.put(url, json={**base, 'rating': True}).status_code == 422
    assert client.put(url, json={**base, 'attach': True}).status_code == 422
    assert client.put(url, content=b'x' * (feedback.MAX_BYTES + 1)).status_code == 413
    assert client.put(url, json=base, headers={'origin': 'https://evil.example'}).status_code == 403
    monkeypatch.setenv('FEEDBACK_ATTACHMENTS_ENABLED', 'false')
    assert client.put(url, json={**base, 'attach': True, 'transcript': 'private'}).status_code == 403
    assert client.get('/feedback/config').json() == {'enabled': True, 'attachments': False, 'audio': False, 'max_audio_bytes': feedback.MAX_AUDIO_BYTES}
    monkeypatch.delenv('FEEDBACK_BUCKET')
    assert client.put(url, json=base).status_code == 503
    assert not packages


def test_failures_are_generic_and_do_not_report_success(setup, monkeypatch, caplog):
    client, _, _ = setup
    def broken(*args):
        raise RuntimeError('PRIVATE CONTENT AND CREDENTIALS')
    monkeypatch.setattr(feedback, 'save', broken)
    response = client.put('/feedback/' + str(uuid4()), json={'rating': 1, 'category': 'general'})
    assert response.status_code == 503
    assert 'PRIVATE' not in response.text + caplog.text


def test_conditional_object_creation_checks_existing_fingerprint(monkeypatch):
    class Blob:
        metadata = None
        conflict = False
        existing = 'same'
        def upload_from_string(self, text, **kwargs):
            assert kwargs['if_generation_match'] == 0
            assert json.loads(text) == {'attachments': None}
            if self.conflict:
                raise PreconditionFailed('exists')
        def reload(self, **kwargs):
            self.metadata = {'fingerprint': self.existing}
    blob = Blob()
    class Bucket:
        def blob(self, name):
            assert name == 'cases/case.json'
            return blob
    monkeypatch.setenv('FEEDBACK_BUCKET', 'test-feedback')
    monkeypatch.setattr(feedback, 'bucket', lambda name: Bucket())
    feedback.save('case', {'attachments': None}, 'same')
    blob.conflict = True
    feedback.save('case', {'attachments': None}, 'same')
    blob.existing = 'different'
    with pytest.raises(HTTPException) as exc:
        feedback.save('case', {'attachments': None}, 'same')
    assert exc.value.status_code == 409


def test_score_only_contains_numeric_rating(monkeypatch):
    import httpx
    calls = []
    def post(url, **kwargs):
        calls.append(kwargs)
        return httpx.Response(200, request=httpx.Request("POST", url))
    monkeypatch.setattr(httpx, "post", post)
    monkeypatch.setenv('LANGFUSE_BASE_URL', 'https://langfuse.example')
    monkeypatch.setenv('LANGFUSE_PUBLIC_KEY', 'public')
    monkeypatch.setenv('LANGFUSE_SECRET_KEY', 'secret')
    monkeypatch.setattr(feedback.llm_observability, '_client', object())
    assert feedback.llm_observability.feedback_score('case', 'trace', 4)
    assert calls[0]['json'] == {
        'id': 'case', 'traceId': 'trace', 'name': 'session_usefulness', 'value': 4, 'dataType': 'NUMERIC',
    }
    assert calls[0]['follow_redirects'] is False


def test_feedback_routes_require_authentication(monkeypatch):
    from frontend.auth import EntraAuthMiddleware
    monkeypatch.setenv('AUTH_REQUIRED', 'true')
    monkeypatch.setenv('NAIS_TOKEN_INTROSPECTION_ENDPOINT', 'http://introspection.invalid')
    app = FastAPI()
    app.include_router(feedback.router)
    app.add_middleware(EntraAuthMiddleware)
    client = TestClient(app)
    assert client.put('/feedback/' + str(uuid4()) + '/audio', content=b'audio').status_code == 401
    assert client.get('/feedback/config').status_code == 401
    assert client.put('/feedback/' + str(uuid4()), json={'rating': 4, 'category': 'general'}).status_code == 401


@pytest.fixture
def audio_store(setup, monkeypatch):
    import hashlib
    monkeypatch.setenv('FEEDBACK_AUDIO_ENABLED', 'true')
    objects = {}
    def save_audio(key, stream, digest, content_type):
        stream.seek(0)
        raw = stream.read()
        assert hashlib.sha256(raw).hexdigest() == digest
        if key in objects and objects[key]['sha256'] != digest:
            raise HTTPException(409, 'different audio')
        objects[key] = {'object': f'audio/{key}', 'sha256': digest, 'bytes': len(raw), 'content_type': content_type}
    def metadata(key, digest):
        if key not in objects or objects[key]['sha256'] != digest:
            raise HTTPException(409, 'missing audio')
        return objects[key]
    monkeypatch.setattr(feedback, 'save_audio', save_audio)
    monkeypatch.setattr(feedback, 'audio_metadata', metadata)
    return setup, objects


def test_audio_requires_opt_in_and_is_scoped_to_submission_owner(audio_store):
    (client, _, packages), objects = audio_store
    submission = str(uuid4())
    url = '/feedback/' + submission
    raw = b'RIFF fictional test audio'
    response = client.put(url + '/audio', content=raw, headers={'content-type': 'audio/wav'})
    assert response.status_code == 200
    digest = response.json()['sha256']
    assert client.put(url + '/audio', content=raw, headers={'content-type': 'audio/wav'}).json() == response.json()
    assert len(objects) == 1
    assert client.put(url + '/audio', content=b'different', headers={'content-type': 'audio/wav'}).status_code == 409
    payload = {'rating': 4, 'category': 'transcription', 'attach': True, 'transcript': 'fictional', 'audio_sha256': digest}
    assert client.put(url, json=payload, headers={'test-owner': 'bob'}).status_code == 409
    assert not packages
    assert client.put(url, json={**payload, 'attach': False, 'transcript': None}).status_code == 403
    assert client.put(url, json=payload).status_code == 200
    package = next(iter(packages.values()))[0]
    assert package['attachments']['audio']['sha256'] == digest
    assert package['attachments']['audio']['bytes'] == len(raw)


def test_audio_limits_and_disable_gate(audio_store, monkeypatch):
    (client, _, _), objects = audio_store
    url = '/feedback/' + str(uuid4()) + '/audio'
    headers = {'content-type': 'audio/wav'}
    assert client.put(url, content=b'', headers=headers).status_code == 422
    assert client.put(url, content=b'bad', headers={'content-type': 'text/html'}).status_code == 415
    monkeypatch.setattr(feedback, 'MAX_AUDIO_BYTES', 4)
    assert client.put(url, content=b'12345', headers=headers).status_code == 413
    assert not objects
    monkeypatch.setenv('FEEDBACK_AUDIO_ENABLED', 'false')
    assert client.put(url, content=b'123', headers=headers).status_code == 403
    assert not objects


def test_audio_cloud_failure_is_generic(audio_store, monkeypatch, caplog):
    (client, _, _), _ = audio_store
    def broken(*args):
        raise RuntimeError('PRIVATE AUDIO OR CREDENTIAL')
    monkeypatch.setattr(feedback, 'save_audio', broken)
    response = client.put('/feedback/' + str(uuid4()) + '/audio', content=b'audio', headers={'content-type': 'audio/wav'})
    assert response.status_code == 503
    assert 'PRIVATE' not in response.text + caplog.text


def test_real_audio_storage_helpers_keep_objects_immutable(monkeypatch):
    import io
    from google.api_core.exceptions import PreconditionFailed, NotFound
    class Blob:
        name = 'audio/case'
        size = 5
        content_type = 'audio/wav'
        conflict = False
        missing = False
        existing = 'digest'
        def upload_from_file(self, stream, **kwargs):
            assert stream.tell() == 0
            assert stream.read() == b'audio'
            assert kwargs['if_generation_match'] == 0
            if self.conflict:
                raise PreconditionFailed('already exists')
        def reload(self, **kwargs):
            if self.missing:
                raise NotFound('missing')
            self.metadata = {'sha256': self.existing}
    blob = Blob()
    class Bucket:
        def blob(self, name):
            assert name == 'audio/case'
            return blob
    monkeypatch.setenv('FEEDBACK_BUCKET', 'test')
    monkeypatch.setattr(feedback, 'bucket', lambda name: Bucket())
    source = io.BytesIO(b'audio')
    source.seek(5)
    feedback.save_audio('case', source, 'digest', 'audio/wav')
    blob.conflict = True
    feedback.save_audio('case', source, 'digest', 'audio/wav')
    blob.existing = 'another'
    with pytest.raises(HTTPException) as exc:
        feedback.save_audio('case', source, 'digest', 'audio/wav')
    assert exc.value.status_code == 409
    blob.missing = True
    with pytest.raises(HTTPException) as exc:
        feedback.audio_metadata('case', 'digest')
    assert exc.value.status_code == 409


@pytest.mark.parametrize('audio', [False, True])
def test_public_https_origin_survives_internal_proxy_url(audio_store, monkeypatch, audio):
    (client, _, _), _ = audio_store
    public = 'https://ao-ki-transkribering.intern.nav.no'
    monkeypatch.setenv('FEEDBACK_ALLOWED_ORIGINS', public)
    # The app sees HTTP behind the TLS-terminating proxy while the browser sends HTTPS.
    url = '/feedback/' + str(uuid4())
    headers = {'origin': public, 'host': 'ao-ki-transkribering.intern.nav.no'}
    if audio:
        response = client.put(url + '/audio', content=b'fictional audio', headers={**headers, 'content-type': 'audio/wav'})
    else:
        response = client.put(url, json={'rating': 4, 'category': 'general'}, headers=headers)
    assert response.status_code == 200, response.text


@pytest.mark.parametrize('origin', [
    'https://ao-ki-transkribering.intern.nav.no',
    'https://ao-ki-transkribering.ansatt.nav.no',
])
def test_origin_uses_public_allowlist_not_proxy_host(setup, monkeypatch, origin):
    client, _, _ = setup
    monkeypatch.setenv('FEEDBACK_ALLOWED_ORIGINS',
                       'https://ao-ki-transkribering.intern.nav.no,https://ao-ki-transkribering.ansatt.nav.no')
    response = client.put('/feedback/' + str(uuid4()), json={'rating': 4, 'category': 'general'},
                          headers={'origin': origin, 'host': 'internal-api:8765'})
    assert response.status_code == 200


def test_untrusted_origin_cannot_spoof_proxy_headers(setup, monkeypatch):
    client, _, packages = setup
    monkeypatch.setenv('FEEDBACK_ALLOWED_ORIGINS', 'https://ao-ki-transkribering.intern.nav.no')
    for origin in ['https://evil.example', 'null', 'https://ao-ki-transkribering.intern.nav.no.evil.example']:
        response = client.put('/feedback/' + str(uuid4()), json={'rating': 4, 'category': 'general'}, headers={
            'origin': origin, 'host': origin.replace('https://', ''),
            'x-forwarded-host': origin.replace('https://', ''), 'x-forwarded-proto': 'https',
        })
        assert response.status_code == 403
    assert not packages


def test_deployment_without_origin_configuration_fails_closed(setup, monkeypatch):
    client, _, packages = setup
    monkeypatch.delenv('FEEDBACK_ALLOWED_ORIGINS', raising=False)
    monkeypatch.setenv('NAIS_CLUSTER_NAME', 'prod-gcp')
    response = client.put('/feedback/' + str(uuid4()), json={'rating': 4, 'category': 'general'},
                          headers={'origin': 'http://testserver'})
    assert response.status_code == 403
    assert not packages


def test_feedback_allowlists_match_manifest_ingresses():
    from pathlib import Path
    import yaml
    for environment in ('dev', 'prod'):
        manifest = yaml.safe_load(Path(f'.nais/{environment}.yaml').read_text().replace('{{image}}', 'test-image'))
        spec = manifest['spec']
        configured = next(item['value'] for item in spec['env'] if item['name'] == 'FEEDBACK_ALLOWED_ORIGINS')
        assert set(configured.split(',')) == set(spec['ingresses'])
