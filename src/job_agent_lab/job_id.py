"""Stable job ids from normalized job URLs."""

import hashlib
from urllib.parse import urlsplit, urlunsplit


def job_id_from_link(url: str) -> str:
    """Return a SHA-256 hex job_id for a job posting URL.

    Normalization (via ``_normalize_job_link``) makes common URL variants
    produce the same id so duplicates can be detected across sheet syncs.

    Args:
        url: Raw job link from the sheet or elsewhere.

    Returns:
        64-character lowercase hex digest used as the SQLite primary key.
    """
    normalized = _normalize_job_link(url)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _normalize_job_link(url: str) -> str:
    """Normalize a URL for hashing: trim, lowercase scheme/host, strip trailing slash."""
    url = url.strip()
    parts = urlsplit(url)
    host = parts.hostname.lower() if parts.hostname else ""
    path = parts.path.rstrip("/") or ""
    # rebuild without trailing slash on path; keep query/fragment as-is for now
    netloc = host
    if parts.port:
        netloc = f"{host}:{parts.port}"
    return urlunsplit((parts.scheme.lower(), netloc, path, parts.query, parts.fragment))
