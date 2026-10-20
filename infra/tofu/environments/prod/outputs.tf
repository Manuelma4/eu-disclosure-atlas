output "cluster_id" {
  description = "Kapsule cluster id."
  value       = module.cluster.cluster_id
}

output "kubeconfig" {
  description = "Admin kubeconfig for the Argo CD bootstrap (tofu output -raw kubeconfig)."
  value       = module.cluster.kubeconfig
  sensitive   = true
}

output "ingress_ip" {
  description = "Public IP of the ingress load balancer."
  value       = module.cluster.ingress_ip
}

output "ingress_ip_id" {
  description = "Reserved IP id for the Traefik Service annotation."
  value       = module.cluster.ingress_ip_id
}

output "buckets" {
  description = "Bucket names by role."
  value       = merge(module.storage.private_buckets, { open_data = module.storage.open_data_bucket })
}

output "open_data_endpoint" {
  description = "Public endpoint of the open-data releases."
  value       = module.storage.open_data_endpoint
}
