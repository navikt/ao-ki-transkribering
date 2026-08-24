terraform {
  required_version = ">= 1.6"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.0"
    }
  }

  # State stored in a GCS bucket in the team project.
  # Create the bucket once manually before first init:
  #   gcloud storage buckets create gs://ao-ki-taskforce-prod-2472-tfstate \
  #     --project=ao-ki-taskforce-prod-2472 \
  #     --location=europe-west4 \
  #     --uniform-bucket-level-access
  backend "gcs" {
    bucket = "ao-ki-taskforce-prod-2472-tfstate"
    prefix = "gke"
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
  zone    = var.zone
}
