"""Polite HTTP: robots.txt enforcement, per-host rate limiting, retries with backoff."""

from __future__ import annotations

import logging
import random
import time
import urllib.robotparser
from urllib.parse import urlsplit

import requests

log = logging.getLogger(__name__)

DEFAULT_UA = "WhiskyDealFinder/0.1 (personal price tracker; +https://github.com/afshin-amini/deal_finder)"


class RobotsDisallowed(Exception):
    pass


class PoliteSession:
    def __init__(
        self,
        user_agent: str = DEFAULT_UA,
        min_delay: float = 3.0,
        timeout: float = 30.0,
        max_retries: int = 3,
    ):
        self.user_agent = user_agent
        self.min_delay = min_delay
        self.timeout = timeout
        self.max_retries = max_retries
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": user_agent, "Accept-Language": "en-CA,en;q=0.8"})
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self._last_hit: dict[str, float] = {}
        self._host_delay: dict[str, float] = {}

    # ---- robots.txt -------------------------------------------------------

    def _robots_for(self, url: str) -> urllib.robotparser.RobotFileParser | None:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin in self._robots:
            return self._robots[origin]
        rp = urllib.robotparser.RobotFileParser()
        robots_url = origin + "/robots.txt"
        try:
            self._wait(parts.netloc)
            resp = self.session.get(robots_url, timeout=self.timeout)
            self._last_hit[parts.netloc] = time.monotonic()
            if resp.status_code in (401, 403):
                # Per the robots spec, an auth-blocked robots.txt means "disallow all".
                rp.disallow_all = True
            elif resp.status_code >= 400:
                rp.allow_all = True
            else:
                rp.parse(resp.text.splitlines())
                delay = rp.crawl_delay(self.user_agent)
                if delay:
                    self._host_delay[parts.netloc] = max(float(delay), self.min_delay)
        except requests.RequestException as exc:
            log.warning("robots.txt fetch failed for %s (%s); treating as disallowed", origin, exc)
            rp.disallow_all = True
        self._robots[origin] = rp
        return rp

    def allowed(self, url: str) -> bool:
        rp = self._robots_for(url)
        return rp is None or rp.can_fetch(self.user_agent, url)

    # ---- rate limiting ----------------------------------------------------

    def _wait(self, host: str) -> None:
        delay = self._host_delay.get(host, self.min_delay)
        last = self._last_hit.get(host)
        if last is not None:
            remaining = delay - (time.monotonic() - last)
            if remaining > 0:
                time.sleep(remaining + random.uniform(0, 0.5))

    # ---- fetch ------------------------------------------------------------

    def get(self, url: str, *, params: dict | None = None, accept: str | None = None) -> requests.Response:
        full = requests.Request("GET", url, params=params).prepare().url or url
        if not self.allowed(full):
            raise RobotsDisallowed(full)
        host = urlsplit(full).netloc
        headers = {"Accept": accept} if accept else None
        last_exc: Exception | None = None
        for attempt in range(self.max_retries):
            self._wait(host)
            try:
                resp = self.session.get(full, headers=headers, timeout=self.timeout)
            except requests.RequestException as exc:
                last_exc = exc
                self._last_hit[host] = time.monotonic()
                time.sleep(2 ** (attempt + 1))
                continue
            self._last_hit[host] = time.monotonic()
            if resp.status_code == 429 or resp.status_code >= 500:
                retry_after = resp.headers.get("Retry-After", "")
                wait = int(retry_after) if retry_after.isdigit() else 2 ** (attempt + 2)
                log.info("%s -> %s, backing off %ss", full, resp.status_code, wait)
                # Slow down for the rest of the run too.
                self._host_delay[host] = max(self._host_delay.get(host, self.min_delay) * 2, wait)
                time.sleep(wait)
                last_exc = requests.HTTPError(f"{resp.status_code} for {full}")
                continue
            return resp
        raise last_exc or RuntimeError(f"failed to fetch {full}")
