"""Dual-key API error payloads for backward-compatible clients."""

from rest_framework.response import Response


def dual_key_error(message: str, *, status_code: int) -> Response:
    return Response({'error': message, 'detail': message}, status=status_code)
