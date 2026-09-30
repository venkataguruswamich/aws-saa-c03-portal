#!/usr/bin/env python3
"""
SAA-C03 Cloud Lab Portal - live lab dependency monitor (Flask backend).

Serves index.html and exposes:
  GET  /api/health         liveness probe (never touches downstream tiers)
  GET  /api/lab/status     cached, non-blocking snapshot of every tier check
  POST /api/lab/refresh    ask the background loop to run a cycle now (rate limited)

Environment variables
  RDS_HOST / RDS_PORT (5432)          RDS endpoint, e.g. mydb.abc123.us-east-1.rds.amazonaws.com
  REDIS_HOST / REDIS_PORT (6379)      ElastiCache (Redis OSS / Valkey) primary endpoint
  REDIS_TLS (false)                   true when ElastiCache in-transit encryption is enabled
  AWS_REGION (us-east-1)              Region used for DynamoDB
  DYNAMODB_TABLE ("")                 optional table for DescribeTable; ListTables(Limit=1) otherwise
  DYNAMODB_ENDPOINT_URL ("")          optional override, e.g. http://localhost:8000 for DynamoDB Local
  CHECK_INTERVAL_SECONDS (10)         background loop period
  SOCKET_TIMEOUT_SECONDS (3)          connect/read timeout for each probe
  MIN_REFRESH_GAP_SECONDS (2)         minimum gap between cycles triggered by the UI
  MAX_CONCURRENT_SOCKETS (6)          hard cap on simultaneously open probe sockets
  PROXY_X_FOR_HOPS (2)                trusted reverse-proxy hops in X-Forwarded-For
  IMDS_ENDPOINT (http://169.254.169.254)
  IMDS_DISABLED (false)               skip IMDS lookups (local development off EC2)
  CORS_ALLOW_ORIGIN (*)               origin allowed to call the API from another host
  HOST (127.0.0.1) / PORT (5000)

Production: gunicorn --workers 1 --threads 8 app:app
  One worker keeps a single monitor thread per container. Do not use --preload:
  threads started before fork() do not survive into the worker processes.
"""
from __future__ import annotations

import atexit
import json
import logging
import math
import os
import socket
import ssl
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import BotoCoreError, ClientError
from flask import Flask, jsonify, request, send_from_directory
from werkzeug.middleware.proxy_fix import ProxyFix


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #
def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def _env_bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


BASE_DIR = Path(__file__).resolve().parent
AWS_REGION = os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "us-east-1"
CHECK_INTERVAL = max(2.0, _env_float("CHECK_INTERVAL_SECONDS", 10.0))
SOCKET_TIMEOUT = max(0.5, _env_float("SOCKET_TIMEOUT_SECONDS", 3.0))
MIN_REFRESH_GAP = max(0.5, _env_float("MIN_REFRESH_GAP_SECONDS", 2.0))
MAX_CONCURRENT_SOCKETS = max(1, _env_int("MAX_CONCURRENT_SOCKETS", 6))
PROXY_HOPS = max(0, _env_int("PROXY_X_FOR_HOPS", 2))
IMDS_ENDPOINT = os.getenv("IMDS_ENDPOINT", "http://169.254.169.254").rstrip("/")
IMDS_DISABLED = _env_bool("IMDS_DISABLED")
CORS_ALLOW_ORIGIN = os.getenv("CORS_ALLOW_ORIGIN", "*")
DYNAMODB_TABLE = os.getenv("DYNAMODB_TABLE", "").strip()
DYNAMODB_ENDPOINT_URL = os.getenv("DYNAMODB_ENDPOINT_URL", "").strip()

# botocore resolves credentials from IMDS on its own. Force IMDSv2-only and give
# it enough time/attempts to survive the extra network hop of a Docker bridge.
os.environ.setdefault("AWS_EC2_METADATA_V1_DISABLED", "true")
os.environ.setdefault("AWS_METADATA_SERVICE_TIMEOUT", "2")
os.environ.setdefault("AWS_METADATA_SERVICE_NUM_ATTEMPTS", "3")

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)-7s [%(threadName)s] %(message)s",
)
log = logging.getLogger("lab-monitor")


@dataclass(frozen=True)
class TierConfig:
    key: str
    name: str
    host: str
    port: int
    probe: str  # "tcp" | "tls" | "redis"
    tls: bool = False
    env_hint: str = ""


def _dynamodb_target() -> tuple[str, int, bool]:
    if DYNAMODB_ENDPOINT_URL:
        parsed = urlparse(DYNAMODB_ENDPOINT_URL)
        tls = parsed.scheme == "https"
        return parsed.hostname or "", parsed.port or (443 if tls else 80), tls
    return f"dynamodb.{AWS_REGION}.amazonaws.com", 443, True


_ddb_host, _ddb_port, _ddb_tls = _dynamodb_target()
_redis_tls = _env_bool("REDIS_TLS")

TIERS: tuple[TierConfig, ...] = (
    TierConfig("rds", "Amazon RDS", os.getenv("RDS_HOST", "").strip(),
               _env_int("RDS_PORT", 5432), "tcp", env_hint="RDS_HOST"),
    TierConfig("elasticache", "Amazon ElastiCache (Redis)", os.getenv("REDIS_HOST", "").strip(),
               _env_int("REDIS_PORT", 6379), "redis", tls=_redis_tls, env_hint="REDIS_HOST"),
    TierConfig("dynamodb", "Amazon DynamoDB", _ddb_host, _ddb_port,
               "tls" if _ddb_tls else "tcp", tls=_ddb_tls, env_hint="DYNAMODB_ENDPOINT_URL"),
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _exc_message(exc: BaseException) -> str:
    """Exact exception text; falls back to repr() when str() is empty."""
    text = str(exc)
    return text if text else repr(exc)


# --------------------------------------------------------------------------- #
# IMDSv2 client (container- and proxy-safe)
# --------------------------------------------------------------------------- #
class IMDSUnavailable(RuntimeError):
    pass


class IMDSv2Client:
    """
    Session-token IMDS client.

    * Tokens are cached and refreshed 60 s before expiry; a 401 forces a refresh.
    * Requests bypass HTTP(S)_PROXY settings: the link-local IMDS address must
      never be sent through a forward proxy.
    * No X-Forwarded-For header is ever sent. IMDS rejects token PUT requests
      that carry X-Forwarded-For, which is what happens when a reverse proxy
      mistakenly forwards to 169.254.169.254.
    * Inside Docker (bridge networking) the token response needs one extra hop.
      With the default hop limit of 1 the PUT times out; the diagnostic below
      tells the operator to raise the hop limit to 2.
    """

    TOKEN_PATH = "/latest/api/token"

    def __init__(self, base_url: str, ttl_seconds: int = 21600,
                 timeout: float = 1.0, attempts: int = 2) -> None:
        self._base = base_url
        self._ttl = ttl_seconds
        self._timeout = timeout
        self._attempts = attempts
        self._token: str | None = None
        self._token_expiry = 0.0
        self._token_lock = threading.Lock()
        self._cache: dict | None = None
        self._cache_until = 0.0
        self._cache_lock = threading.Lock()
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def _open(self, req: urllib.request.Request) -> str:
        with self._opener.open(req, timeout=self._timeout) as resp:
            return resp.read().decode("utf-8")

    def _fetch_token(self) -> str:
        req = urllib.request.Request(
            self._base + self.TOKEN_PATH,
            method="PUT",
            headers={"X-aws-ec2-metadata-token-ttl-seconds": str(self._ttl)},
        )
        return self._open(req)

    @staticmethod
    def _diagnose(exc: BaseException | None) -> str:
        if exc is None:
            return "IMDSv2 token request failed for an unknown reason"
        base = f"{type(exc).__name__}: {_exc_message(exc)}"
        reason = getattr(exc, "reason", None)
        timed_out = isinstance(exc, (socket.timeout, TimeoutError)) or isinstance(
            reason, (socket.timeout, TimeoutError))
        if isinstance(exc, urllib.error.HTTPError):
            if exc.code == 403:
                return (f"{base} - IMDS refused the token request. The metadata endpoint may be "
                        "disabled (http-endpoint=disabled), or the PUT carried an X-Forwarded-For "
                        "header (IMDS rejects proxied token requests).")
            if exc.code == 405:
                return f"{base} - IMDS rejected the HTTP method; token requests must use PUT."
            return base
        if timed_out:
            return (f"{base} - No reply to PUT {IMDSv2Client.TOKEN_PATH}. When running in a Docker "
                    "container on EC2 the IMDSv2 response hop limit must be 2: "
                    "aws ec2 modify-instance-metadata-options --instance-id <instance-id> "
                    "--http-put-response-hop-limit 2 --http-endpoint enabled. "
                    "Off EC2, set IMDS_DISABLED=true.")
        return f"{base} - Instance metadata service is unreachable from this network namespace."

    def _get_token(self, force: bool = False) -> str:
        with self._token_lock:
            now = time.monotonic()
            if not force and self._token and now < self._token_expiry - 60:
                return self._token
            last_exc: BaseException | None = None
            for attempt in range(self._attempts):
                try:
                    token = self._fetch_token()
                    self._token = token
                    self._token_expiry = time.monotonic() + self._ttl
                    return token
                except urllib.error.HTTPError as exc:
                    exc.close()  # release the response socket held by the error object
                    last_exc = exc
                    if exc.code in (403, 405):
                        break  # deterministic refusals: retrying cannot succeed
                except OSError as exc:  # URLError, timeouts, connection refused
                    last_exc = exc
                time.sleep(0.2 * (2 ** attempt))
            self._token = None
            raise IMDSUnavailable(self._diagnose(last_exc)) from last_exc

    def token_ttl_remaining(self) -> int | None:
        with self._token_lock:
            if not self._token:
                return None
            return max(0, int(self._token_expiry - time.monotonic()))

    def get(self, path: str) -> str:
        for force_refresh in (False, True):
            token = self._get_token(force=force_refresh)
            req = urllib.request.Request(
                f"{self._base}/latest/{path.lstrip('/')}",
                headers={"X-aws-ec2-metadata-token": token},
            )
            try:
                return self._open(req)
            except urllib.error.HTTPError as exc:
                exc.close()
                if exc.code == 401 and not force_refresh:
                    continue  # token expired or instance metadata options changed
                raise
        raise IMDSUnavailable("IMDS returned 401 Unauthorized after a token refresh")

    def snapshot(self) -> dict:
        now = time.monotonic()
        with self._cache_lock:
            if self._cache is not None and now < self._cache_until:
                return dict(self._cache)

        result: dict = {
            "available": False, "instance_id": None, "availability_zone": None,
            "region": None, "instance_type": None, "private_ip": None,
            "token_ttl_remaining_s": None, "error": None, "checked_at": _now_iso(),
        }
        cache_for = 30.0
        if IMDS_DISABLED:
            result["error"] = "IMDS lookups disabled (IMDS_DISABLED=true)"
            cache_for = 3600.0
        else:
            try:
                doc = json.loads(self.get("dynamic/instance-identity/document"))
                result.update(
                    available=True,
                    instance_id=doc.get("instanceId"),
                    availability_zone=doc.get("availabilityZone"),
                    region=doc.get("region"),
                    instance_type=doc.get("instanceType"),
                    private_ip=doc.get("privateIp"),
                    token_ttl_remaining_s=self.token_ttl_remaining(),
                )
                cache_for = 300.0
            except IMDSUnavailable as exc:
                result["error"] = _exc_message(exc)
            except urllib.error.HTTPError as exc:
                exc.close()
                result["error"] = f"HTTPError: {_exc_message(exc)}"
            except (OSError, ValueError) as exc:
                result["error"] = f"{type(exc).__name__}: {_exc_message(exc)}"

        with self._cache_lock:
            self._cache = dict(result)
            self._cache_until = time.monotonic() + cache_for
        return result


# --------------------------------------------------------------------------- #
# Raw socket probes
# --------------------------------------------------------------------------- #
# Health probes deliberately open a fresh socket per check: re-using a pooled
# socket would report "UP" for a tier whose listener has since died. Resource
# safety comes from (1) a bounded worker pool, (2) a semaphore capping how many
# probe sockets can be open at once, and (3) explicit shutdown+close in finally.
_socket_gate = threading.BoundedSemaphore(MAX_CONCURRENT_SOCKETS)


def _open_socket(host: str, port: int, use_tls: bool) -> socket.socket:
    raw = socket.create_connection((host, port), timeout=SOCKET_TIMEOUT)
    if not use_tls:
        return raw
    try:
        ctx = ssl.create_default_context()
        return ctx.wrap_socket(raw, server_hostname=host)
    except BaseException:
        raw.close()
        raise


def _shutdown_and_close(sock: socket.socket) -> None:
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass  # peer already closed / never fully connected
    finally:
        sock.close()


def _recv_line(sock: socket.socket, limit: int = 512) -> bytes:
    buf = bytearray()
    while b"\r\n" not in buf and len(buf) < limit:
        chunk = sock.recv(128)
        if not chunk:
            raise ConnectionError("Redis closed the connection before replying to PING")
        buf.extend(chunk)
    return bytes(buf)


def _socket_probe(cfg: TierConfig) -> str:
    with _socket_gate:
        sock = _open_socket(cfg.host, cfg.port, cfg.tls)
        try:
            if cfg.probe == "redis":
                sock.sendall(b"*1\r\n$4\r\nPING\r\n")
                reply = _recv_line(sock)
                text = reply.decode("utf-8", errors="replace").strip()
                if reply.startswith(b"+PONG"):
                    return "RESP PING -> +PONG"
                if reply.startswith(b"-NOAUTH") or b"AUTH" in reply.upper():
                    return f"Listener reachable; AUTH required ({text})"
                raise ConnectionError(f"Unexpected Redis reply to PING: {text!r}")
            if cfg.tls:
                return f"TCP + TLS handshake OK ({sock.version()})"
            return "TCP three-way handshake OK"
        finally:
            _shutdown_and_close(sock)


# --------------------------------------------------------------------------- #
# DynamoDB API probe (shared boto3 client with a bounded connection pool)
# --------------------------------------------------------------------------- #
_ddb_client = None
_ddb_client_lock = threading.Lock()


def _dynamodb_client():
    global _ddb_client
    with _ddb_client_lock:
        if _ddb_client is None:
            _ddb_client = boto3.session.Session().client(
                "dynamodb",
                region_name=AWS_REGION,
                endpoint_url=DYNAMODB_ENDPOINT_URL or None,
                config=BotoConfig(
                    connect_timeout=SOCKET_TIMEOUT,
                    read_timeout=SOCKET_TIMEOUT,
                    retries={"max_attempts": 1, "mode": "standard"},
                    max_pool_connections=MAX_CONCURRENT_SOCKETS,
                    tcp_keepalive=True,
                ),
            )
        return _ddb_client


def _dynamodb_api_probe() -> str:
    client = _dynamodb_client()
    if DYNAMODB_TABLE:
        table = client.describe_table(TableName=DYNAMODB_TABLE)["Table"]
        return f"DescribeTable {DYNAMODB_TABLE}: {table.get('TableStatus', 'UNKNOWN')}"
    names = client.list_tables(Limit=1).get("TableNames", [])
    return f"ListTables OK ({'tables present' if names else 'no tables'})"


# --------------------------------------------------------------------------- #
# Tier check orchestration
# --------------------------------------------------------------------------- #
def _base_result(cfg: TierConfig) -> dict:
    return {
        "key": cfg.key, "name": cfg.name,
        "endpoint": f"{cfg.host}:{cfg.port}" if cfg.host else "(not configured)",
        "probe": cfg.probe + (" + boto3" if cfg.key == "dynamodb" else ""),
        "status": "UNKNOWN", "latency_ms": None, "detail": "",
        "errors": [], "checked_at": _now_iso(), "consecutive_failures": 0,
    }


def _add_error(result: dict, source: str, exc: BaseException) -> None:
    result["errors"].append({
        "source": source, "type": type(exc).__name__, "message": _exc_message(exc),
    })


def run_tier_check(cfg: TierConfig) -> dict:
    result = _base_result(cfg)
    if not cfg.host:
        result["status"] = "UNCONFIGURED"
        result["detail"] = f"Set {cfg.env_hint} to enable this check"
        return result

    started = time.perf_counter()
    try:
        result["detail"] = _socket_probe(cfg)
        result["status"] = "UP"
    except (OSError, ssl.SSLError, ConnectionError) as exc:  # includes socket.timeout / gaierror
        result["status"] = "DOWN"
        result["detail"] = "Socket probe failed"
        _add_error(result, "socket", exc)
    result["latency_ms"] = round((time.perf_counter() - started) * 1000, 1)

    if cfg.key == "dynamodb" and result["status"] == "UP":
        try:
            result["detail"] += f"; {_dynamodb_api_probe()}"
        except (ClientError, BotoCoreError) as exc:
            result["status"] = "DEGRADED"
            _add_error(result, "boto3", exc)
    return result


class LabMonitor:
    """Background loop that probes every tier concurrently and caches results."""

    def __init__(self, tiers: tuple[TierConfig, ...], imds: IMDSv2Client, interval: float) -> None:
        self._tiers = tiers
        self._imds = imds
        self._interval = interval
        self._pool = ThreadPoolExecutor(max_workers=len(tiers) + 1, thread_name_prefix="probe")
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._checking = False
        self._cycle = 0
        self._last_run = 0.0
        self._last_cycle_ms: float | None = None
        self._failures = {t.key: 0 for t in tiers}
        self._errors: deque[dict] = deque(maxlen=100)
        self._results = [dict(_base_result(t), status="PENDING") for t in tiers]
        self._imds_state: dict = {"available": False, "error": "Not checked yet"}
        self._thread = threading.Thread(target=self._loop, name="lab-monitor", daemon=True)

    def start(self) -> None:
        if not self._thread.is_alive():
            self._thread.start()
            log.info("Lab monitor started (interval=%.1fs, timeout=%.1fs, socket cap=%d)",
                     self._interval, SOCKET_TIMEOUT, MAX_CONCURRENT_SOCKETS)

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread.is_alive():
            self._thread.join(timeout=SOCKET_TIMEOUT * 4 + 5)
        self._pool.shutdown(wait=True, cancel_futures=True)

    def request_refresh(self) -> tuple[bool, float]:
        with self._lock:
            if self._checking:
                return False, 1.0
            gap = time.monotonic() - self._last_run
            if gap < MIN_REFRESH_GAP:
                return False, MIN_REFRESH_GAP - gap
        self._wake.set()
        return True, 0.0

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._run_cycle()
            except Exception:  # keep the loop alive whatever happens
                log.exception("Monitor cycle crashed")
            self._wake.wait(self._interval)
            self._wake.clear()

    def _run_cycle(self) -> None:
        started = time.perf_counter()
        with self._lock:
            self._checking = True
            self._last_run = time.monotonic()
        try:
            try:
                futures = {self._pool.submit(run_tier_check, cfg): cfg for cfg in self._tiers}
                imds_future = self._pool.submit(self._imds.snapshot)
            except RuntimeError:
                return  # pool already shut down
            deadline = SOCKET_TIMEOUT * 4 + 2
            done, _ = wait([*futures, imds_future], timeout=deadline)

            results: list[dict] = []
            for fut, cfg in futures.items():
                if fut in done:
                    try:
                        results.append(fut.result())
                        continue
                    except Exception as exc:
                        log.exception("Probe for %s raised", cfg.key)
                        failure = exc
                else:
                    fut.cancel()
                    failure = TimeoutError(f"Check exceeded the {deadline:.0f}s cycle deadline")
                res = _base_result(cfg)
                res["status"] = "DOWN"
                res["detail"] = "Probe did not complete"
                _add_error(res, "internal", failure)
                results.append(res)

            if imds_future in done and imds_future.exception() is None:
                imds_state = imds_future.result()
            else:
                imds_state = {"available": False, "error": "IMDS lookup did not complete in time"}

            with self._lock:
                for res in results:
                    failing = res["status"] in ("DOWN", "DEGRADED")
                    self._failures[res["key"]] = self._failures[res["key"]] + 1 if failing else 0
                    res["consecutive_failures"] = self._failures[res["key"]]
                    for err in res["errors"]:
                        self._record_error(res, err)
                self._results = results
                self._imds_state = imds_state
                self._cycle += 1
                self._last_cycle_ms = round((time.perf_counter() - started) * 1000, 1)
        finally:
            with self._lock:
                self._checking = False

    def _record_error(self, res: dict, err: dict) -> None:
        """Collapse repeats of the same error so the log stays readable. Caller holds the lock."""
        now = _now_iso()
        for entry in reversed(self._errors):
            if (entry["tier"] == res["key"] and entry["source"] == err["source"]
                    and entry["message"] == err["message"]):
                entry["count"] += 1
                entry["last_seen"] = now
                return
        self._errors.append({
            "tier": res["key"], "tier_name": res["name"], "source": err["source"],
            "type": err["type"], "message": err["message"],
            "first_seen": now, "last_seen": now, "count": 1,
        })
        log.warning("%s %s %s: %s", res["name"], err["source"], err["type"], err["message"])

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "generated_at": _now_iso(),
                "cycle": self._cycle,
                "checking": self._checking,
                "interval_seconds": self._interval,
                "last_cycle_ms": self._last_cycle_ms,
                "tiers": [dict(r, errors=list(r["errors"])) for r in self._results],
                "errors": [dict(e) for e in reversed(self._errors)],
                "imds": dict(self._imds_state),
            }


def _open_fd_count() -> int | None:
    for path in ("/proc/self/fd", "/dev/fd"):
        try:
            return len(os.listdir(path))
        except OSError:
            continue
    return None


# --------------------------------------------------------------------------- #
# Flask application
# --------------------------------------------------------------------------- #
def create_app() -> Flask:
    app = Flask(__name__, static_folder=None)
    # Trust exactly PROXY_HOPS proxies (e.g. ALB -> nginx -> app). With x_for=2
    # Werkzeug takes the second-from-right X-Forwarded-For entry as the client
    # IP and ignores anything further left, which a client could have spoofed.
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=PROXY_HOPS, x_proto=1, x_host=1)

    monitor = LabMonitor(TIERS, IMDSv2Client(IMDS_ENDPOINT), CHECK_INTERVAL)
    monitor.start()
    atexit.register(monitor.stop)
    app.extensions["lab_monitor"] = monitor

    @app.after_request
    def _headers(resp):
        if request.path.startswith("/api/"):
            resp.headers["Access-Control-Allow-Origin"] = CORS_ALLOW_ORIGIN
            resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
            resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
            resp.headers["Cache-Control"] = "no-store"
        resp.headers["X-Content-Type-Options"] = "nosniff"
        return resp

    @app.get("/")
    @app.get("/index.html")
    def index():
        return send_from_directory(BASE_DIR, "index.html")

    # PWA files: manifest, service worker (offline mode) and icons.
    @app.get("/manifest.webmanifest")
    def manifest():
        return send_from_directory(BASE_DIR, "manifest.webmanifest",
                                   mimetype="application/manifest+json")

    @app.get("/sw.js")
    def service_worker():
        resp = send_from_directory(BASE_DIR, "sw.js", mimetype="text/javascript")
        resp.headers["Cache-Control"] = "no-cache"
        return resp

    @app.get("/favicon.ico")
    def favicon():
        return send_from_directory(BASE_DIR / "icons", "icon-192.png", mimetype="image/png")

    @app.get("/icons/<path:name>")
    def icons(name):
        return send_from_directory(BASE_DIR / "icons", name)

    @app.get("/api/health")
    def health():
        return jsonify(ok=True, time=_now_iso())

    @app.get("/api/lab/status")
    def lab_status():
        data = monitor.snapshot()
        orig = request.environ.get("werkzeug.proxy_fix.orig", {})
        data["client"] = {
            "resolved_client_ip": request.remote_addr,
            "x_forwarded_for": request.headers.get("X-Forwarded-For"),
            "proxy_peer": orig.get("REMOTE_ADDR", request.remote_addr),
            "trusted_hops": PROXY_HOPS,
            "scheme": request.scheme,
        }
        data["runtime"] = {
            "open_fds": _open_fd_count(),
            "active_threads": threading.active_count(),
            "max_concurrent_sockets": MAX_CONCURRENT_SOCKETS,
            "socket_timeout_s": SOCKET_TIMEOUT,
            "region": AWS_REGION,
        }
        return jsonify(data)

    @app.route("/api/lab/refresh", methods=["POST", "OPTIONS"])
    def lab_refresh():
        if request.method == "OPTIONS":
            return ("", 204)
        accepted, retry_after = monitor.request_refresh()
        if accepted:
            return jsonify(accepted=True, message="Check cycle scheduled"), 202
        resp = jsonify(accepted=False, retry_after=round(retry_after, 1),
                       message=f"Refresh rate limited; retry in {retry_after:.1f}s")
        resp.status_code = 429
        resp.headers["Retry-After"] = str(max(1, math.ceil(retry_after)))
        return resp

    return app


app = create_app()

if __name__ == "__main__":
    app.run(host=os.getenv("HOST", "127.0.0.1"), port=_env_int("PORT", 5000),
            debug=False, use_reloader=False, threaded=True)
