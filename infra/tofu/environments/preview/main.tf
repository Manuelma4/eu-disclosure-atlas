# Preview cluster: a small autoscaled Kapsule cluster where Argo CD's ApplicationSet creates one namespace per
# pull request (deploy/argocd/apps/preview). Throwaway buckets and a wildcard record for *.preview.<zone>.

locals {
  name = "atlas-preview"
  tags = ["atlas", "env:preview", "managed-by:opentofu"]
}

module "cluster" {
  source = "../../modules/kapsule"

  name                        = local.name
  region                      = var.region
  zone                        = var.zone
  node_type                   = "PRO2-XS"
  min_nodes                   = 1
  max_nodes                   = 3
  delete_additional_resources = true
  tags                        = local.tags
}

module "storage" {
  source = "../../modules/object-storage"

  prefix                 = "eu-disclosure-atlas-preview"
  region                 = var.region
  bronze_cold_after_days = 0
  backup_retention_days  = 7
  force_destroy          = true
  tags                   = { project = "atlas", env = "preview" }
}

module "dns" {
  source = "../../modules/dns"
  count  = var.manage_dns ? 1 : 0

  dns_zone   = var.dns_zone
  records    = ["*.preview"]
  ingress_ip = module.cluster.ingress_ip
}
