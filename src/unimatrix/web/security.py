"""Same-origin dashboard access, DNS-rebinding protection and optional operator auth."""

import base64
import hmac
import ipaddress
from urllib.parse import urlsplit

from starlette.responses import JSONResponse


class DashboardSecurity:
    def __init__(self, app, allowed_hosts, token=None):
        self.app = app
        self.allowed_hosts = frozenset(host.lower().strip("[]") for host in allowed_hosts)
        if not self.allowed_hosts or "*" in self.allowed_hosts:
            raise ValueError("explicit_allowed_hosts_required")
        if token is not None and len(token) < 32:
            raise ValueError("dashboard_token_requires_at_least_32_characters")
        self.token = token

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = {k.decode().lower(): v.decode() for k, v in scope["headers"]}
        host = headers.get("host", "")
        try:
            url = urlsplit(scope["scheme"] + "://" + host)
            valid = (
                url.hostname in self.allowed_hosts
                and not url.username
                and not url.password
                and not url.path
                and not url.query
                and not url.fragment
            )
            _ = url.port
        except ValueError:
            valid = False
        status, error = (None, None) if valid else (400, "untrusted_host")
        if not error and self.token is None:
            client = scope.get("client")
            try:
                local_client = client is not None and ipaddress.ip_address(client[0]).is_loopback
            except ValueError:
                local_client = False
            if not local_client:
                status, error = 403, "remote_client_requires_operator_authentication"
        origin = headers.get("origin")
        if not error and (
            headers.get("sec-fetch-site") == "cross-site"
            or (origin and origin != scope["scheme"] + "://" + host)
        ):
            status, error = 403, "cross_origin_request"
        if not error and self.token:
            authorization = headers.get("authorization", "")
            supplied = ""
            if authorization.startswith("Bearer "):
                supplied = authorization[7:]
            elif authorization.startswith("Basic "):
                try:
                    user, supplied = (
                        base64.b64decode(authorization[6:], validate=True).decode().split(":", 1)
                    )
                    if user != "operator":
                        supplied = ""
                except (ValueError, UnicodeError):
                    supplied = ""
            if not hmac.compare_digest(supplied.encode(), self.token.encode()):
                status, error = 401, "operator_authentication_required"
        if not error and scope["method"] not in {"GET", "HEAD", "OPTIONS"}:
            if headers.get("x-unimatrix-request") != "1":
                status, error = 403, "same_origin_request_header_required"
        if error:
            extra = (
                {"WWW-Authenticate": 'Basic realm="UNIMATRIx", charset="UTF-8"'}
                if status == 401
                else {}
            )
            return await JSONResponse({"detail": error}, status_code=status, headers=extra)(
                scope, receive, send
            )

        async def secure_send(message):
            if message["type"] == "http.response.start":
                message.setdefault("headers", []).extend(
                    [
                        (b"x-content-type-options", b"nosniff"),
                        (b"x-frame-options", b"DENY"),
                        (b"referrer-policy", b"no-referrer"),
                        (b"cache-control", b"no-store"),
                        (
                            b"content-security-policy",
                            b"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
                        ),
                    ]
                )
            await send(message)

        await self.app(scope, receive, secure_send)
