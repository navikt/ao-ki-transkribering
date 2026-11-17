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
    assert client.get('/feedback/config').json() == {'enabled': True, 'attachments': False, 'audio': False}
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
    assert client.get('/feedback/config').status_code == 401
    assert client.put('/feedback/' + str(uuid4()), json={'rating': 4, 'category': 'general'}).status_code == 401
