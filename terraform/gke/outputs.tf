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
  value       = "gcloud container clusters get-credentials ${google_container_cluster.gpu.name} --region=${var.region} --project=${var.project_id}"
}

output "model_bucket" {
  description = "GCS bucket for model weights"
  value       = google_storage_bucket.modeller.name
}

output "vllm_pod_sa" {
  description = "GCP service account email to annotate the vllm Kubernetes SA with"
  value       = google_service_account.vllm_pod.email
}

output "litellm_url" {
  description = "Public LiteLLM Cloud Run URL — use as TRANSKRIPSJON_SERVICE_URL in NAIS"
  value       = google_cloud_run_v2_service.litellm.uri
}

output "litellm_api_key" {
  description = "LiteLLM master API key — store in NAIS secret LITELLM_API_KEY"
  value       = "sk-${random_password.litellm_api_key.result}"
  sensitive   = true
}

output "vllm_whisper_ilb_ip" {
  description = "Reserved internal IP for vllm-whisper — used in k8s/vllm-whisper.yaml loadBalancerIP"
  value       = google_compute_address.vllm_whisper_ilb.address
}

output "vllm_borealis_ilb_ip" {
  description = "Reserved internal IP for vllm-borealis — used in k8s/vllm-borealis.yaml loadBalancerIP"
  value       = google_compute_address.vllm_borealis_ilb.address
}
