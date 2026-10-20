terraform {
  required_version = ">= 1.10"
  required_providers {
    scaleway = {
      source  = "scaleway/scaleway"
      version = "~> 2.87"
    }
  }

  # State in a Scaleway bucket created once by hand (see docs/operations.md); credentials come from
  # AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY. Initialise with: tofu init -backend-config=backend.hcl
  backend "s3" {
    region                      = "fr-par"
    endpoints                   = { s3 = "https://s3.fr-par.scw.cloud" }
    skip_credentials_validation = true
    skip_region_validation      = true
    skip_requesting_account_id  = true
    skip_s3_checksum            = true
    use_path_style              = true
  }

  # Client-side state and plan encryption (OpenTofu): the state holds the cluster's admin kubeconfig.
  encryption {
    key_provider "pbkdf2" "state" {
      passphrase = var.state_passphrase
    }
    method "aes_gcm" "state" {
      keys = key_provider.pbkdf2.state
    }
    state {
      method = method.aes_gcm.state
    }
    plan {
      method = method.aes_gcm.state
    }
  }
}

provider "scaleway" {
  # SCW_ACCESS_KEY, SCW_SECRET_KEY and SCW_DEFAULT_PROJECT_ID come from the environment.
  region = var.region
  zone   = var.zone
}
