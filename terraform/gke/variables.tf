variable "project_id" {
  description = "GCP project ID for the ao-ki-taskforce team project"
  type        = string
  default     = "ao-ki-taskforce-prod-2472"
}

variable "region" {
  description = "GCP region — europe-west4 has L4 GPUs"
  type        = string
  default     = "europe-west4"
}

variable "node_locations" {
  description = "Zones where regional GKE node pools may provision nodes"
  type        = list(string)
  default     = ["europe-west4-a", "europe-west4-b", "europe-west4-c"]
}

variable "cluster_name" {
  description = "GKE cluster name"
  type        = string
  default     = "ao-ki-gpu"
}

variable "gpu_min_nodes" {
  description = "Minimum GPU nodes (0 = scale to zero outside working hours)"
  type        = number
  default     = 0
}

variable "gpu_max_nodes" {
  description = "Maximum GPU nodes"
  type        = number
  default     = 2
}

variable "litellm_image" {
  description = "Container image for LiteLLM (must be from docker.pkg.dev, gcr.io, or docker.io mirrorable)"
  type        = string
  default     = "europe-west4-docker.pkg.dev/ao-ki-taskforce-prod-2472/vllm/litellm:main-latest"
}
