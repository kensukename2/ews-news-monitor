"""A polite HTTP client: identifies itself, obeys robots.txt and rate limits."""

import time
import urllib.robotparser
from typing import Dict
from urllib.parse import urlsplit

import requests


class RobotsDisallowed(Exception):
    pass


class PoliteSession:
    def __init__(self, user_agent: str, timeout: int = 20, min_interval: float = 1.5):
        self.user_agent = user_agent
        self.timeout = timeout
        self.min_interval = min_interval
        self.session = requests.Session()
        self.session.headers["User-Agent"] = user_agent
        self._robots: Dict[str, urllib.robotparser.RobotFileParser] = {}
        self._last: Dict[str, float] = {}

    def _robots_for(self, url: str) -> urllib.robotparser.RobotFileParser:
        parts = urlsplit(url)
        base = f"{parts.scheme}://{parts.netloc}"
        if base not in self._robots:
            rp = urllib.robotparser.RobotFileParser()
            try:
                r = self.session.get(base + "/robots.txt", timeout=self.timeout)
                # Per the robots standard: 4xx = no restrictions; 5xx = assume disallowed.
                if r.status_code >= 500:
                    rp.parse(["User-agent: *", "Disallow: /"])
                elif r.status_code == 200:
                    rp.parse(r.text.splitlines())
                else:
                    rp.parse([])
            except requests.RequestException:
                rp.parse([])
            self._robots[base] = rp
        return self._robots[base]

    def allowed(self, url: str) -> bool:
        return self._robots_for(url).can_fetch(self.user_agent, url)

    def _wait(self, host: str, interval: float):
        last = self._last.get(host)
        if last is not None:
            delay = interval - (time.monotonic() - last)
            if delay > 0:
                time.sleep(delay)
        self._last[host] = time.monotonic()

    def get(self, url: str, check_robots: bool = True, min_interval: float = None,
            **kwargs) -> requests.Response:
        if check_robots and not self.allowed(url):
            raise RobotsDisallowed(f"robots.txt disallows {url}")
        self._wait(urlsplit(url).netloc, min_interval or self.min_interval)
        kwargs.setdefault("timeout", self.timeout)
        return self.session.get(url, **kwargs)
