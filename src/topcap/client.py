"""TikHub REST 客户端。无状态：每次调用独立，超时、限流、5xx 一律退避重试。见 ADR 0004。"""
from __future__ import annotations

import random
import time
from typing import Any

import httpx

from . import __version__

RETRY_STATUS = {408, 429, 500, 502, 503, 504}
MAX_ATTEMPTS = 4


class TikHubError(RuntimeError):
    def __init__(self, message: str, *, status: int | None = None, path: str = ""):
        super().__init__(message)
        self.status = status
        self.path = path


class TikHub:
    def __init__(self, key: str, base: str = "https://api.tikhub.io", timeout: float = 45.0):
        if not key:
            raise TikHubError("缺少 TikHub API key，先运行 topcap init")
        self._client = httpx.Client(
            base_url=base, timeout=timeout,
            headers={"Authorization": f"Bearer {key}", "User-Agent": f"topcap/{__version__}"},
        )
        self.calls = 0

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "TikHub":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def call(self, method: str, path: str, *, params: dict | None = None,
             json_body: dict | None = None) -> dict[str, Any]:
        last: Exception | None = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                self.calls += 1
                r = self._client.request(method, path, params=params, json=json_body)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last = exc
            else:
                if r.status_code == 200:
                    payload = r.json()
                    if payload.get("code") not in (200, None):
                        raise TikHubError(
                            f"{payload.get('code')}: {payload.get('message_zh') or payload.get('message')}",
                            status=payload.get("code"), path=path)
                    return payload
                if r.status_code not in RETRY_STATUS:
                    raise TikHubError(f"HTTP {r.status_code}: {r.text[:200]}",
                                      status=r.status_code, path=path)
                last = TikHubError(f"HTTP {r.status_code}", status=r.status_code, path=path)
            if attempt < MAX_ATTEMPTS:
                time.sleep(min(2 ** attempt * 0.5, 8) + random.uniform(0, 0.4))
        raise TikHubError(f"{path} 重试 {MAX_ATTEMPTS} 次仍失败: {last}", path=path)

    def get(self, path: str, **params: Any) -> dict[str, Any]:
        return self.call("GET", path, params=params)

    def post(self, path: str, **body: Any) -> dict[str, Any]:
        return self.call("POST", path, json_body=body)
