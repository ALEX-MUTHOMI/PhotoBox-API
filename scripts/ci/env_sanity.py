from __future__ import annotations

import os
import sys


TRUE_VALUES = {"true", "1", "yes", "on"}
FALSE_VALUES = {"false", "0", "no", "off"}
REQUIRED = (
    "DEBUG",
    "ALLOWED_HOSTS",
    "CORS_ALLOWED_ORIGINS",
    "CSRF_TRUSTED_ORIGINS",
    "DB_HOST",
    "DB_NAME",
    "DB_USER",
    "CELERY_BROKER_URL",
    "CELERY_RESULT_BACKEND",
    "CLOUDFLARE_R2_ENDPOINT",
    "CLOUDFLARE_R2_BUCKET_NAME",
    "CLOUDFLARE_R2_DOMAIN",
    "CLOUDFLARE_ACCESS_KEY_ID",
    "CLOUDFLARE_SECRET_ACCESS_KEY",
    "CLOUDFLARE_WEBHOOK_SECRET",
    "CLOUDINARY_CLOUD_NAME",
    "LEMON_SQUEEZY_API_KEY",
    "LEMON_SQUEEZY_STORE_ID",
    "LEMON_SQUEEZY_WEBHOOK_SECRET_PRIMARY",
)

REQUIRED_ONE_OF = (
    ("DJANGO_SECRET_KEY", "SECRET_KEY"),
    ("DB_PASSWORD", "DB_PASS"),
)


def main() -> int:
    missing = [name for name in REQUIRED if not os.environ.get(name)]
    missing.extend(
        "/".join(names)
        for names in REQUIRED_ONE_OF
        if not any(os.environ.get(name) for name in names)
    )
    if missing:
        print(f"Missing required environment variables: {', '.join(missing)}", file=sys.stderr)
        return 1

    debug_value = os.environ["DEBUG"].strip().lower()
    if debug_value not in TRUE_VALUES | FALSE_VALUES:
        print("DEBUG must be one of true, 1, yes, on, false, 0, no, off.", file=sys.stderr)
        return 1

    if debug_value in FALSE_VALUES:
        if "*" in {item.strip() for item in os.environ["ALLOWED_HOSTS"].split(",")}:
            print("ALLOWED_HOSTS must not contain '*' when DEBUG is false.", file=sys.stderr)
            return 1
        if "*" in os.environ["CORS_ALLOWED_ORIGINS"]:
            print("CORS_ALLOWED_ORIGINS must be explicit when DEBUG is false.", file=sys.stderr)
            return 1

    print("env sanity ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
