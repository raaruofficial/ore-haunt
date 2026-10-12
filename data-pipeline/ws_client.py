#!/usr/bin/env python3
"""
Minimal RFC 6455 websocket client in pure stdlib (socket + ssl) — just
enough for Solana JSON-RPC subscriptions (accountSubscribe,
programSubscribe): text frames, ping/pong, close. Client frames are
masked per spec. Fragmented messages are reassembled.

Measured feasibility on the public endpoint is recorded in
OPTIMIZATIONS.md. This exists so the poller can run push-driven at
~0 RPC calls/round in steady state (one subscription, one notification
per Board write) where the endpoint permits it.
"""

import base64
import json
import os
import socket
import ssl
import struct


class WsError(Exception):
    pass


class WsClient:
    def __init__(self, url, timeout=30):
        if not (url.startswith("wss://") or url.startswith("ws://")):
            raise WsError(f"bad ws url {url}")
        self.secure = url.startswith("wss://")
        rest = url.split("://", 1)[1]
        hostport, _, path = rest.partition("/")
        self.path = "/" + path if path else "/"
        host, _, port = hostport.partition(":")
        self.host = host
        self.port = int(port) if port else (443 if self.secure else 80)
        raw = self._connect(timeout)
        if self.secure:
            ctx = ssl.create_default_context()
            raw = ctx.wrap_socket(raw, server_hostname=self.host)
        self.sock = raw
        self.sock.settimeout(timeout)
        self._handshake()
        self._next_id = 1
        self._buf = b""
        self.subscriptions = {}

    def _connect(self, timeout):
        """Direct TCP, or an HTTP CONNECT tunnel when a proxy is configured
        via HTTPS_PROXY/https_proxy (standard behavior behind corporate
        proxies and sandboxed egress)."""
        proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
        if not proxy:
            return socket.create_connection((self.host, self.port), timeout=timeout)
        p = proxy.split("://", 1)[-1]
        cred, _, hostport = p.rpartition("@")
        phost, _, pport = hostport.partition(":")
        s = socket.create_connection((phost, int(pport or 3128)), timeout=timeout)
        lines = [f"CONNECT {self.host}:{self.port} HTTP/1.1",
                 f"Host: {self.host}:{self.port}"]
        if cred:
            auth = base64.b64encode(cred.encode()).decode()
            lines.append(f"Proxy-Authorization: Basic {auth}")
        s.sendall(("\r\n".join(lines) + "\r\n\r\n").encode())
        resp = b""
        while b"\r\n\r\n" not in resp:
            chunk = s.recv(4096)
            if not chunk:
                raise WsError("proxy closed during CONNECT")
            resp += chunk
        if b" 200 " not in resp.split(b"\r\n", 1)[0]:
            raise WsError(f"CONNECT refused: {resp.splitlines()[0]!r}")
        return s

    # ---------------------------------------------------------- handshake
    def _handshake(self):
        key = base64.b64encode(os.urandom(16)).decode()
        req = (
            f"GET {self.path} HTTP/1.1\r\n"
            f"Host: {self.host}:{self.port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        )
        self.sock.sendall(req.encode())
        resp = b""
        while b"\r\n\r\n" not in resp:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise WsError("handshake: connection closed")
            resp += chunk
        head, _, rest = resp.partition(b"\r\n\r\n")
        if b"101" not in head.split(b"\r\n", 1)[0]:
            raise WsError(f"handshake refused: {head.splitlines()[0]!r}")
        expect = base64.b64encode(
            __import__("hashlib").sha1(
                (key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()
        ).decode()
        if expect.encode() not in head:
            raise WsError("handshake: bad Sec-WebSocket-Accept")
        self._buf = rest

    # ---------------------------------------------------------- framing
    def _read_exact(self, n):
        while len(self._buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise WsError("connection closed by peer")
            self._buf += chunk
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def _read_frame(self):
        """-> (opcode, payload). Handles fragmentation internally."""
        payload = b""
        while True:
            h = self._read_exact(2)
            fin = h[0] & 0x80
            op = h[0] & 0x0F
            masked = h[1] & 0x80
            ln = h[1] & 0x7F
            if ln == 126:
                ln = struct.unpack(">H", self._read_exact(2))[0]
            elif ln == 127:
                ln = struct.unpack(">Q", self._read_exact(8))[0]
            mask = self._read_exact(4) if masked else None
            data = self._read_exact(ln) if ln else b""
            if mask:
                data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
            if op == 0x9:      # ping -> pong
                self._send_frame(0xA, data)
                continue
            if op == 0x8:
                raise WsError("close frame received")
            payload += data
            if fin:
                return op, payload

    def _send_frame(self, op, payload):
        mask = os.urandom(4)
        ln = len(payload)
        if ln < 126:
            head = struct.pack("BB", 0x80 | op, 0x80 | ln)
        elif ln < 65536:
            head = struct.pack(">BBH", 0x80 | op, 0x80 | 126, ln)
        else:
            head = struct.pack(">BBQ", 0x80 | op, 0x80 | 127, ln)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self.sock.sendall(head + mask + masked)

    # ---------------------------------------------------------- JSON-RPC
    def request(self, method, params):
        rid = self._next_id
        self._next_id += 1
        self._send_frame(0x1, json.dumps(
            {"jsonrpc": "2.0", "id": rid, "method": method, "params": params}).encode())
        while True:
            op, payload = self._read_frame()
            if op != 0x1:
                continue
            msg = json.loads(payload)
            if msg.get("id") == rid:
                if "error" in msg:
                    raise WsError(f"{method}: {msg['error']}")
                return msg["result"]

    def subscribe_account(self, address):
        sub = self.request("accountSubscribe",
                           [address, {"encoding": "base64", "commitment": "confirmed"}])
        self.subscriptions[sub] = ("account", address)
        return sub

    def subscribe_program(self, program_id, filters=None):
        cfg = {"encoding": "base64", "commitment": "confirmed"}
        if filters:
            cfg["filters"] = filters
        sub = self.request("programSubscribe", [program_id, cfg])
        self.subscriptions[sub] = ("program", program_id)
        return sub

    def next_notification(self, timeout=None):
        """-> parsed notification dict, or None on timeout."""
        if timeout is not None:
            self.sock.settimeout(timeout)
        try:
            while True:
                op, payload = self._read_frame()
                if op != 0x1:
                    continue
                msg = json.loads(payload)
                if msg.get("method", "").endswith("Notification"):
                    return msg
        except (socket.timeout, TimeoutError):
            return None
        finally:
            if timeout is not None:
                self.sock.settimeout(30)

    def close(self):
        try:
            self._send_frame(0x8, b"")
        except Exception:
            pass
        try:
            self.sock.close()
        except Exception:
            pass
