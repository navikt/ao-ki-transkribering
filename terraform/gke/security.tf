variable "model_security_enabled" {
  description = "Enable scoped gateway credentials and verified TLS to model servers after bootstrapping secrets and GPU pods."
  type        = bool
  default     = false
}

variable "cloud_run_require_iam" {
  description = "Remove public Cloud Run invocation after NAIS identity-token smoke tests pass."
  type        = bool
  default     = false
}

variable "nais_invoker_service_accounts" {
  description = "NAIS application identities allowed to invoke the gateway (not service-account keys)."
  type        = set(string)
  default = [
    "ao-ki-trans-ao-ki-task-yimbi4i@nais-dev-2e7b.iam.gserviceaccount.com",
    "ao-ki-trans-ao-ki-task-yimbi4i@nais-prod-020f.iam.gserviceaccount.com",
  ]
}

variable "model_gateway_subnet_cidr" {
  description = "Dedicated /26 for Cloud Run model traffic; verify it does not overlap existing VPC subnets."
  type        = string
  default     = "10.120.0.0/26"
}

locals {
  model_security_secrets = toset(["nais-dev-model-key", "nais-prod-model-key", "vllm-api-key", "vllm-ca", "vllm-tls-bundle"])
}

# Secret values are bootstrapped outside OpenTofu so private keys do not enter state.
resource "google_secret_manager_secret" "model_security" {
  for_each  = local.model_security_secrets
  project   = var.project_id
  secret_id = each.value
  replication {
    user_managed {
      replicas { location = var.region }
    }
  }
  depends_on = [google_project_service.apis["secretmanager.googleapis.com"]]
}

resource "google_secret_manager_secret_iam_member" "gateway_model_security" {
  for_each  = toset(["nais-dev-model-key", "nais-prod-model-key", "vllm-api-key", "vllm-ca"])
  project   = var.project_id
  secret_id = google_secret_manager_secret.model_security[each.value].secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.litellm.email}"
}

resource "google_secret_manager_secret" "gateway_auth_code" {
  project   = var.project_id
  secret_id = "litellm-scoped-auth"
  replication {
    user_managed {
      replicas { location = var.region }
    }
  }
  depends_on = [google_project_service.apis["secretmanager.googleapis.com"]]
}

resource "google_secret_manager_secret_version" "gateway_auth_code" {
  secret      = google_secret_manager_secret.gateway_auth_code.id
  secret_data = file("${path.module}/../../images/litellm/scoped_auth.py")
  lifecycle {
    create_before_destroy = true
  }
}

resource "google_secret_manager_secret_iam_member" "gateway_reads_auth_code" {
  project   = var.project_id
  secret_id = google_secret_manager_secret.gateway_auth_code.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.litellm.email}"
}

resource "google_compute_subnetwork" "model_gateway" {
  name                     = "model-gateway"
  project                  = var.project_id
  region                   = var.region
  network                  = "default"
  ip_cidr_range            = var.model_gateway_subnet_cidr
  private_ip_google_access = true
  depends_on               = [google_project_service.apis["compute.googleapis.com"]]
}

resource "google_cloud_run_v2_service_iam_member" "nais_invoker" {
  for_each = var.nais_invoker_service_accounts
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.litellm.name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${each.value}"
}

resource "google_compute_firewall" "model_gateway_allow" {
  count         = var.model_security_enabled ? 1 : 0
  project       = var.project_id
  name          = "model-gateway-allow"
  network       = "default"
  priority      = 800
  source_ranges = [var.model_gateway_subnet_cidr]
  target_tags   = ["ao-ki-model-server"]
  allow {
    protocol = "tcp"
    ports    = ["8000"]
  }
}

resource "google_compute_firewall" "model_gateway_deny" {
  count         = var.model_security_enabled ? 1 : 0
  project       = var.project_id
  name          = "model-gateway-deny"
  network       = "default"
  priority      = 900
  source_ranges = ["0.0.0.0/0"]
  target_tags   = ["ao-ki-model-server"]
  deny {
    protocol = "tcp"
    ports    = ["8000", "30000-32767"]
  }
}

# With externalTrafficPolicy=Local, the passthrough ILB uses health-check NodePorts.
resource "google_compute_firewall" "model_health_checks" {
  count         = var.model_security_enabled ? 1 : 0
  project       = var.project_id
  name          = "model-health-checks"
  network       = "default"
  priority      = 700
  source_ranges = ["130.211.0.0/22", "35.191.0.0/16"]
  target_tags   = ["ao-ki-model-server"]
  allow {
    protocol = "tcp"
    ports    = ["30000-32767"]
  }
}
