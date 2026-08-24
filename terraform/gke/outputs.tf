output "cluster_name" {
  description = "GKE cluster name"
  value       = google_container_cluster.gpu.name
}

output "cluster_endpoint" {
  description = "GKE control plane endpoint"
  value       = google_container_cluster.gpu.endpoint
  sensitive   = true
}

output "kubeconfig_command" {
  description = "Command to configure kubectl for this cluster"
  value       = "gcloud container clusters get-credentials ${google_container_cluster.gpu.name} --zone=${var.zone} --project=${var.project_id}"
}

output "model_bucket" {
  description = "GCS bucket for model weights"
  value       = google_storage_bucket.modeller.name
}

output "vllm_pod_sa" {
  description = "GCP service account email to annotate the vllm Kubernetes SA with"
  value       = google_service_account.vllm_pod.email
}
