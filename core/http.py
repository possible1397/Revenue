"""有禮貌的 HTTP：固定間隔、重試與退避、User-Agent。"""
import time
import logging
import requests

log = logging.getLogger(__name__)


class PoliteSession:
    def __init__(self, user_agent, min_interval=1.0, retries=3, backoff=5, timeout=30):
        self.s = requests.Session()
        self.s.headers["User-Agent"] = user_agent
        self.min_interval, self.retries, self.backoff, self.timeout = min_interval, retries, backoff, timeout
        self._last = 0.0

    def get(self, url, **kw):
        return self._request("GET", url, **kw)

    def post(self, url, **kw):
        return self._request("POST", url, **kw)

    def _request(self, method, url, **kw):
        last_err = None
        for attempt in range(self.retries):
            wait = self.min_interval - (time.time() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.time()
            try:
                r = self.s.request(method, url, timeout=self.timeout, **kw)
                if r.status_code == 200:
                    return r
                last_err = f"HTTP {r.status_code}"
                if r.status_code == 404:
                    break  # 找不到不用重試
            except requests.RequestException as e:
                last_err = repr(e)
            log.warning("%s %s 失敗（%s），第 %d 次", method, url, last_err, attempt + 1)
            time.sleep(self.backoff * (attempt + 1))
        raise RuntimeError(f"{method} {url} 失敗：{last_err}")
