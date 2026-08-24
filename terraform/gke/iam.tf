# ── Service account for GPU nodes ────────────────────────────────────────────
resource "google_service_account" "vllm_node" {
  project      = var.project_id
  account_id   = "vllm-node"
  display_name = "vLLM GKE node SA — reads model weights from GCS"
}

# Allow the node SA to read model weights from GCS
resource "google_storage_bucket_iam_member" "vllm_node_gcs_reader" {
  bucket = google_storage_bucket.modeller.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.vllm_node.email}"
}

# ── Workload Identity for vLLM pods ──────────────────────────────────────────
# Pods in the `vllm` namespace can impersonate this KSA to read from GCS.
resource "google_service_account" "vllm_pod" {
  project      = var.project_id
  account_id   = "vllm-pod"
  display_name = "vLLM pod SA — Workload Identity for GCS access"
}

resource "google_storage_bucket_iam_member" "vllm_pod_gcs_reader" {
  bucket = google_storage_bucket.modeller.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.vllm_pod.email}"
}

# Allow the Kubernetes service account (vllm/vllm) to impersonate the GCP SA.
# The Workload Identity pool is created by GKE — must depend on the cluster.
resource "google_service_account_iam_member" "workload_identity_binding" {
  service_account_id = google_service_account.vllm_pod.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "serviceAccount:${var.project_id}.svc.id.goog[vllm/vllm]"

  depends_on = [google_container_cluster.gpu]
}

# ── Artifact Registry read access for GPU nodes ───────────────────────────────
# GPU nodes pull vLLM images from the team's Artifact Registry.
resource "google_project_iam_member" "vllm_node_artifact_reader" {
  project = var.project_id
  role    = "roles/artifactregistry.reader"
  member  = "serviceAccount:${google_service_account.vllm_node.email}"
}
