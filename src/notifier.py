from __future__ import annotations

import json
import os
import socket
import ssl
import urllib.request
from dataclasses import dataclass
from contextlib import contextmanager

from .config import NotifyConfig


@dataclass(frozen=True)
class NotifyResult:
    sent: bool
    message: str = ""


class TelegramNotifier:
    def __init__(self, config: NotifyConfig) -> None:
        self.config = config
        self.token = os.getenv(config.telegram_token_env, "")
        self.chat_id = os.getenv(config.telegram_chat_id_env, "")

    def send(self, text: str) -> NotifyResult:
        if not self.config.telegram_enabled:
            return NotifyResult(False, "telegram disabled")
        if not self.token or not self.chat_id:
            return NotifyResult(False, "telegram env missing")
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        payload = json.dumps({"chat_id": self.chat_id, "text": text}).encode("utf-8")
        request = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
        try:
            context = ssl._create_unverified_context() if self.config.telegram_allow_insecure_ssl else None
            with _maybe_force_ipv4(self.config.telegram_force_ipv4):
                with urllib.request.urlopen(request, timeout=10, context=context) as response:
                    ok = 200 <= response.status < 300
                    return NotifyResult(ok, f"status={response.status}")
        except Exception as exc:
            return NotifyResult(False, str(exc))


class NullNotifier:
    def send(self, text: str) -> NotifyResult:
        return NotifyResult(False, "notifier disabled")


@contextmanager
def _maybe_force_ipv4(enabled: bool):
    if not enabled:
        yield
        return
    original = socket.getaddrinfo

    def ipv4_getaddrinfo(*args, **kwargs):
        return [item for item in original(*args, **kwargs) if item[0] == socket.AF_INET]

    socket.getaddrinfo = ipv4_getaddrinfo
    try:
        yield
    finally:
        socket.getaddrinfo = original
