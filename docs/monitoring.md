# Operational Monitoring

## Scope and Privacy

Monitoring records operational metadata only: fixed operation/outcome categories,
route templates, status codes, durations, queue depth and retry counts. It does
not collect audio, transcripts, prompts, generated results, request/response
bodies, credentials, user identities, filenames or URLs/query strings.
Recording/job IDs may correlate existing operational logs but are never metric
labels. Unknown HTTP paths and unsupported methods share fixed fallback labels.
Structured exception diagnostics record type and code locations, not messages
or local variables. Tracing and automatic body capture remain disabled.

The API's dedicated Prometheus registry has no default hostname/platform
collectors. CPU, memory and restart panels use existing NAIS platform metrics.

## Collection

- JSON operational events go to stdout and NAIS Loki.
- NAIS scrapes `/metrics` on **pod port 9091**, configured in both manifests.
- This is a separate listener, not a route on the application port 8765. No
  ingress, service port, Entra bypass or new public hostname is added for it.
  Do not expose port 9091 through an ingress or an external load balancer.
- `METRICS_ENABLED=true` enables the listener; local runs default to disabled.
  `METRICS_PORT` defaults to 9091 and `METRICS_HOST` to loopback locally. NAIS
  explicitly binds `0.0.0.0` for scraping. Stop/start is tied to the API lifespan.
- One Uvicorn API process owns each registry. Do not add multiple Uvicorn workers
  without implementing Prometheus multiprocess support. Metrics reset on restart.

Application HTTP metrics end when the final response body is sent, before any
FastAPI background work. Streaming responses include their streaming duration.
Summary jobs and uploaded-audio processing have separate outcome/duration metrics;
an HTTP 202 or 200 does not imply that the background job succeeded.

## Signals

| Metric prefix | Meaning |
|---------------|---------|
| `transkriberit_http_requests_total` | Completed responses, by method, route template and status |
| `transkriberit_http_duration_seconds` | Histogram of response duration |
| `transkriberit_http_inflight` | Responses still in progress |
| `transkriberit_operations_total` | Fixed operations and outcomes: ok, error, cancelled, timeout, auth, busy, unavailable |
| `transkriberit_operation_duration_seconds` | Processing duration; summary queue wait is separate |
| `transkriberit_operations_inflight` | Currently executing operations |
| `transkriberit_summary_queued` | Jobs waiting for one of the two summary slots |
| `transkriberit_summary_oldest_queued_timestamp_seconds` | Oldest waiting submission; zero when empty |
| `transkriberit_summary_queue_wait_seconds` | Time until processing or cancellation |
| `transkriberit_summary_rejected_total` | Submissions rejected at queue capacity |
| `transkriberit_speech_retries_total` | Scheduled retries inside the speech client |

`recording_chunk` measures actual chunk processing, not replay of a cached chunk.
Recording latency above the 14-second advance per chunk suggests backlog, but is
not a measurement of the browser's captured-versus-processed timeline. Browser
network failures that never reach the API cannot appear in server HTTP counters.

Model-call errors are passive observations of attempted work, not background
availability probes. No user data is generated or sent just for monitoring.
Model-list success, `/isReady` and `up=1` do not prove inference works. Idle model
outages can go undetected until a real call or an explicit synthetic smoke test.
This first pass does not instrument the separate local CPU worker process or
export GPU utilization. Use GCP/GKE monitoring for GPU/node details separately.

## Dashboard and Alerts

Import [dashboard JSON](../observability/dashboard.json) in Grafana's dashboard
import screen and save it in the team's folder. Select the **environment-specific
Mimir/Prometheus datasource**; review labels before combining cluster sources.
The dashboard includes HTTP errors/latency, queue depth/wait, active operations,
operation failures/duration, retries, memory, CPU and container restarts.

[Alert rules](../.nais/alerts.yaml) deploy with the Application in dev and prod
through the existing workflow. They use the default team notification route,
not a repository-owned Slack webhook. Verify the team's Slack destination in
NAIS Console before relying on notifications. The dashboard import and Slack
configuration are not performed automatically by the workflow.

Initial thresholds are deliberately explicit and should be tuned after observing
traffic: missing/unhealthy scrape targets for five minutes; over 5% HTTP 5xx with
at least 20 responses in five minutes, sustained five minutes; gateway access
denials; repeated model failures; summary queue wait over two minutes sustained
two minutes; and repeated background summary failures. Low-traffic applications
also need operational/model failure signals, not HTTP percentages alone.

## Verify After Deployment

1. Check both API pods are ready and each generated scrape target uses port 9091.
2. In Grafana Explore, select the correct environment datasource and query
   `up{namespace="ao-ki-taskforce",app="ao-ki-transkribering"}`. NAIS uses the
   shared scrape job `nais-system/monitoring-apps-tenant`, not the application
   name. Scope targets by namespace and app so another application's healthy
   scrape cannot hide our outage. Confirm the target labels before trusting
   alerts. Missing metrics are a rollout failure, not proof of a model outage.
3. Generate normal API traffic and inspect `transkriberit_http_requests_total`.
   Confirm IDs, request contents and credentials never appear in labels or logs.
4. Check queue/failure panels during a synthetic dev job. Cancellation must return
   active/waiting gauges to zero; resubmission of the same job is not a new job.
5. Confirm alert rules are loaded in both environments. Exercise a temporary test
   alert in dev with the team, confirm Slack delivery and resolution, then remove
   the test. Do not break production credentials to test an auth-failure alert.
6. Check `/metrics` on the application ingress does not expose the registry.
   A direct pod-only scrape should work without loosening application auth.

## Incident Runbook

- **Metrics unavailable:** inspect pod readiness/restarts, listener startup,
  port conflicts and scrape/network configuration. Check the normal API health
  separately. A healthy scrape does not establish external ingress reachability.
- **HTTP errors:** inspect status/route and timestamps, then correlated operational
  logs. Health probes are not representative of successful user operations.
- **Model access denied:** verify scoped secrets, workload identity and Cloud Run
  invoker permissions. Never print keys, tokens or model request bodies.
- **Model failures:** check gateway 429/5xx, model pod readiness and capacity.
  Speech retries can mask transient failures; compare retry and final outcomes.
- **Queue stalled:** compare active summary count, processing latency and gateway
  failures. More queue capacity does not add GPU capacity. Coordinate restarts:
  in-flight jobs are process-local and will be interrupted.
- **Summary failures:** distinguish gateway failures from source-validation and
  output-limit failures using safe categories and code locations, not meeting text.

## References

- [NAIS metrics setup](https://doc.nais.io/observability/metrics/how-to/expose/)
- [NAIS metrics and platform signals](https://doc.nais.io/observability/metrics/)
- [NAIS Prometheus alerts and team routing](https://doc.nais.io/observability/alerting/reference/prometheusrule/)
- [Prometheus Python metrics listener](https://prometheus.github.io/client_python/exporting/http/)
