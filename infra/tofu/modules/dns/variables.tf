variable "dns_zone" {
  description = "DNS zone managed by Scaleway Domains, e.g. atlas.example.org."
  type        = string
}

variable "records" {
  description = "Record names inside the zone (\"\" is the apex, \"*.preview\" a wildcard)."
  type        = list(string)
}

variable "ingress_ip" {
  description = "IPv4 address of the ingress load balancer."
  type        = string

  validation {
    condition     = can(cidrhost("${var.ingress_ip}/32", 0))
    error_message = "ingress_ip must be an IPv4 address."
  }
}

variable "ttl" {
  description = "Record TTL in seconds."
  type        = number
  default     = 300
}

variable "caa_letsencrypt" {
  description = "Add a CAA record that allows only Let's Encrypt."
  type        = bool
  default     = false
}
