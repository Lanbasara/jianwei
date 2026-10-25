"""Best-effort defense-in-depth against accidental Python IP networking.

Python imports ``sitecustomize`` during startup. The QA runner supplies this
directory as the complete ``PYTHONPATH`` for credential-free subprocesses.
Local files, pipes, and AF_UNIX sockets remain available. This is not a
security boundary: ``-S``/``-I``, direct ``_socket`` use, and non-Python
processes can bypass it. Trusted fixed runners and exact file manifests are
the enforced gate boundary.
"""
from __future__ import annotations

import errno
import socket
from typing import Any


class NetworkDisabledError(PermissionError):
    """An offline QA process attempted to use IP networking."""


def _blocked(*_args: Any, **_kwargs: Any) -> Any:
    raise NetworkDisabledError(errno.EPERM, "IP networking is disabled for offline QA")


_ORIGINAL_SOCKET = socket.socket


class _OfflineSocket(_ORIGINAL_SOCKET):
    def _deny_ip(self) -> None:
        if self.family in (socket.AF_INET, socket.AF_INET6):
            _blocked()

    def bind(self, *args: Any, **kwargs: Any) -> Any:
        self._deny_ip()
        return super().bind(*args, **kwargs)

    def connect(self, *args: Any, **kwargs: Any) -> Any:
        self._deny_ip()
        return super().connect(*args, **kwargs)

    def connect_ex(self, *args: Any, **kwargs: Any) -> Any:
        self._deny_ip()
        return super().connect_ex(*args, **kwargs)

    def sendto(self, *args: Any, **kwargs: Any) -> Any:
        self._deny_ip()
        return super().sendto(*args, **kwargs)

    def sendmsg(self, *args: Any, **kwargs: Any) -> Any:
        self._deny_ip()
        return super().sendmsg(*args, **kwargs)


socket.socket = _OfflineSocket
for _name in (
    "create_connection",
    "getaddrinfo",
    "gethostbyaddr",
    "gethostbyname",
    "gethostbyname_ex",
    "getnameinfo",
):
    setattr(socket, _name, _blocked)
