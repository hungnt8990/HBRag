"""Setup 4 file log mỗi lần chạy job (full/info/warning/error)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

LOGGER_ROOT = "doffice_sync"
_FORMAT = "%(asctime)s [%(levelname)-5s] [%(name)s] %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"


@dataclass
class JobLoggers:
    logger: logging.Logger
    log_dir: Path

    def get(self, child: str) -> logging.Logger:
        return logging.getLogger(f"{LOGGER_ROOT}.{child}")


def setup_job_logging(base_dir: str, run_stamp: str) -> JobLoggers:
    log_dir = Path(base_dir) / run_stamp
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

    # KHÔNG gắn StreamHandler: log chi tiết chỉ vào file; console do spinner phụ trách
    # (chỉ hiện tiến độ + summary), tránh log chi tiết làm rối màn hình.
    return JobLoggers(logger=logger, log_dir=log_dir)
