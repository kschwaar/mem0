"""Explicitly scoped local evaluation transport; no retries or redirects.

Storage isolation must be independently provisioned and verified before use.
This client cannot establish isolation merely from a URL or scope prefix.
"""

import hashlib
import json
import math
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from benchmark_compat import validate_result
from benchmark_service_requests import plan


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class ServiceFailure(RuntimeError):
    pass


class LocalServiceClient:
    def __init__(self, base_url, api_key, scope_prefix, timeout=30):
        parsed = urllib.parse.urlsplit(base_url)
        if (parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "::1"}
                or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {"", "/"}):
            raise ValueError("Require an explicit loopback HTTP origin")
        if not isinstance(api_key, str) or not api_key or "\r" in api_key or "\n" in api_key:
            raise ValueError("Require an in-memory API key")
        if not isinstance(scope_prefix, str) or len(scope_prefix) < 16:
            raise ValueError("Require a unique evaluation scope prefix of at least 16 characters")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("Require a positive finite request timeout")
        self.base_url, self._key, self.prefix, self.timeout = base_url.rstrip("/"), api_key, scope_prefix, timeout
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        self.last_evidence = None

    def configuration_fingerprint(self):
        """Read effective configuration without persisting or exposing credentials."""
        request = urllib.request.Request(self.base_url + "/configure", method="GET",
                                         headers={"X-API-Key": self._key})
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                raw = response.read(4 * 1024 * 1024 + 1)
            if len(raw) > 4 * 1024 * 1024:
                raise ValueError("Oversized configuration")
            configuration = json.loads(raw)
            if not isinstance(configuration, dict) or not configuration:
                raise ValueError("Invalid configuration")
            return hashlib.sha256(json.dumps(configuration, sort_keys=True).encode()).hexdigest()
        except Exception:
            raise ServiceFailure("Configuration verification failed; not retried") from None

    def call(self, operation, request):
        self.last_evidence = None
        spec = plan(operation, request)
        # At least one unique run-specific entity ID must constrain every request.
        fields = spec.get("json", spec.get("params", {}))
        scope = fields.get("filters", fields)
        if not any(isinstance(scope.get(k), str) and scope[k].startswith(self.prefix)
                   for k in ("user_id", "agent_id", "run_id")):
            raise ValueError("Request must include this client's evaluation scope prefix")
        url = self.base_url + spec["path"]
        if "params" in spec:
            url += "?" + urllib.parse.urlencode(spec["params"])
        body = json.dumps(spec["json"]).encode() if "json" in spec else None
        req = urllib.request.Request(url, data=body, method=spec["method"],
                                     headers={"X-API-Key": self._key, "Content-Type": "application/json"})
        started = time.perf_counter()
        evidence = {"operation": operation, "status": "error", "http_status": None,
                    "request_id": None, "attempts": 1, "scope": "HTTP client boundary; not SDK/LLM spans"}

        def request_id(headers):
            value = headers.get("X-Request-ID", "") if headers else ""
            return value if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.:-]{1,64}", value) else None

        try:
            with self._opener.open(req, timeout=self.timeout) as response:
                evidence["http_status"] = getattr(response, "status", None)
                evidence["request_id"] = request_id(getattr(response, "headers", None))
                raw = response.read(4 * 1024 * 1024 + 1)
            if len(raw) > 4 * 1024 * 1024:
                raise ServiceFailure("Response exceeded 4 MiB")
            result = validate_result(json.loads(raw))
            evidence["status"] = "success"
            return result
        except urllib.error.HTTPError as exc:
            evidence["http_status"] = exc.code
            evidence["request_id"] = request_id(exc.headers)
            raise ServiceFailure(f"HTTP status {exc.code}; not retried") from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise ServiceFailure("Transport failed; not retried") from None
        except (ValueError, UnicodeError):
            raise ServiceFailure("Malformed service response; not retried") from None
        finally:
            evidence["elapsed_ms"] = (time.perf_counter() - started) * 1000
            self.last_evidence = evidence
