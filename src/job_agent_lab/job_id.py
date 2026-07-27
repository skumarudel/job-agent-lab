import hashlib
from urllib.parse import urlsplit, urlunsplit


def job_id_from_link(url: str) -> str:
    normalized = _normalize_job_link(url)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _normalize_job_link(url: str) -> str:
    url = url.strip()
    parts = urlsplit(url)
    host = parts.hostname.lower() if parts.hostname else ""
    path = parts.path.rstrip("/") or ""
    # rebuild without trailing slash on path; keep query/fragment as-is for now
    netloc = host
    if parts.port:
        netloc = f"{host}:{parts.port}"
    return urlunsplit((parts.scheme.lower(), netloc, path, parts.query, parts.fragment))
