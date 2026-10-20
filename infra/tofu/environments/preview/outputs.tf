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

output "buckets" {
  description = "Bucket names by role."
  value       = module.storage.private_buckets
}
