output "fqdns" {
  description = "Fully qualified names of the records."
  value       = [for name in var.records : name == "" ? var.dns_zone : "${name}.${var.dns_zone}"]
}
