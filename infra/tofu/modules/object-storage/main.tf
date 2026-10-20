# Object storage for one environment:
# - bronze: raw regulator files, private, versioned, moved to cold storage after a while (never published);
# - lake: silver Parquet and DuckLake data files, private;
# - backups: CloudNativePG base backups and WAL, private, expiring;
# - open data (optional): public-read releases of the gold tables, with CORS for in-browser DuckDB-WASM.

locals {
  private_buckets = {
    bronze  = { versioned = true, cold_after_days = var.bronze_cold_after_days, expire_after_days = 0 }
    lake    = { versioned = true, cold_after_days = 0, expire_after_days = 0 }
    backups = { versioned = false, cold_after_days = 0, expire_after_days = var.backup_retention_days }
  }
}

resource "scaleway_object_bucket" "private" {
  for_each = local.private_buckets

  name          = "${var.prefix}-${each.key}"
  region        = var.region
  force_destroy = var.force_destroy
  tags          = merge(var.tags, { dataset = each.key, visibility = "private" })

  versioning {
    enabled = each.value.versioned
  }

  dynamic "lifecycle_rule" {
    for_each = each.value.cold_after_days > 0 ? [1] : []
    content {
      id      = "cold-storage"
      enabled = true
      transition {
        days          = each.value.cold_after_days
        storage_class = "GLACIER"
      }
    }
  }

  dynamic "lifecycle_rule" {
    for_each = each.value.expire_after_days > 0 ? [1] : []
    content {
      id      = "expire"
      enabled = true
      expiration {
        days = each.value.expire_after_days
      }
    }
  }
}

resource "scaleway_object_bucket_acl" "private" {
  for_each = scaleway_object_bucket.private

  bucket = each.value.id
  region = var.region
  acl    = "private"
}

resource "scaleway_object_bucket" "open_data" {
  count = var.open_data_bucket ? 1 : 0

  name          = "${var.prefix}-open-data"
  region        = var.region
  force_destroy = var.force_destroy
  tags          = merge(var.tags, { dataset = "open-data", visibility = "public" })

  versioning {
    enabled = true
  }

  cors_rule {
    allowed_methods = ["GET", "HEAD"]
    allowed_origins = var.open_data_cors_origins
    allowed_headers = ["*"]
    expose_headers  = ["ETag", "Content-Length", "Content-Range"]
    max_age_seconds = 3600
  }
}

resource "scaleway_object_bucket_acl" "open_data" {
  count = var.open_data_bucket ? 1 : 0

  bucket = scaleway_object_bucket.open_data[0].id
  region = var.region
  acl    = "public-read"
}
