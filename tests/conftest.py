"""Offline suite: local HTTP test servers are allowed, external networking is not."""
import ipaddress
import socket

import pytest


@pytest.fixture(autouse=True)
def no_external_test_network(monkeypatch):
    attempted = []
    original_resolve = socket.getaddrinfo
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex

    def guard(host):
        if host in (None, "", "localhost", "127.0.0.1", "::1", b"localhost"):
            return
        try:
            if ipaddress.ip_address(host).is_loopback:
                return
        except ValueError:
            pass
        # Do not echo the attempted address, which may be diagnostic data.
        attempted.append(True)
        raise OSError("External networking is disabled in offline tests")

    def resolve(host, *args, **kwargs):
        guard(host)
        return original_resolve(host, *args, **kwargs)

    def connect(sock, address):
        if isinstance(address, tuple):
            guard(address[0])
        return original_connect(sock, address)

    def connect_ex(sock, address):
        if isinstance(address, tuple):
            guard(address[0])
        return original_connect_ex(sock, address)

    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)
    yield
    assert not attempted, "An offline test attempted external networking; add a proper mock"
