variable "state_passphrase" {
  description = "Passphrase for state and plan encryption (TF_VAR_state_passphrase; at least 16 characters)."
  type        = string
  sensitive   = true
}

variable "region" {
  description = "Scaleway region."
  type        = string
  default     = "fr-par"
}

variable "zone" {
  description = "Scaleway zone."
  type        = string
  default     = "fr-par-1"
}

variable "dns_zone" {
  description = "DNS zone in Scaleway Domains; previews live under pr-<number>.preview.<zone>."
  type        = string
  default     = "atlas.example.org"
}

variable "manage_dns" {
  description = "Create DNS records (false until the zone exists in Scaleway Domains)."
  type        = bool
  default     = false
}
