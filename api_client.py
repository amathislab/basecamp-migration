"""
Rate-limited Basecamp 4 API client with automatic token refresh.
"""

import json
import time
import requests

from env import USER_AGENT

CONFIG_FILE = "config.json"


class BasecampClient:
    """HTTP client for Basecamp 4 API with rate limiting and pagination."""

    def __init__(self, account_id: int, access_token: str | None = None):
        self.account_id = account_id
        self.base_url = f"https://3.basecampapi.com/{account_id}"
        self._backoff = 1
        if access_token:
            self.access_token = access_token
        else:
            self._load_config()

    def _load_config(self):
        with open(CONFIG_FILE) as f:
            config = json.load(f)
        self.access_token = config["access_token"]

    @property
    def _headers(self):
        return {
            "Authorization": f"Bearer {self.access_token}",
            "User-Agent": USER_AGENT,
            "Content-Type": "application/json",
        }

    def _request(self, method: str, url: str, **kwargs) -> requests.Response:
        """Make a rate-limited request with retry on 429/5xx."""
        if not url.startswith("http"):
            url = f"{self.base_url}{url}"

        while True:
            resp = requests.request(method, url, headers=self._headers, **kwargs)

            if resp.status_code == 429:
                wait = int(resp.headers.get("Retry-After", 10))
                print(f"  Rate limited, waiting {wait}s...")
                time.sleep(wait)
                continue

            if resp.status_code >= 500:
                print(f"  Server error {resp.status_code}, backing off {self._backoff}s...")
                time.sleep(self._backoff)
                self._backoff = min(self._backoff * 2, 60)
                continue

            self._backoff = 1  # reset on success
            return resp

    def get(self, path: str, **kwargs) -> requests.Response:
        return self._request("GET", path, **kwargs)

    def post(self, path: str, data: dict = None, **kwargs) -> requests.Response:
        return self._request("POST", path, json=data, **kwargs)

    def put(self, path: str, data: dict = None, **kwargs) -> requests.Response:
        return self._request("PUT", path, json=data, **kwargs)

    def get_json(self, path: str) -> dict | list:
        resp = self.get(path)
        resp.raise_for_status()
        return resp.json()

    def get_all(self, path: str, skip_404: bool = True) -> list | None:
        """Fetch all pages of a paginated endpoint.
        If skip_404=True, returns None on 404 instead of raising."""
        items = []
        url = path if path.startswith("http") else f"{self.base_url}{path}"
        while url:
            resp = self.get(url)
            if skip_404 and resp.status_code == 404:
                return None
            resp.raise_for_status()
            items.extend(resp.json())
            url = resp.links.get("next", {}).get("url")
        return items

    def post_json(self, path: str, data: dict) -> dict:
        resp = self.post(path, data=data)
        resp.raise_for_status()
        return resp.json()

    def upload_file(self, filename: str, content: bytes, content_type: str) -> str:
        """Upload a file and return its attachable_sgid."""
        url = f"{self.base_url}/attachments.json?name={filename}"
        headers = {**self._headers, "Content-Type": content_type, "Content-Length": str(len(content))}
        while True:
            resp = requests.post(url, headers=headers, data=content)
            if resp.status_code == 429:
                time.sleep(int(resp.headers.get("Retry-After", 10)))
                continue
            resp.raise_for_status()
            return resp.json()["attachable_sgid"]
