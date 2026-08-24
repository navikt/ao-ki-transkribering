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

variable "zone" {
  description = "GCP zone for the zonal GKE cluster"
  type        = string
  default     = "europe-west4-b"
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
