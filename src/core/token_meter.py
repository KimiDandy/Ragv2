import tiktoken
from threading import Lock

try:
    from .config import CHAT_MODEL
except Exception:
    CHAT_MODEL = "gpt-4.1"

_enc_lock = Lock()
_enc = None

def _get_encoder(model: str | None = None):
    global _enc
    with _enc_lock:
        if _enc is not None:
            return _enc
        try:
            _enc = tiktoken.encoding_for_model(model or CHAT_MODEL)
        except Exception:
            _enc = tiktoken.get_encoding("cl100k_base")
        return _enc


def estimate_tokens(text: str, model: str | None = None) -> int:
    enc = _get_encoder(model)
    return len(enc.encode(text or ""))


class TokenBudget:
    def __init__(self, total: int, model: str | None = None):
        self.total = max(int(total or 0), 0)
        self.used = 0
        self.model = model or CHAT_MODEL

    def remaining(self) -> int:
        return max(self.total - self.used, 0)

    def can_afford(self, prompt: str, max_out: int) -> bool:
        est = estimate_tokens(prompt or "", self.model) + int(max(max_out, 0))
        return (self.used + est) <= int(self.total * 0.90)

    def charge(self, prompt: str, max_out: int):
        self.used += estimate_tokens(prompt or "", self.model) + int(max(max_out, 0))
