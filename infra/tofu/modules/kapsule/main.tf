# Scaleway Kapsule: a managed Kubernetes cluster (free control plane) on a private network, with Cilium,
# an autoscaled and autohealed node pool, and a reserved load-balancer IP for the ingress controller.

resource "scaleway_vpc_private_network" "this" {
  name   = "${var.name}-pn"
  region = var.region
  tags   = var.tags
}

resource "scaleway_k8s_cluster" "this" {
  name                        = var.name
  region                      = var.region
  version                     = var.kubernetes_version
  cni                         = "cilium"
  private_network_id          = scaleway_vpc_private_network.this.id
  delete_additional_resources = var.delete_additional_resources
  tags                        = var.tags

  auto_upgrade {
    enable                        = var.auto_upgrade
    maintenance_window_day        = "sunday"
    maintenance_window_start_hour = 3
  }

  autoscaler_config {
    scale_down_delay_after_add       = "10m"
    scale_down_unneeded_time         = "10m"
    scale_down_utilization_threshold = 0.5
    balance_similar_node_groups      = true
  }
}

resource "scaleway_k8s_pool" "default" {
  cluster_id             = scaleway_k8s_cluster.this.id
  name                   = "default"
  region                 = var.region
  zone                   = var.zone
  node_type              = var.node_type
  size                   = var.min_nodes
  min_size               = var.min_nodes
  max_size               = var.max_nodes
  autoscaling            = true
  autohealing            = true
  root_volume_size_in_gb = var.root_volume_size_gb
  container_runtime      = "containerd"
  tags                   = var.tags

  # The cluster autoscaler owns the current size.
  lifecycle {
    ignore_changes = [size]
  }
}

# Stable public IP for the ingress controller's LoadBalancer Service
# (annotation service.beta.kubernetes.io/scw-loadbalancer-ip-id), so DNS does not follow cluster rebuilds.
resource "scaleway_lb_ip" "ingress" {
  zone = var.zone
  tags = var.tags
}
