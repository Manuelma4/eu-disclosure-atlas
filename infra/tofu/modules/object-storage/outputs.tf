output "private_buckets" {
  description = "Private bucket names by role (bronze, lake, backups)."
  value       = { for role, bucket in scaleway_object_bucket.private : role => bucket.name }
}

output "open_data_bucket" {
  description = "Public open-data bucket name, or null."
  value       = var.open_data_bucket ? scaleway_object_bucket.open_data[0].name : null
}

output "open_data_endpoint" {
  description = "Public HTTPS endpoint of the open-data bucket, or null."
  value       = var.open_data_bucket ? scaleway_object_bucket.open_data[0].endpoint : null
}

output "s3_endpoint" {
  description = "S3 API endpoint of the region."
  value       = "https://s3.${var.region}.scw.cloud"
}
