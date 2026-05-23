from __future__ import annotations

import ssl
from urllib.error import URLError
from urllib.request import Request, urlopen


def send_request(request: Request, timeout: int = 10, verify_ssl: bool = True) -> tuple[str, bool]:
    try:
        return _read(request, timeout, verify_ssl), False
    except URLError as exc:
        if verify_ssl and _is_ssl_verify_error(exc):
            return _read(request, timeout, False), True
        raise


def _read(request: Request, timeout: int, verify_ssl: bool) -> str:
    context = None if verify_ssl else ssl._create_unverified_context()
    with urlopen(request, timeout=timeout, context=context) as response:
        return response.read().decode("utf-8")


def _is_ssl_verify_error(exc: URLError) -> bool:
    reason = getattr(exc, "reason", None)
    if isinstance(reason, ssl.SSLCertVerificationError):
        return True
    return "CERTIFICATE_VERIFY_FAILED" in str(exc)
