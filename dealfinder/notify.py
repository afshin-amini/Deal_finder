"""Push notifications via ntfy.sh (or a self-hosted ntfy server)."""

from __future__ import annotations

import logging

import requests

log = logging.getLogger(__name__)


class Ntfy:
    def __init__(self, topic: str | None, server: str = "https://ntfy.sh", token: str | None = None, dry_run: bool = False):
        self.topic = topic
        self.server = server.rstrip("/")
        self.token = token
        self.dry_run = dry_run or not topic

    def send(self, title: str, message: str, *, click: str | None = None, priority: int = 3, tags: list[str] | None = None) -> bool:
        if self.dry_run:
            print(f"\n[ntfy dry-run] {title}\n{message}\n{click or ''}")
            return True
        # HTTP headers must be latin-1; ntfy accepts RFC 2047 encoded titles for anything else.
        try:
            title.encode("latin-1")
            hdr_title = title
        except UnicodeEncodeError:
            import base64
            hdr_title = "=?UTF-8?B?" + base64.b64encode(title.encode()).decode() + "?="
        headers = {"Title": hdr_title, "Priority": str(priority)}
        if tags:
            headers["Tags"] = ",".join(tags)
        if click:
            headers["Click"] = click
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        try:
            r = requests.post(f"{self.server}/{self.topic}", data=message.encode("utf-8"), headers=headers, timeout=20)
            r.raise_for_status()
            return True
        except requests.RequestException as exc:
            log.error("ntfy send failed: %s", exc)
            return False
