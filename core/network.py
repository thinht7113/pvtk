"""
PVTK TCP Network Socket Client
Handles binary packet framing and streaming over persistent TCP connection.
"""

import socket
import struct
import time
from core.message import Message

import base64
import threading
import weakref
from typing import Optional, Dict, Any
from core.message import Message

active_connections: "weakref.WeakSet[PVTKConnection]" = weakref.WeakSet()
active_conn_lock = threading.Lock()

def graceful_shutdown_all():
    """
    Called by the main thread signal handler on Ctrl+C.
    Sends 0x0003 (Leave Character) to all active connections before closing them.
    This prevents ghost accounts on the server.
    """
    with active_conn_lock:
        conns = list(active_connections)
        active_connections.clear()
    for conn in conns:
        try:
            if conn.is_connected and conn.sock:
                # Construct CG_LOGOUT message (0x0003)
                # total_len=4, msg_type=3 (0x0003), payload_len=0
                logout_packet = struct.pack(">Hh", 4, 3)
                try:
                    conn.sock.settimeout(0.5)
                    conn.sock.sendall(logout_packet)
                except Exception:
                    pass
            conn.close()
        except Exception:
            pass

def parse_proxy_string(proxy_str: str) -> Dict[str, Any]:
    """
    Parses various proxy formats:
    - http://user:pass@host:port or https://...
    - socks5://user:pass@host:port or socks5://host:port
    - host:port:user:pass
    - user:pass@host:port
    - host:port
    """
    proxy_str = proxy_str.strip()
    proto = "socks5"
    if proxy_str.startswith("http://"):
        proto = "http"
        proxy_str = proxy_str[7:]
    elif proxy_str.startswith("https://"):
        proto = "http"
        proxy_str = proxy_str[8:]
    elif proxy_str.startswith("socks5://"):
        proto = "socks5"
        proxy_str = proxy_str[9:]
    elif proxy_str.startswith("socks4://"):
        proto = "socks4"
        proxy_str = proxy_str[9:]

    user = None
    password = None

    if "@" in proxy_str:
        auth_part, host_part = proxy_str.split("@", 1)
        if ":" in auth_part:
            user, password = auth_part.split(":", 1)
        else:
            user = auth_part
        if ":" in host_part:
            h, p = host_part.split(":", 1)
            host, port = h, int(p)
        else:
            host, port = host_part, 1080 if proto.startswith("socks") else 8080
    elif proxy_str.count(":") == 3:
        # host:port:user:pass
        host, p, user, password = proxy_str.split(":", 3)
        port = int(p)
    elif proxy_str.count(":") == 1:
        # host:port
        host, p = proxy_str.split(":", 1)
        port = int(p)
    else:
        host = proxy_str
        port = 1080 if proto.startswith("socks") else 8080

    return {
        "proto": proto,
        "host": host,
        "port": port,
        "user": user,
        "password": password
    }


def _connect_socks5(sock: socket.socket, target_host: str, target_port: int, user: str = None, password: str = None) -> bool:
    """Performs SOCKS5 RFC 1928 / RFC 1929 Handshake on socket."""
    # 1. Greeting
    if user and password:
        sock.sendall(b"\x05\x02\x00\x02")  # Supports No Auth (0x00) and User/Pass (0x02)
    else:
        sock.sendall(b"\x05\x01\x00")      # No Auth only

    resp = sock.recv(2)
    if not resp or len(resp) < 2 or resp[0] != 0x05:
        return False

    auth_method = resp[1]
    if auth_method == 0x02:  # User/Pass auth required
        if not user or not password:
            return False
        u_bytes = user.encode("utf-8")
        p_bytes = password.encode("utf-8")
        auth_req = b"\x01" + bytes([len(u_bytes)]) + u_bytes + bytes([len(p_bytes)]) + p_bytes
        sock.sendall(auth_req)
        auth_resp = sock.recv(2)
        if not auth_resp or len(auth_resp) < 2 or auth_resp[1] != 0x00:
            return False
    elif auth_method != 0x00:
        return False

    # 2. Connect Command (RFC 1928)
    try:
        ip_bytes = socket.inet_aton(target_host)
        req = b"\x05\x01\x00\x01" + ip_bytes + struct.pack(">H", target_port)
    except Exception:
        host_bytes = target_host.encode("utf-8")
        req = b"\x05\x01\x00\x03" + bytes([len(host_bytes)]) + host_bytes + struct.pack(">H", target_port)

    sock.sendall(req)
    resp = sock.recv(10)
    if not resp or len(resp) < 4 or resp[0] != 0x05 or resp[1] != 0x00:
        return False
    return True


def _connect_http_tunnel(sock: socket.socket, target_host: str, target_port: int, user: str = None, password: str = None) -> bool:
    """Performs HTTP CONNECT Tunneling Handshake on socket."""
    req_lines = [
        f"CONNECT {target_host}:{target_port} HTTP/1.1",
        f"Host: {target_host}:{target_port}",
        "Proxy-Connection: Keep-Alive",
        "User-Agent: Mozilla/5.0"
    ]
    if user and password:
        creds = base64.b64encode(f"{user}:{password}".encode("utf-8")).decode("utf-8")
        req_lines.append(f"Proxy-Authorization: Basic {creds}")
    req_lines.append("\r\n")

    req_data = "\r\n".join(req_lines).encode("utf-8")
    sock.sendall(req_data)

    resp_data = bytearray()
    while b"\r\n\r\n" not in resp_data:
        chunk = sock.recv(1024)
        if not chunk:
            break
        resp_data.extend(chunk)
        if len(resp_data) > 8192:
            break

    header_text = resp_data.decode("latin-1", errors="ignore")
    first_line = header_text.split("\r\n", 1)[0]
    if " 200 " in first_line or first_line.startswith("HTTP/1.1 200") or first_line.startswith("HTTP/1.0 200"):
        return True
    return False


class PVTKConnection:
    def __init__(self, host: str, port: int, timeout: float = 15.0, proxy: Optional[str] = None):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.proxy = proxy
        self.sock: Optional[socket.socket] = None
        self.is_connected = False
        self._rx_buf: bytearray = bytearray()
        self.lock = threading.RLock()

    def connect(self) -> bool:
        self.close()
        try:
            self._rx_buf = bytearray()
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            self.sock.settimeout(self.timeout)

            if self.proxy:
                # Tunnel through proxy
                p_info = parse_proxy_string(self.proxy)
                proxy_host = p_info["host"]
                proxy_port = p_info["port"]
                proto = p_info["proto"]
                user = p_info["user"]
                password = p_info["password"]

                # Connect to Proxy server
                self.sock.connect((proxy_host, proxy_port))

                # Handshake
                if proto == "http":
                    success = _connect_http_tunnel(self.sock, self.host, self.port, user, password)
                else:
                    # Default SOCKS5
                    success = _connect_socks5(self.sock, self.host, self.port, user, password)
                    if not success and not self.proxy.startswith("socks5://"):
                        # If default SOCKS5 failed and user didn't explicitly prefix socks5://, try HTTP CONNECT
                        try:
                            try:
                                self.sock.close()
                            except Exception:
                                pass
                            self.sock = None
                            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                            self.sock.settimeout(self.timeout)
                            self.sock.connect((proxy_host, proxy_port))
                            success = _connect_http_tunnel(self.sock, self.host, self.port, user, password)
                        except Exception:
                            success = False

                if not success:
                    self.is_connected = False
                    self.close()
                    return False
            else:
                # Direct connection
                self.sock.connect((self.host, self.port))

            self.is_connected = True
            with active_conn_lock:
                active_connections.add(self)
            return True
        except Exception as e:
            self.is_connected = False
            self.close()
            return False

    def send_message(self, msg: Message) -> bool:
        with self.lock:
            if not self.is_connected or not self.sock:
                return False
            try:
                self.sock.settimeout(self.timeout)
                data = msg.getDataBytes()
                self.sock.sendall(data)
                return True
            except Exception:
                self.close()
                return False

    def receive_message(self, timeout: float = None) -> Message:
        """
        Receives exactly one complete protocol message from the TCP stream.
        Uses persistent _rx_buf accumulator to prevent partial-packet desynchronization on timeout.
        """
        with self.lock:
            if not self.is_connected or not self.sock:
                return None

            start_time = time.time()
            timeout_val = timeout if timeout is not None else self.timeout

            try:
                while self.is_connected and self.sock:
                    # 1. If buffer has at least 4 bytes (header: 2 bytes len + 2 bytes type)
                    if len(self._rx_buf) >= 4:
                        total_len = struct.unpack(">H", self._rx_buf[:2])[0]
                        if total_len < 4:
                            # Malformed header, skip 2 bytes
                            del self._rx_buf[:2]
                            continue
                        if len(self._rx_buf) >= total_len:
                            msg_type = struct.unpack(">h", self._rx_buf[2:4])[0]
                            payload = bytes(self._rx_buf[4:total_len])
                            del self._rx_buf[:total_len]
                            return Message(msg_type, payload)

                    # 2. Need more data to form complete packet
                    elapsed = time.time() - start_time
                    remaining_time = timeout_val - elapsed
                    if remaining_time <= 0:
                        return None

                    self.sock.settimeout(max(0.05, remaining_time))
                    try:
                        chunk = self.sock.recv(16384)
                    except socket.timeout:
                        return None
                    except Exception:
                        self.close()
                        return None

                    if not chunk:
                        self.close()
                        return None
                    self._rx_buf.extend(chunk)
            except socket.timeout:
                return None
            except Exception:
                self.close()
                return None
            return None

    def send_and_wait(self, msg: Message, expected_type: int = None, timeout: float = 10.0) -> Message:
        """Sends a request message and waits for the specific response opcode."""
        with self.lock:
            if not self.send_message(msg):
                return None
            start_time = time.time()
            while time.time() - start_time < timeout:
                remaining = timeout - (time.time() - start_time)
                if remaining <= 0:
                    break
                res = self.receive_message(timeout=min(2.0, remaining))
                if res is None:
                    if not self.is_connected or not self.sock:
                        return None
                    continue
                if expected_type is None or res.type == expected_type:
                    return res
            return None

    def drain_buffer(self, max_duration: float = 0.5) -> int:
        """
        Đọc và vứt hết tất cả packet đang pending trong TCP buffer.
        Dùng trước khi gửi Quiz request để tránh send_and_wait bị nghẽn bởi packet thế giới.
        Returns: Số packet đã drain.
        """
        with self.lock:
            count = 0
            end_time = time.time() + max_duration
            while time.time() < end_time:
                res = self.receive_message(timeout=0.05)
                if res is None:
                    break
                count += 1
            return count

    def _recv_exact(self, num_bytes: int) -> bytes:
        with self.lock:
            data = bytearray()
            while len(data) < num_bytes:
                packet = self.sock.recv(num_bytes - len(data))
                if not packet:
                    self.close()
                    break
                data.extend(packet)
            return bytes(data)

    def close(self):
        with self.lock:
            self.is_connected = False
            if self.sock:
                try:
                    self.sock.shutdown(socket.SHUT_RDWR)
                except Exception:
                    pass
                try:
                    self.sock.close()
                except Exception:
                    pass
                self.sock = None
            
            with active_conn_lock:
                active_connections.discard(self)

    def __del__(self):
        sock = getattr(self, "sock", None)
        if sock is not None:
            try:
                sock.close()
            except Exception:
                pass
            self.sock = None
