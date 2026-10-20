variable "name" {
  description = "Cluster name, e.g. atlas-prod."
  type        = string
}

variable "region" {
  description = "Scaleway region (fr-par, nl-ams or pl-waw: all in the EU)."
  type        = string
  default     = "fr-par"
}

variable "zone" {
  description = "Zone of the node pool and the load-balancer IP."
  type        = string
  default     = "fr-par-1"
}

variable "kubernetes_version" {
  description = "Kubernetes minor version offered by Kapsule (scw k8s version list). A minor version enables auto-upgrade of patches."
  type        = string
  default     = "1.35"
}

variable "auto_upgrade" {
  description = "Apply patch releases automatically in the Sunday 03:00 maintenance window."
  type        = bool
  default     = true
}

variable "node_type" {
  description = "Instance type of the default pool."
  type        = string
  default     = "PRO2-S"
}

variable "min_nodes" {
  description = "Minimum (and initial) number of nodes."
  type        = number
  default     = 2
}

variable "max_nodes" {
  description = "Maximum number of nodes for the cluster autoscaler."
  type        = number
  default     = 5

  validation {
    condition     = var.max_nodes >= var.min_nodes
    error_message = "max_nodes must be at least min_nodes."
  }
}

variable "root_volume_size_gb" {
  description = "Root volume per node, in GB."
  type        = number
  default     = 40
}

variable "delete_additional_resources" {
  description = "Delete volumes and load balancers created by Kubernetes when the cluster is destroyed (true for previews)."
  type        = bool
  default     = false
}

variable "tags" {
  description = "Tags applied to every resource."
  type        = list(string)
  default     = []
}
