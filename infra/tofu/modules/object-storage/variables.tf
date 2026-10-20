variable "prefix" {
  description = "Bucket name prefix; bucket names are global, e.g. eu-disclosure-atlas-prod."
  type        = string

  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9-]{1,40}[a-z0-9]$", var.prefix))
    error_message = "prefix must be 3-42 lowercase letters, digits or hyphens."
  }
}

variable "region" {
  description = "Scaleway region of the buckets."
  type        = string
  default     = "fr-par"
}

variable "bronze_cold_after_days" {
  description = "Days before raw bronze files move to cold storage (0 disables)."
  type        = number
  default     = 90
}

variable "backup_retention_days" {
  description = "Days PostgreSQL backups are kept (CloudNativePG keeps its own 30-day retention inside)."
  type        = number
  default     = 35
}

variable "open_data_bucket" {
  description = "Create the public open-data bucket."
  type        = bool
  default     = false
}

variable "open_data_cors_origins" {
  description = "Origins allowed to read open data from the browser."
  type        = list(string)
  default     = ["*"]
}

variable "force_destroy" {
  description = "Allow destroying non-empty buckets (previews only)."
  type        = bool
  default     = false
}

variable "tags" {
  description = "Tags applied to every bucket."
  type        = map(string)
  default     = {}
}
