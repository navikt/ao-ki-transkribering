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
  description = "Minimum GPU nodes (0 = allow scale to zero when GPU workloads are stopped)"
  type        = number
  default     = 0
}

variable "gpu_max_nodes" {
  description = "Maximum GPU nodes"
  type        = number
  default     = 2
}

variable "a100_node_locations" {
  description = "Zones with A100 fallback pools for manual, higher-cost escalation"
  type        = list(string)
  default     = ["europe-west4-a", "europe-west4-b"]
}

variable "litellm_image" {
  description = "Container image for LiteLLM (must be from docker.pkg.dev, gcr.io, or docker.io mirrorable)"
  type        = string
  default     = "europe-west4-docker.pkg.dev/ao-ki-taskforce-prod-2472/vllm/litellm:main-latest"
}

variable "model_api_max_parallel_requests" {
  description = "Gateway in-flight request limit per application key; does not increase GPU inference concurrency"
  type        = number
  default     = 32

  validation {
    condition     = var.model_api_max_parallel_requests >= 1 && var.model_api_max_parallel_requests <= 128 && floor(var.model_api_max_parallel_requests) == var.model_api_max_parallel_requests
    error_message = "The parallel request limit must be an integer between 1 and 128."
  }
}
