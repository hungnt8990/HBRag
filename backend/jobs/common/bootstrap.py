"""Hàm dùng chung cho mọi job trong ``jobs/`` (bootstrap path, timestamp...).

Tách riêng để các job khác (vd job đẩy Qdrant sau này) tái sử dụng, không lặp code.
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path


def backend_root() -> Path:
    """Thư mục ``backend/`` (gốc chứa package ``app`` và ``jobs``)."""
    # jobs/common/bootstrap.py -> parents[2] = backend/
    return Path(__file__).resolve().parents[2]


def ensure_backend_on_path() -> Path:
    """Thêm ``backend/`` vào sys.path để chạy trực tiếp ``python jobs/.../run.py``."""
    root = backend_root()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    return root


def run_stamp() -> str:
    """Mốc thời gian cho tên thư mục log: ``YYYYMMDD_HHMMSS``."""
    return datetime.now().strftime("%Y%m%d_%H%M%S")
