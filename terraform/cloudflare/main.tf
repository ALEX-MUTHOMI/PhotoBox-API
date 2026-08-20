terraform {
  required_providers {
    cloudflare = {
      source  = "cloudflare/cloudflare"
      version = "~> 4.0"
    }
  }
}

variable "cloudflare_zone_id" {
  type        = string
  description = "Cloudflare Zone ID for the PhotoBox SaaS domain"
}

# 1. Edge Rate Limiting Rule
resource "cloudflare_rate_limit" "auth_rate_limit" {
  zone_id   = var.cloudflare_zone_id
  threshold = 60
  period    = 60

  match {
    request {
      url_pattern = "*api/v1/gallery/*/access*"
      schemes     = ["HTTPS"]
      methods     = ["POST", "GET"]
    }
    response {
      statuses = [400, 401, 403, 429]
    }
  }

  action {
    mode    = "challenge"
    timeout = 300
  }

  disabled    = false
  description = "Edge rate limiting to prevent gallery PIN and token brute force"
}

# 2. Cloudflare Turnstile Widget
resource "cloudflare_turnstile_widget" "gallery_auth" {
  account_id = var.cloudflare_zone_id
  name       = "PhotoBox Client Gallery Verification"
  mode       = "managed"
  domains    = ["photobox.io", "*.photobox.io"]
}
