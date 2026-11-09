import ipaddress
import os


def client_ip(request_or_scope):
    scope = getattr(request_or_scope, "scope", request_or_scope)
    socket = scope.get("client")
    address = socket[0] if socket else "unknown"
    if os.getenv("TRUST_PROXY_HEADERS", "false").lower() == "true":
        headers = dict(scope.get("headers", []))
        forwarded = headers.get(b"x-forwarded-for", b"").decode("latin-1").split(",", 1)[0].strip()
        if forwarded:
            try:
                return str(ipaddress.ip_address(forwarded))
            except ValueError:
                pass
    try:
        return str(ipaddress.ip_address(address))
    except ValueError:
        return address[:200]
