"""Cliente HTTP com retry inline (sem deps externas alem de requests)."""
import os
import time
import requests

USER_AGENT = os.environ.get(
    "USER_AGENT",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
TIMEOUT = int(os.environ.get("HTTP_TIMEOUT_SEC", "30"))
RETRIES = int(os.environ.get("HTTP_RETRIES", "3"))


def _session():
    s = requests.Session()
    s.headers.update({
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9,pt-BR;q=0.8",
    })
    return s


def fetch(url, method="GET", **kwargs):
    """Fetch com retry exponencial."""
    kwargs.setdefault("timeout", TIMEOUT)
    s = _session()
    last_exc = None
    for i in range(RETRIES):
        try:
            resp = s.request(method, url, **kwargs)
            resp.raise_for_status()
            return resp
        except requests.RequestException as e:
            last_exc = e
            if i < RETRIES - 1:
                time.sleep(2 ** i + 1)
    raise last_exc
