# Production: Kapsule in Paris, private buckets for bronze, lake and backups, the public open-data bucket,
# and DNS for the website, the API and the live stream.

locals {
  name = "atlas-prod"
  tags = ["atlas", "env:prod", "managed-by:opentofu"]
}

module "cluster" {
  source = "../../modules/kapsule"

  name                = local.name
  region              = var.region
  zone                = var.zone
  node_type           = "PRO2-S"
  min_nodes           = 2
  max_nodes           = 5
  root_volume_size_gb = 60
  tags                = local.tags
}

module "storage" {
  source = "../../modules/object-storage"

  prefix                 = "eu-disclosure-atlas-prod"
  region                 = var.region
  open_data_bucket       = true
  open_data_cors_origins = ["https://${var.dns_zone}"]
  tags                   = { project = "atlas", env = "prod" }
}

module "dns" {
  source = "../../modules/dns"
  count  = var.manage_dns ? 1 : 0

  dns_zone        = var.dns_zone
  records         = ["", "api", "stream"]
  ingress_ip      = module.cluster.ingress_ip
  caa_letsencrypt = true
}
