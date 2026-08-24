# ── Enable required APIs ──────────────────────────────────────────────────────
resource "google_project_service" "apis" {
  for_each = toset([
    "container.googleapis.com",
    "storage.googleapis.com",
    "artifactregistry.googleapis.com",
    "iam.googleapis.com",
  ])
  project            = var.project_id
  service            = each.key
  disable_on_destroy = false
}

# ── GKE cluster ───────────────────────────────────────────────────────────────
resource "google_container_cluster" "gpu" {
  name     = var.cluster_name
  project  = var.project_id
  location = var.zone

  # Remove the default node pool — we manage pools explicitly.
  remove_default_node_pool = true
  initial_node_count       = 1

  # Workload Identity: lets pods authenticate to GCP APIs (GCS) without
  # mounting service account keys.
  workload_identity_config {
    workload_pool = "${var.project_id}.svc.id.goog"
  }

  # Use the default VPC. NAIS will add VPC peering from their side.
  # No custom network needed for the pilot.

  maintenance_policy {
    recurring_window {
      # Upgrades during weekend nights (Sat 21:00 UTC → Sun 05:00 UTC = 23:00–07:00 CET)
      start_time = "2024-01-06T21:00:00Z"
      end_time   = "2024-01-07T05:00:00Z"
      recurrence = "FREQ=WEEKLY;BYDAY=SA"
    }
  }

  # Don't accidentally destroy the cluster
  deletion_protection = false # set to true after pilot is stable

  depends_on = [google_project_service.apis]
}

# ── System nodepool ───────────────────────────────────────────────────────────
# Runs kube-system, LiteLLM (if deployed to GKE rather than Cloud Run), etc.
resource "google_container_node_pool" "system" {
  name     = "system"
  project  = var.project_id
  cluster  = google_container_cluster.gpu.name
  location = var.zone

  autoscaling {
    min_node_count = 1
    max_node_count = 2
  }

  node_config {
    machine_type = "n2-standard-4"
    disk_size_gb = 50
    disk_type    = "pd-ssd"
    image_type   = "COS_CONTAINERD"

    workload_metadata_config {
      mode = "GKE_METADATA"
    }

    oauth_scopes = ["https://www.googleapis.com/auth/cloud-platform"]
  }

  management {
    auto_repair  = true
    auto_upgrade = true
  }
}

# ── GPU nodepool ──────────────────────────────────────────────────────────────
# g2-standard-12: 12 vCPU, 48 GB RAM, 1× L4 (24 GB VRAM)
# Fits Borealis-12b in BF16 (~24 GB) and nb-whisper-large (~3 GB) on separate pods.
# Autoscales to 0 outside working hours (use Cloud Scheduler — see README).
resource "google_container_node_pool" "gpu" {
  name     = "gpu-l4"
  project  = var.project_id
  cluster  = google_container_cluster.gpu.name
  location = var.zone

  initial_node_count = 0

  autoscaling {
    min_node_count = var.gpu_min_nodes
    max_node_count = var.gpu_max_nodes
  }

  node_config {
    machine_type = "g2-standard-12"
    disk_size_gb = 100
    disk_type    = "pd-ssd"
    image_type   = "COS_CONTAINERD"

    guest_accelerator {
      type  = "nvidia-l4"
      count = 1

      # GKE installs the NVIDIA driver automatically on COS nodes.
      gpu_driver_installation_config {
        gpu_driver_version = "LATEST"
      }
    }

    # Taint ensures only pods that explicitly tolerate GPUs are scheduled here.
    taint {
      key    = "nvidia.com/gpu"
      value  = "present"
      effect = "NO_SCHEDULE"
    }

    workload_metadata_config {
      mode = "GKE_METADATA"
    }

    service_account = google_service_account.vllm_node.email
    oauth_scopes    = ["https://www.googleapis.com/auth/cloud-platform"]
  }

  management {
    auto_repair  = true
    auto_upgrade = true
  }
}
