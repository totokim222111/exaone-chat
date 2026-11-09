import os
from urllib.parse import urlsplit

FRIENDLI = "https://api.friendli.ai/serverless/v1"
EXAONE = "https://api.exaone.ai/v1"
LEGACY_MODEL = "LGAI-EXAONE/K-EXAONE-2.0-750B-A37B"
DEFAULT_BASE_URL = os.getenv("DEFAULT_BASE_URL", EXAONE).strip().rstrip("/")
DEFAULT_MODEL = os.getenv("DEFAULT_MODEL", "k-exaone_v2.5").strip()
MODELS = [m.strip() for m in os.getenv("MODELS", f"k-exaone_v2.5,{LEGACY_MODEL},LGAI-EXAONE/K-EXAONE-236B-A23B").split(",") if m.strip()]
ALLOWED = [u.strip().rstrip("/") for u in os.getenv("ALLOWED_BASE_URLS", f"{EXAONE},{FRIENDLI}").split(",") if u.strip()]
EXAONE_STYLE_HOSTS = [h.strip().lower() for h in os.getenv("EXAONE_STYLE_HOSTS", "api.exaone.ai").split(",") if h.strip()]
REASONING_EFFORTS = ["low", "high", "max"]
CUSTOM = os.getenv("ALLOW_CUSTOM_BASE_URL", "false").lower() == "true"
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "false").lower() == "true"
SEARXNG_URL = os.getenv("SEARXNG_URL", "http://searxng:8080").strip().rstrip("/")
DEFAULTS = dict(baseUrl=DEFAULT_BASE_URL, model=DEFAULT_MODEL, systemPrompt="", temperature=1.0,
                topP=0.95, maxTokens=8192, thinking=True, reasoningEffort="max", searchProvider="auto", webSearch=False)


def is_exaone_style(base_url: str) -> bool:
    return urlsplit(base_url).hostname in EXAONE_STYLE_HOSTS


def migrate_legacy_settings(saved: dict) -> dict:
    """Used only by migration 002; later explicit Friendli selections are preserved."""
    updated = saved.copy()
    if (saved.get("baseUrl", FRIENDLI).rstrip("/") == FRIENDLI
            and saved.get("model", LEGACY_MODEL) == LEGACY_MODEL):
        updated.update(baseUrl=DEFAULT_BASE_URL, model=DEFAULT_MODEL)
    return updated


def validate_base_url(value: str) -> str:
    value = value.rstrip("/")
    try:
        url = urlsplit(value)
        _ = url.port
    except ValueError:
        raise ValueError("올바른 Base URL을 입력해 주세요.") from None
    if (url.scheme not in ("http", "https") or not url.hostname or url.username is not None
            or url.password is not None or url.query or url.fragment or any(c.isspace() for c in value)):
        raise ValueError("인증정보·쿼리·프래그먼트 없는 http(s) URL을 입력해 주세요.")
    if not CUSTOM and value not in ALLOWED:
        raise ValueError("허용된 Base URL을 선택해 주세요.")
    return value
