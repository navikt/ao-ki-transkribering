# ── Random API key ────────────────────────────────────────────────────────────
resource "random_password" "litellm_api_key" {
  length  = 40
  special = false
}

resource "random_password" "litellm_salt_key" {
  length  = 40
  special = false
}

# ── Secret Manager ────────────────────────────────────────────────────────────
resource "google_secret_manager_secret" "litellm_api_key" {
  project   = var.project_id
  secret_id = "litellm-api-key"
  replication {
    user_managed {
      replicas {
        location = var.region
      }
    }
  }
  depends_on = [google_project_service.apis["secretmanager.googleapis.com"]]
}

resource "google_secret_manager_secret_version" "litellm_api_key" {
  secret = google_secret_manager_secret.litellm_api_key.id
  # sk- prefix required by LiteLLM master key format
  secret_data = "sk-${random_password.litellm_api_key.result}"
}

resource "google_secret_manager_secret" "litellm_config" {
  project   = var.project_id
  secret_id = "litellm-config"
  replication {
    user_managed {
      replicas {
        location = var.region
      }
    }
  }
  depends_on = [google_project_service.apis["secretmanager.googleapis.com"]]
}

resource "google_secret_manager_secret" "litellm_salt_key" {
  project   = var.project_id
  secret_id = "litellm-salt-key"
  replication {
    user_managed {
      replicas {
        location = var.region
      }
    }
  }
  depends_on = [google_project_service.apis["secretmanager.googleapis.com"]]
}

resource "google_secret_manager_secret_version" "litellm_salt_key" {
  secret      = google_secret_manager_secret.litellm_salt_key.id
  secret_data = random_password.litellm_salt_key.result
}

resource "google_secret_manager_secret_version" "litellm_config" {
  secret = google_secret_manager_secret.litellm_config.id
  # vLLM exposes an OpenAI-compatible API — LiteLLM calls it as an openai/ proxy.
  secret_data = <<-YAML
    model_list:
      - model_name: nb-whisper-large
        litellm_params:
          model: openai//models/nb-whisper-large
          api_base: "http://${google_compute_address.vllm_whisper_ilb.address}:8000/v1"
          api_key: none
      - model_name: borealis-12b
        litellm_params:
          model: openai//models/borealis-12b
          api_base: "http://${google_compute_address.vllm_borealis_ilb.address}:8000/v1"
          api_key: none
    general_settings:
      max_parallel_requests: 10
  YAML
}

# ── Reserved internal IPs for vLLM Internal Load Balancers ────────────────────
# GCP assigns IPs from the default subnet in europe-west4.
# These IPs are stable — used in both the K8s service manifests and the
# LiteLLM config above. Apply Terraform before kubectl apply.
resource "google_compute_address" "vllm_whisper_ilb" {
  name         = "vllm-whisper-ilb"
  project      = var.project_id
  region       = var.region
  address_type = "INTERNAL"
  subnetwork   = "default"
  depends_on   = [google_project_service.apis["compute.googleapis.com"]]
}

resource "google_compute_address" "vllm_borealis_ilb" {
  name         = "vllm-borealis-ilb"
  project      = var.project_id
  region       = var.region
  address_type = "INTERNAL"
  subnetwork   = "default"
  depends_on   = [google_project_service.apis["compute.googleapis.com"]]
}

# ── Service account for Cloud Run ─────────────────────────────────────────────
resource "google_service_account" "litellm" {
  project      = var.project_id
  account_id   = "litellm"
  display_name = "LiteLLM Cloud Run SA"
}

resource "google_secret_manager_secret_iam_member" "litellm_reads_api_key" {
  project   = var.project_id
  secret_id = google_secret_manager_secret.litellm_api_key.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.litellm.email}"
}

resource "google_secret_manager_secret_iam_member" "litellm_reads_config" {
  project   = var.project_id
  secret_id = google_secret_manager_secret.litellm_config.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.litellm.email}"
}

resource "google_secret_manager_secret_iam_member" "litellm_reads_salt_key" {
  project   = var.project_id
  secret_id = google_secret_manager_secret.litellm_salt_key.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.litellm.email}"
}

# ── Cloud Run: LiteLLM gateway ────────────────────────────────────────────────
resource "google_cloud_run_v2_service" "litellm" {
  project  = var.project_id
  name     = "litellm"
  location = var.region

  template {
    service_account = google_service_account.litellm.email

    # Direct VPC egress — routes private IP traffic into the default VPC,
    # giving LiteLLM access to vLLM Internal Load Balancers without exposing them.
    vpc_access {
      network_interfaces {
        network    = "default"
        subnetwork = "default"
      }
      egress = "PRIVATE_RANGES_ONLY"
    }

    containers {
      # TODO: pin to a specific digest before prod
      image = var.litellm_image
      args  = ["--config", "/app/config/config.yaml", "--host", "0.0.0.0", "--port", "4000"]

      ports { container_port = 4000 }

      resources {
        limits   = { cpu = "1", memory = "2Gi" }
        cpu_idle = true # scale to zero between requests
      }

      env {
        name = "LITELLM_MASTER_KEY"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.litellm_api_key.secret_id
            version = "latest"
          }
        }
      }
      env {
        name = "LITELLM_SALT_KEY"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.litellm_salt_key.secret_id
            version = "latest"
          }
        }
      }

      volume_mounts {
        name       = "litellm-config"
        mount_path = "/app/config"
      }
    }

    volumes {
      name = "litellm-config"
      secret {
        secret = google_secret_manager_secret.litellm_config.secret_id
        items {
          version = "latest"
          path    = "config.yaml"
          mode    = 0444
        }
      }
    }
  }

  depends_on = [
    google_project_service.apis["run.googleapis.com"],
    google_secret_manager_secret_iam_member.litellm_reads_api_key,
    google_secret_manager_secret_iam_member.litellm_reads_config,
    google_secret_manager_secret_iam_member.litellm_reads_salt_key,
  ]
}

# Public access — LiteLLM enforces the API key, so no Cloud Run IAM auth needed.
resource "google_cloud_run_v2_service_iam_member" "litellm_public" {
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.litellm.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}
