output "cluster_id" {
  description = "Kapsule cluster id."
  value       = scaleway_k8s_cluster.this.id
}

output "apiserver_url" {
  description = "Kubernetes API server URL."
  value       = scaleway_k8s_cluster.this.apiserver_url
}

output "kubeconfig" {
  description = "Admin kubeconfig (used once to bootstrap Argo CD; day-to-day access goes through Argo CD)."
  value       = scaleway_k8s_cluster.this.kubeconfig[0].config_file
  sensitive   = true
}

output "ingress_ip" {
  description = "Reserved public IP address for the ingress load balancer."
  value       = scaleway_lb_ip.ingress.ip_address
}

output "ingress_ip_id" {
  description = "Id of the reserved IP, for the scw-loadbalancer-ip-id Service annotation."
  value       = scaleway_lb_ip.ingress.id
}

output "private_network_id" {
  description = "Private network of the cluster nodes."
  value       = scaleway_vpc_private_network.this.id
}
