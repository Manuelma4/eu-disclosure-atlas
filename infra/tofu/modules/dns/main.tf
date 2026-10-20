# A records in a Scaleway Domains zone, all pointing at the ingress load balancer's reserved IP.

resource "scaleway_domain_record" "this" {
  for_each = toset(var.records)

  dns_zone = var.dns_zone
  name     = each.value
  type     = "A"
  data     = var.ingress_ip
  ttl      = var.ttl
}

# Let's Encrypt only: no other CA may issue certificates for the zone.
resource "scaleway_domain_record" "caa" {
  count = var.caa_letsencrypt ? 1 : 0

  dns_zone = var.dns_zone
  name     = ""
  type     = "CAA"
  data     = "0 issue \"letsencrypt.org\""
  ttl      = 3600
}
