# Model API security and cutover

## Status

The repository now supports Cloud Run IAM authentication, separate restricted
NAIS application keys, and authenticated TLS from LiteLLM to the GPU servers.
The deployed environment enables these controls through the checked-in
`terraform/gke/security.auto.tfvars`. Variable defaults remain false for staged
bootstrap; a fresh environment must temporarily override both switches to false
until credentials and GPU TLS are prepared. Local operation is unchanged.

The cutover smoke tests cover NAIS dev/prod model listing, synthetic Whisper
transcription, and denial of admin routes, alternate models, and provider overrides.
Borealis inference still needs verification when its second GPU becomes available.
The checklist below also includes follow-up network-isolation and log-content
checks; deployment alone does not prove every security property.

NAIS client deployments enable `MODEL_API_IAM_AUTH=true`. The client obtains
short-lived, audience-bound Google ID tokens from its workload identity and sends
them in `X-Serverless-Authorization`. The separate application key stays in
`Authorization`. Identity failures do not fall back to API-key-only requests.

## Credential and network boundaries

- The frontend validates inbound Entra bearer tokens through the NAIS token
  introspection sidecar (`identity_provider=entra_id`) for HTTP and WebSocket
  connections. This delegates signature, issuer, audience and time validation
  to NAIS, rather than trusting autologin alone. Missing/invalid tokens are
  rejected; sidecar failures fail closed. Only GET/HEAD `/isAlive` and `/isReady`
  bypass validation. Tokens and introspection responses are not logged.
- `AUTH_REQUIRED=true` is explicit in both NAIS manifests. `NAIS_CLUSTER_NAME`
  also forces validation even if that flag is accidentally disabled; missing
  sidecar configuration prevents startup. Local runs without either setting
  remain unauthenticated and should bind only to localhost. WebSockets are
  validated at connection establishment; this does not add session revocation
  checks during an already established recording.
- The ingress remains internal-only and `allowAllUsers` still permits all users
  in NAV's Entra tenant. No phone, network or group permissions are broadened.
  Per-user result ownership and narrower group authorization remain follow-ups.
  See [NAIS employee token validation](https://docs.nais.io/auth/entra-id/how-to/login/).

- `nais-dev-model-key` and `nais-prod-model-key` are independent inference-only
  credentials. They cannot access gateway administration, other models, or
  override provider URLs/credentials. Only model listing, chat completion, and
  audio transcription routes are permitted. No new database is required.
- The master key remains an administrative secret, not an application credential.
  The custom authentication path does not accept it. Retain access for recovery;
  do not distribute it to NAIS or clients.
- Cloud Run invoker IAM permits only the discovered NAIS dev/prod application
  service accounts once `cloud_run_require_iam=true`. The URL remains internet
  reachable; unauthenticated invocation is denied. This is not private ingress.
- Cloud Run uses the dedicated `model-gateway` subnet. Model load balancers limit
  source ranges to it; higher-priority firewall rules restrict TCP/8000 on GPU
  nodes. Verify actual firewall policy and pod/direct-IP paths after rollout.
  The ILBs use `externalTrafficPolicy: Local` to preserve the gateway source IP;
  health-check NodePorts allow Google's probe ranges while other NodePort access
  on GPU nodes is denied.
- LiteLLM verifies the private CA and certificate IP SANs, never `ssl_verify=false`.
  vLLM requires a backend-only API key. An additional ASGI middleware denies
  alternative inference/admin routes; `/health` is the only unauthenticated route.
- Kubernetes HTTPS probes do not verify certificates. They are liveness checks,
  not evidence that LiteLLM certificate verification works.

Separate credentials are **not complete dev/prod infrastructure isolation**: both
environments still share the gateway/GPU capacity. Separate gateways/projects are
needed if that stronger boundary is required. User authorization at the NAIS API,
including per-user ownership of transcription results, requires a separate review.

## Ordered rollout

Use a maintenance window: moving a running GPU deployment to TLS restarts it and
interrupts inference. Node tag changes may also trigger node updates; inspect the
OpenTofu plan before applying. No second GPU is assumed available.

1. Check that `10.120.0.0/26` is free in the default VPC (or set
   `model_gateway_subnet_cidr`). Review `tofu -chdir=terraform/gke plan`, then apply
   (auto-mode VPCs reserve `10.128.0.0/9`, so custom subnets must be outside it)
   with both security switches false. This creates empty Secret Manager resources,
   the gateway subnet, and NAIS invoker permissions. It does not lock out clients.
2. Run `./scripts/bootstrap-model-security.sh`. It creates credentials and an IP-SAN
   certificate once, stores them in Secret Manager, and synchronizes Kubernetes
   secrets in the explicit model-cluster context. It never prints credentials.
   Partial initialization fails rather than overwriting credentials. Private keys
   stay out of OpenTofu state and git. Back up/protect Secret Manager access.
3. Deploy the new API code with identity-token authentication, **before removing
   public invocation**. Smoke-test from both NAIS environments. The existing
   application key still works at this stage; confirm token acquisition succeeds.
4. Run `MODEL_SECURITY_PREPARE=true START_GPU_FALLBACK=false ./scripts/apply-k8s.sh` to deploy TLS-enabled
   GPU pods. This renderer requires Python with PyYAML (`.venv/bin/python` by
   default), preserves the current node pool, and uses `Recreate` to avoid requiring
   an additional GPU for a rolling surge. Normal hourly GPU retries remain enabled.
   Verify the GPU health and HTTPS certificate before proceeding.
   The old HTTP gateway cannot infer during this maintenance interval.
5. Persist `model_security_enabled=true` in your OpenTofu variable file, review the
   plan, and apply. The gateway loads the scoped authentication handler, separate
   keys, CA trust, and backend credential. Create `ao-ki-model-api` once in each
   NAIS environment with `nais secret create ao-ki-model-api --team ao-ki-taskforce
   --environment dev-gcp` (and `prod-gcp`). Run `./scripts/sync-nais-model-key.sh dev`
   and `./scripts/sync-nais-model-key.sh prod`. They use `nais secret set` with stdin
   (run `nais login --nais` first); NAIS restarts referencing workloads. Only two
   keys are updated. The old unmanaged `ao-ki-transkribering-secrets` remains intact;
   the new managed secret is loaded after it, overriding only model credentials.
6. Test model listing, synthetic audio upload, and synthetic summary generation
   from both applications. Test the **actual deployed LiteLLM image**, not just the
   custom-auth unit tests. Check that application keys cannot reach `/key/generate`
   or supply another model, `api_base`, or `api_key`.
7. Persist `cloud_run_require_iam=true`, review the plan, and apply. Anonymous calls
   and calls with only an application key must now fail at Cloud Run. A valid ID
   token without an application key must fail at LiteLLM. Both credentials should
   succeed. Verify that unrelated service accounts are denied too.
8. Verify another VPC workload cannot reach the model port directly, and check
   certificate failures are rejected by the gateway. Review effective firewall
   rules, not only their source manifests. Remove/rotate old copies of the master
   key from NAIS and operator environments after the cutover.

Do not automate `tofu apply` before reviewing the plan. Never use real meeting
content in smoke tests. Do not paste keys, ID tokens, or secret output into logs.

## Rotation and recovery

Server certificates last 90 days. Run `./scripts/bootstrap-model-security.sh --renew`
before expiry (schedule at least monthly in an authorized operator environment).
It keeps the CA and application keys, signs a new leaf certificate, updates Secret
Manager and Kubernetes, and vLLM's SSL-refresh option reloads the mounted files.
Verify certificate reload on the deployed vLLM version; restart the model pods if
needed. The certificate extensions in `scripts/model-tls.cnf` include authority and
subject key identifiers, and the script verifies server purpose, IP SANs, and
strict X.509 compliance. Gateway trust verification must remain enabled.
The script refuses certificates with less than seven days remaining unless
renewing. No automatic renewal job has been provisioned yet.

For application-key rotation, add a new Secret Manager version, update the matching
NAIS secret, and redeploy the gateway and client together in a maintenance window.
Current static keys do not implement overlapping rotation; plan for that brief
interruption. Revoke superseded versions after verification.

For recovery, keep a trusted operator Cloud Run invoker identity available using
an explicit temporary IAM grant, not `allUsers`. Do not disable TLS verification.
If reverting the whole cutover is necessary, GPU manifests and gateway protocol
must be reverted together. Removing IAM protection is a security rollback, not
a normal troubleshooting step.

## Data retention and logging

- Uploaded audio is deleted in worker/remote-job `finally` blocks after processing.
- Completed/failed result JSON expires after six hours (`JOB_RETENTION_SECONDS`),
  checked every five minutes while the app runs and on startup. Result files are
  owner-readable/writable only. Active jobs are not deleted by the cleanup task.
- Crashes may leave orphaned upload files; browser memory/downloaded copies,
  Kubernetes node disks, and already-written logs are outside this cleanup.
  This is retention minimization, not guaranteed secure erasure.
- Gateway verbose/message logging and spend/error persistence are disabled in
  config. GPU request logging is restricted to ERROR and prompt log length is zero
  in secure manifests, without relying on version-specific request-log flags. These switches
  are not proof that every third-party exception is payload-free: verify synthetic
  canary content does not appear in Cloud Logging or Loki before sensitive use.
- Existing Cloud Logging/Loki retention and access must be checked with the
  platform owners. This change does not silently alter shared platform log policy.

## Sources

- [Cloud Run service authentication](https://docs.cloud.google.com/run/docs/authenticating/service-to-service)
- [LiteLLM custom authentication](https://docs.litellm.ai/docs/proxy/custom_auth)
- [LiteLLM logging controls](https://docs.litellm.ai/docs/proxy/logging)
- [vLLM TLS and API-key options](https://docs.vllm.ai/en/latest/cli/serve/)
