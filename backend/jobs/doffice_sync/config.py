"""Config của job DOffice sync — đọc ES nguồn từ ``settings`` đã có."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class JobConfig:
    # ES DOffice nguồn (mặc định lấy từ app settings.doffice_es_*).
    vanban_es_url: str = ""
    vanban_es_user: str | None = None
    vanban_es_password: str | None = None
    vanban_es_verify_ssl: bool = False
    quyen_es_url: str = ""  # rỗng -> = vanban host

    # Filter
    don_vi_filter: list[int] | None = None
    id_vb_filter: list[str] | None = None

    # Hiệu năng
    batch_size: int = 500
    max_workers: int = 20

    # Chế độ
    full_scan: bool = False
    dry_run: bool = False
    retry_only: bool = False
    scan_limit: int | None = None

    # Retry
    retry_delay_minutes: int = 60
    max_retry_count: int = 5

    # Log
    log_dir: str = "logs/doffice_sync"

    def snapshot(self) -> dict:
        data = asdict(self)
        data.pop("vanban_es_password", None)  # không log password
        return data

    @classmethod
    def from_settings(cls, **overrides) -> "JobConfig":
        """Khởi tạo từ app settings (doffice_es_url/username/password/verify_ssl)."""
        from app.core.config import settings

        host = _host_only(settings.doffice_es_url)
        cfg = cls(
            vanban_es_url=host,
            vanban_es_user=settings.doffice_es_username,
            vanban_es_password=settings.doffice_es_password,
            vanban_es_verify_ssl=settings.doffice_es_verify_ssl,
            quyen_es_url=host,
        )
        for key, value in overrides.items():
            if value is not None and hasattr(cfg, key):
                setattr(cfg, key, value)
        return cfg


def _host_only(url: str) -> str:
    """Bỏ ``/doffice_vanban/_search`` -> chỉ còn ``https://host:9200``."""
    from urllib.parse import urlsplit

    parts = urlsplit(url)
    if parts.scheme and parts.netloc:
        return f"{parts.scheme}://{parts.netloc}"
    return url.rstrip("/")
