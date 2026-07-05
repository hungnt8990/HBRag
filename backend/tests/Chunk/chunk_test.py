"""Test chunk văn bản DOffice y hệt mô hình chunking của job ``run_pg_es.bat``.

Luồng (giống hệt bước "Chunking" trong ``ingestion_doffice_unified.persist_chunks`` /
``persist_to_postgres``):

    ES ``doffice_vanban`` (lấy _source theo id_vb)
      -> normalize_doffice_source(source)                # dựng NormalizedDofficeDocument
      -> build_doffice_chunks(normalized, body_max_chars, body_overlap, table_max_chars)

Tham số cắt chunk lấy từ profile ``doffice_admin`` (mặc định 3200 / 300 / 3500) — CÙNG
nguồn với job thật (``get_profile_config("doffice_admin")``). KHÔNG ghi PG/ES/Qdrant:
chỉ chunk trong bộ nhớ rồi xuất ra file text để soi kết quả.

Đầu ra: mỗi văn bản 1 file ``<id_vb>-<ky_hieu>.txt`` trong ``tests/Chunk/output/``,
các chunk sắp theo đúng thứ tự ``chunk_index`` (thứ tự đọc) mà chunker sinh ra.

Cách dùng:
    python tests/Chunk/chunk_test.py 1382311
    python tests/Chunk/chunk_test.py 1382311 1068586 1068587

Credential ES nguồn lấy TỪ settings (.env: DOFFICE_ES_URL/USERNAME/PASSWORD/VERIFY_SSL) —
cùng nguồn với job thật, không hardcode. Có thể ghi đè nhanh qua env:
    DOFFICE_ES_URL / DOFFICE_ES_INDEX / DOFFICE_ES_USERNAME / DOFFICE_ES_PASSWORD
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

# Cho phép import package ``app`` khi chạy trực tiếp file này (backend root = 2 cấp trên).
BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

# Console tiếng Việt: ép stdout/stderr về UTF-8 (Windows mặc định cp1252 gây lỗi encode).
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass

import httpx  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.services.chunkers.chunker_doffice_chunking import build_doffice_chunks  # noqa: E402
from app.services.ingestion.ingestion_doffice_content_normalizer import (  # noqa: E402
    normalize_doffice_source,
)
from app.services.ingestion.ingestion_profiles import get_profile_config  # noqa: E402


def _search_url() -> str:
    """URL endpoint _search của index doffice_vanban (đọc từ settings như job).

    ``settings.doffice_es_url`` trong .env đã là URL đầy đủ tới ``/_search`` — dùng thẳng.
    Cho phép ghi đè nhanh qua env ``DOFFICE_ES_URL``.
    """
    url = (os.getenv("DOFFICE_ES_URL") or settings.doffice_es_url or "").strip().rstrip("/")
    if url.endswith("_search"):
        return url
    return f"{url}/{ES_INDEX}/_search"


ES_INDEX = os.getenv("DOFFICE_ES_INDEX", "doffice_vanban")
# Credential ES nguồn: LẤY TỪ settings (đồng bộ với job run_pg_es) — không hardcode mật khẩu.
ES_USER = os.getenv("DOFFICE_ES_USERNAME") or settings.doffice_es_username
ES_PASSWORD = os.getenv("DOFFICE_ES_PASSWORD") or settings.doffice_es_password
ES_VERIFY_SSL = settings.doffice_es_verify_ssl

OUTPUT_DIR = Path(__file__).resolve().parent / "output"
# Cache raw _source theo id_vb: lấy từ ES 1 lần rồi dùng lại (soi dữ liệu raw + chạy
# offline khi hiệu chỉnh chunker). Xóa file trong raw/ nếu muốn ép tải lại.
RAW_DIR = Path(__file__).resolve().parent / "raw"

# Ký tự không hợp lệ cho tên file Windows -> thay bằng "_".
_UNSAFE_FILENAME = re.compile(r'[\\/:*?"<>|\r\n\t]+')


def _safe_filename_part(value: str) -> str:
    cleaned = _UNSAFE_FILENAME.sub("_", str(value or "").strip())
    cleaned = re.sub(r"\s+", "_", cleaned).strip("._")
    return cleaned or "khong-ky-hieu"


def _raw_cache_path(id_vb: str) -> Path:
    return RAW_DIR / f"{_safe_filename_part(id_vb)}.json"


def load_cached_source(id_vb: str) -> dict | None:
    """Đọc _source đã cache trong raw/<id_vb>.json (nếu có)."""
    path = _raw_cache_path(id_vb)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — cache hỏng thì coi như chưa có, tải lại từ ES.
        return None
    return data if isinstance(data, dict) else None


def save_cached_source(id_vb: str, source: dict) -> None:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    _raw_cache_path(id_vb).write_text(
        json.dumps(source, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def fetch_source(client: httpx.Client, id_vb: str) -> dict | None:
    """Lấy _source của 1 văn bản từ ES ``doffice_vanban`` theo id_vb.

    Query ``term id_vb`` giống curl mẫu; BasicAuth + verify lấy từ settings (như job).
    """
    body = {
        "query": {
            "bool": {
                "must": [{"term": {"id_vb": str(id_vb)}}],
                "must_not": [],
                "should": [],
            }
        },
        "from": 0,
        "size": 10,
        "sort": [],
        "aggs": {},
    }
    resp = client.post(
        _search_url(),
        json=body,
        headers={"Content-Type": "application/json"},
    )
    resp.raise_for_status()
    hits = (resp.json().get("hits") or {}).get("hits") or []
    if not hits:
        return None
    return hits[0].get("_source") or None


def _meta_line(chunk) -> str:
    """Dòng metadata gọn cho mỗi chunk (loại/mục/bảng/độ dài)."""
    meta = chunk.metadata or {}
    section = meta.get("section_path") or meta.get("section_title")
    if isinstance(section, (list, tuple)):
        section = " > ".join(str(p) for p in section)
    parts = [f"chunk_type={meta.get('chunk_type')}"]
    if section:
        parts.append(f"section={section}")
    if meta.get("table_name"):
        parts.append(f"table={meta.get('table_name')}")
    if meta.get("artifact_type"):
        parts.append(f"artifact={meta.get('artifact_type')}")
    if meta.get("quality_status"):
        parts.append(f"quality={meta.get('quality_status')}")
    parts.append(f"chars={len(chunk.content or '')}")
    return " | ".join(parts)


def chunk_one(source: dict, cfg: dict) -> list:
    """Chunk 1 văn bản đúng như job: normalize -> build_doffice_chunks (tham số profile)."""
    normalized = normalize_doffice_source(source)
    return build_doffice_chunks(
        normalized,
        body_max_chars=int(cfg.get("doffice_body_max_chars") or 2800),
        body_overlap=int(cfg.get("doffice_body_overlap") or 300),
        table_max_chars=int(cfg.get("doffice_table_max_chars") or 3500),
    )


def write_output(id_vb: str, source: dict, chunks: list) -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    ky_hieu = source.get("ky_hieu") or source.get("so_ky_hieu") or ""
    filename = f"{_safe_filename_part(id_vb)}-{_safe_filename_part(ky_hieu)}.txt"
    out_path = OUTPUT_DIR / filename

    lines: list[str] = []
    lines.append("=" * 80)
    lines.append(f"id_vb        : {id_vb}")
    lines.append(f"ky_hieu      : {ky_hieu}")
    lines.append(f"trich_yeu    : {source.get('trich_yeu') or ''}")
    lines.append(f"noi_ban_hanh : {source.get('noi_ban_hanh') or ''}")
    lines.append(f"ngay_vb      : {source.get('ngay_vb') or ''}")
    lines.append(f"ten_file     : {source.get('ten_file') or ''}")
    lines.append(f"TỔNG SỐ CHUNK: {len(chunks)}")
    lines.append("=" * 80)
    lines.append("")

    for chunk in chunks:
        lines.append("─" * 80)
        lines.append(f"### CHUNK #{chunk.chunk_index}")
        lines.append(_meta_line(chunk))
        lines.append("─" * 80)
        lines.append(chunk.content or "")
        lines.append("")

    out_path.write_text("\n".join(lines), encoding="utf-8")
    return out_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Test chunk văn bản DOffice (mô hình run_pg_es).")
    parser.add_argument("id_vb", nargs="+", help="Một hoặc nhiều id_vb cần chunk thử.")
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Bỏ qua cache raw/, ép tải lại _source từ ES.",
    )
    args = parser.parse_args()

    # Gom tham số: cho phép "1,2 3" -> [1,2,3].
    id_list: list[str] = []
    for raw in args.id_vb:
        id_list.extend(p.strip() for p in raw.replace(";", ",").replace(" ", ",").split(",") if p.strip())

    cfg = get_profile_config("doffice_admin")
    print(
        f"Cấu hình chunk (profile doffice_admin): "
        f"body_max={cfg.get('doffice_body_max_chars')} "
        f"overlap={cfg.get('doffice_body_overlap')} "
        f"table_max={cfg.get('doffice_table_max_chars')}"
    )
    print(f"Nguồn ES: {_search_url()} (user={ES_USER})\n")

    ok = 0
    fail = 0
    auth = httpx.BasicAuth(ES_USER, ES_PASSWORD or "") if ES_USER else None
    # verify từ settings (ES nguồn HTTPS self-signed nội bộ -> thường False).
    with httpx.Client(verify=ES_VERIFY_SSL, auth=auth, timeout=60.0) as client:
        for id_vb in id_list:
            source = None if args.refresh else load_cached_source(id_vb)
            from_cache = source is not None
            if source is None:
                try:
                    source = fetch_source(client, id_vb)
                except Exception as exc:  # noqa: BLE001
                    print(f"[LỖI] id_vb={id_vb}: gọi ES thất bại: {exc}")
                    fail += 1
                    continue
                if source is not None:
                    save_cached_source(id_vb, source)
            if source is None:
                print(f"[BỎ QUA] id_vb={id_vb}: không tìm thấy văn bản trên ES.")
                fail += 1
                continue
            try:
                chunks = chunk_one(source, cfg)
            except Exception as exc:  # noqa: BLE001
                print(f"[LỖI] id_vb={id_vb}: chunk thất bại: {exc}")
                fail += 1
                continue
            out_path = write_output(id_vb, source, chunks)
            src_tag = "cache" if from_cache else "ES"
            print(f"[OK]  id_vb={id_vb} ({src_tag}): {len(chunks):>4} chunk -> {out_path}")
            ok += 1

    print(f"\nHoàn tất: {ok} thành công, {fail} lỗi/bỏ qua. Thư mục kết quả: {OUTPUT_DIR}")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    # Tắt cảnh báo InsecureRequestWarning (ES self-signed) cho gọn console.
    try:
        import urllib3

        urllib3.disable_warnings()
    except Exception:  # noqa: BLE001
        pass
    raise SystemExit(main())
