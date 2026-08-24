# ── GCS bucket for model weights ─────────────────────────────────────────────
# Model weights are NOT baked into the vLLM image. They are downloaded to this
# bucket once and mounted into pods at startup.
#
# Upload weights:
#   gsutil -m cp -r /path/to/nb-whisper-large  gs://ao-ki-modeller/whisper/
#   gsutil -m cp -r /path/to/borealis-12b      gs://ao-ki-modeller/borealis-12b/
#
# Startup time: ~3 GB (Whisper) at 675 MiB/s ≈ 5 sec; ~24 GB (Borealis) ≈ 36 sec.
resource "google_storage_bucket" "modeller" {
  name          = "${var.project_id}-modeller"
  project       = var.project_id
  location      = var.region
  storage_class = "STANDARD"

  uniform_bucket_level_access = true

  versioning {
    enabled = false
  }

  # Prevent accidental deletion of model weights
  lifecycle_rule {
    action {
      type = "SetStorageClass"
      storage_class = "NEARLINE"
    }
    condition {
      age = 90 # move to cheaper storage after 90 days of no access
    }
  }
}
