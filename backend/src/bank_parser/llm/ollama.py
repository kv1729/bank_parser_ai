"""
Minimal client for a local Ollama server (Stage 10, D-023).

Statement content may be sent to the model only because it runs on this
machine, so the client refuses any host other than the loopback interface:
a mis-typed setting cannot send a statement anywhere else. Stdlib only.
"""
from dataclasses import dataclass
import json
import time
from urllib.parse import urlparse
import urllib.error
import urllib.request

LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1")


class LLMError(RuntimeError):
    pass


class ServerFailure(LLMError):
    """The model server failed (HTTP 5xx, connection refused or reset), not the answer: worth one retry.
    Seen once: the Ollama runner crashed while loading the model on a busy laptop (D-028)."""


@dataclass(frozen=True)
class LLMReply:
    data: dict
    prompt_tokens: int
    output_tokens: int
    seconds: float
    retries: int = 0            # server failures retried before this answer (D-028)

    @property
    def tokens(self):
        return self.prompt_tokens + self.output_tokens


def require_local(url):
    host = urlparse(url).hostname
    if host not in LOCAL_HOSTS:
        raise LLMError(f"refusing non-local model server {host!r}: statements may only go to a local model")
    return url.rstrip("/")


class OllamaClient:
    # A busy laptop answered /api/tags in 5.5 s, and the model was skipped as "not installed" (D-028).
    TAGS_TIMEOUT = 30.0

    def __init__(self, url="http://127.0.0.1:11434", timeout=240.0, retry_pause=20.0):
        self.url = require_local(url)
        self.timeout = timeout
        self.retry_pause = retry_pause

    def _call(self, path, payload=None, timeout=None):
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(self.url + path, data=data, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            kind = ServerFailure if e.code >= 500 else LLMError
            raise kind(f"model server call {path} failed: HTTP {e.code}") from None
        except ConnectionError as e:
            raise ServerFailure(f"model server call {path} failed: {type(e).__name__}") from None
        except urllib.error.URLError as e:
            kind = ServerFailure if isinstance(e.reason, ConnectionError) else LLMError
            raise kind(f"model server call {path} failed: {type(e).__name__}") from None
        except (TimeoutError, json.JSONDecodeError) as e:      # a timeout is not retried: it already took long
            raise LLMError(f"model server call {path} failed: {type(e).__name__}") from None

    def models(self):
        """{name: digest} of the models installed locally."""
        return {m["name"]: m.get("digest", "") for m in self._call("/api/tags", timeout=self.TAGS_TIMEOUT).get("models", [])}

    def has_model(self, model):
        """Installed and the server answered; a failed check is retried once (a busy server)."""
        for attempt in range(2):
            try:
                return model in self.models()
            except ServerFailure:
                if attempt == 0:
                    time.sleep(self.retry_pause)
            except LLMError:
                return False
        return False

    def chat_json(self, model, system, prompt, schema, seed=7, num_ctx=2048, max_tokens=200):
        """One deterministic call whose answer is forced to match `schema`; returns an LLMReply.
        `max_tokens` caps the answer: small models can otherwise repeat list items until the
        context is full (seen with qwen3.5:4b, D-023). A labelling prompt is ~600 tokens, so a
        2048-token context is enough and keeps the model's memory down (16 GB laptop).
        A server failure (not a bad answer, not a timeout) is retried once (D-028)."""
        payload = {
            "model": model, "stream": False, "think": False, "format": schema,
            "options": {"temperature": 0, "seed": seed, "num_ctx": num_ctx, "num_predict": max_tokens},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        }
        retries = 0
        while True:
            try:
                body = self._call("/api/chat", payload)
                break
            except ServerFailure:
                if retries >= 1:
                    raise
                retries += 1
                time.sleep(self.retry_pause)
        text = (body.get("message") or {}).get("content", "")
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            raise LLMError("model answer is not valid JSON") from None
        if not isinstance(data, dict):
            raise LLMError("model answer is not a JSON object")
        return LLMReply(data, int(body.get("prompt_eval_count") or 0), int(body.get("eval_count") or 0),
                        round((body.get("total_duration") or 0) / 1e9, 2), retries)
