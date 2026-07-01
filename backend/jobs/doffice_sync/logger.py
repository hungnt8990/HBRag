"""Setup file log mỗi lần chạy job.

TẤT CẢ log của mọi job DOffice (unified/pg_es/qdrant/delete...) đều ghi vào
``jobs/doffice_sync/log/`` — neo theo VỊ TRÍ FILE NÀY, KHÔNG phụ thuộc cwd. Mỗi job
có 1 thư mục con theo tên (vd ``doffice_unified``) và mỗi lần chạy 1 thư mục con
``run_stamp``. Riêng văn bản BỎ QUA vì quá nhiều chunk (> max_chunks) được liệt kê
ở file ``vanban_bo_qua_qua_chunk.log`` (logger con ``doffice_sync.oversize``).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

LOGGER_ROOT = "doffice_sync"
# Logger con cho văn bản BỎ QUA vì > max_chunks -> file riêng (liệt kê để theo dõi).
OVERSIZE_LOGGER = f"{LOGGER_ROOT}.oversize"
OVERSIZE_LOG_NAME = "vanban_bo_qua_qua_chunk.log"
# Thư mục log GỐC: jobs/doffice_sync/log (neo theo file này, độc lập cwd).
LOG_ROOT = Path(__file__).resolve().parent / "log"
_FORMAT = "%(asctime)s [%(levelname)-5s] [%(name)s] %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"


@dataclass
class JobLoggers:
    logger: logging.Logger
    log_dir: Path

    def get(self, child: str) -> logging.Logger:
        return logging.getLogger(f"{LOGGER_ROOT}.{child}")


def setup_job_logging(base_dir: str, run_stamp: str) -> JobLoggers:
    # base_dir có thể là path cũ ("logs/jobs/doffice_unified") -> chỉ lấy TÊN job cuối,
    # đặt tất cả dưới LOG_ROOT chung. Giữ tham số cũ để không phải sửa mọi nơi gọi.
    job_name = Path(base_dir).name or "doffice"
    log_dir = LOG_ROOT / job_name / run_stamp
    log_dir.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger(LOGGER_ROOT)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()

    formatter = logging.Formatter(_FORMAT, datefmt=_DATEFMT)

    def _file(name: str, level: int) -> logging.Handler:
        handler = logging.FileHandler(log_dir / name, encoding="utf-8")
        handler.setLevel(level)
        handler.setFormatter(formatter)
        return handler

    logger.addHandler(_file("full.log", logging.DEBUG))
    logger.addHandler(_file("info.log", logging.INFO))
    logger.addHandler(_file("warning.log", logging.WARNING))
    logger.addHandler(_file("error.log", logging.ERROR))

    # Log RIÊNG cho văn bản nhiều chunk: gắn handler vào logger con ``doffice_sync.chunks``.
    # File này CHỈ nhận record phát qua logger đó (code phát hiện chunk lớn) -> không lẫn
    # log thường. propagate vẫn True -> các dòng này cũng vào full.log/info.log.
    chunks_logger = logging.getLogger(f"{LOGGER_ROOT}.chunks")
    chunks_logger.setLevel(logging.INFO)
    for handler in list(chunks_logger.handlers):
        chunks_logger.removeHandler(handler)
        handler.close()
    chunks_logger.addHandler(_file("chunks_big.log", logging.INFO))

    # Log RIÊNG liệt kê văn bản KHÔNG THỂ CHUNK vì vượt ngưỡng (> max_chunks, mặc định 500):
    # logger con ``doffice_sync.oversize`` -> file ``vanban_bo_qua_qua_chunk.log``. Mọi nhánh
    # pipeline (unified/qdrant) phát skip qua logger này để gom về 1 chỗ.
    oversize_logger = logging.getLogger(OVERSIZE_LOGGER)
    oversize_logger.setLevel(logging.INFO)
    for handler in list(oversize_logger.handlers):
        oversize_logger.removeHandler(handler)
        handler.close()
    oversize_logger.addHandler(_file(OVERSIZE_LOG_NAME, logging.INFO))

    # KHÔNG gắn StreamHandler: log chi tiết chỉ vào file; console do spinner phụ trách
    # (chỉ hiện tiến độ + summary), tránh log chi tiết làm rối màn hình.
    return JobLoggers(logger=logger, log_dir=log_dir)
