"""Service-token auth for /internal. The only gate on that namespace (KTD1/KTD2)."""
import hmac
from typing import Optional

from fastapi import Request
from fastapi.responses import JSONResponse


class InternalUnauthorized(Exception):
    pass


async def internal_unauthorized_handler(request: Request, exc: InternalUnauthorized) -> JSONResponse:
    # Bare body, no WWW-Authenticate: reveals nothing about accepted schemes.
    return JSONResponse(status_code=401, content={"error": "unauthorized"})


class InternalAuth:
    """FastAPI dependency: Authorization: Bearer <ENGINE_GATEWAY_TOKEN[_PREVIOUS]>.

    Reads only the Authorization header: never cookies or query parameters.
    Comparison is constant-time. Failure is a bare 401 with no WWW-Authenticate.
    """

    def __init__(self, token: Optional[str], previous: Optional[str] = None):
        self._tokens = [t.encode() for t in (token, previous) if t]

    async def __call__(self, request: Request) -> None:
        header = request.headers.get("authorization", "")
        scheme, _, presented = header.partition(" ")
        ok = False
        if scheme.lower() == "bearer" and presented:
            probe = presented.encode()
            # Evaluate every candidate so timing does not reveal which one matched.
            for candidate in self._tokens:
                ok |= hmac.compare_digest(probe, candidate)
        if not ok:
            raise InternalUnauthorized()
