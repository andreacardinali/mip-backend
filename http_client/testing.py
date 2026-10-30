"""HTTP test primitives exposed through the centralized client module."""

from httpx import (
    BaseTransport,
    ConnectError,
    InvalidURL,
    MockTransport,
    ReadTimeout,
    Request,
)

from .retry import HttpClient as Client
from .retry import HttpResponse as Response
from .retry import HttpStatusError as HTTPStatusError

__all__ = [
    "BaseTransport",
    "Client",
    "ConnectError",
    "HTTPStatusError",
    "InvalidURL",
    "MockTransport",
    "ReadTimeout",
    "Request",
    "Response",
]
