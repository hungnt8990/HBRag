"""Tải file CHÍNH của các văn bản (theo danh sách id_vb) từ gateway DOffice.

Luồng: id_vb -> FILE_CongViec (liệt kê file) -> chọn 1 file FILE_CHINH (ưu tiên PDF)
-> DownloadFileVBByDuongDan (tải bytes) -> lưu ``file/{id_vb}.{đuôi}``.

Token (JWT) truyền qua env ``DOFFICE_TOKEN`` (có/không tiền tố "Bearer "). ID_NV/ID_DV
giải mã trực tiếp từ payload token nên không cần cấu hình thêm. Token hết hạn ~1h.

Chạy:  set DOFFICE_TOKEN=Bearer eyJ...   &&  python -m jobs.doffice_sync.download_files_by_nhanvien
Tùy chọn: --list <tsv> (mặc định file/danh_sach_id_vb_90288.tsv), --out <dir>, --limit N.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
from pathlib import Path
from urllib.parse import quote

import httpx

BASE = "https://gwdoffice.cpc.vn"
LIST_URL = BASE + "/v1/congviec/CongViec/FILE_CongViec"
DOWNLOAD_URL = BASE + "/v1/files/FileVb/DownloadFileVBByDuongDan"

HERE = Path(__file__).resolve().parent
DEFAULT_LIST = HERE / "file" / "danh_sach_id_vb_90288.tsv"
DEFAULT_OUT = HERE / "file"


def _decode_token() -> tuple[str, str, str]:
    """Trả (authorization_header, id_nv, id_dv) từ env DOFFICE_TOKEN."""
    raw = os.environ.get("DOFFICE_TOKEN", "").strip()
    if not raw:
        raise SystemExit("Thiếu env DOFFICE_TOKEN (JWT). Ví dụ: set DOFFICE_TOKEN=Bearer eyJ...")
    token = raw[7:].strip() if raw.lower().startswith("bearer ") else raw
    try:
        payload_b64 = token.split(".")[1]
        payload_b64 += "=" * (-len(payload_b64) % 4)  # pad base64url
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(f"Token không giải mã được: {exc}")
    id_nv = str(payload.get("ID_NV") or "")
    id_dv = str(payload.get("IDDONVI") or "")
    if not id_nv or not id_dv:
        raise SystemExit(f"Token thiếu ID_NV/IDDONVI (payload={payload}).")
    return f"Bearer {token}", id_nv, id_dv


def _read_id_vb_list(path: Path, limit: int | None) -> list[str]:
    ids: list[str] = []
    with path.open(encoding="utf-8") as f:
        for i, line in enumerate(f):
            if i == 0 and line.lower().startswith("id_vb"):
                continue  # header
            first = line.strip().split("\t")[0]
            if first.isdigit():
                ids.append(first)
    return ids[:limit] if limit else ids


def _pick_main_file(data: list[dict]) -> dict | None:
    """Chọn 1 file chính: ưu tiên FILE_CHINH=true + LOAI_FILE=pdf, rồi FILE_CHINH bất kỳ,
    rồi pdf bất kỳ, cuối cùng file đầu tiên."""
    if not data:
        return None
    def is_pdf(f: dict) -> bool:
        return str(f.get("LOAI_FILE") or "").lower() == "pdf"
    chinh = [f for f in data if f.get("FILE_CHINH")]
    for cand in (
        [f for f in chinh if is_pdf(f)],
        chinh,
        [f for f in data if is_pdf(f)],
        data,
    ):
        if cand:
            return cand[0]
    return None


async def _list_files(client: httpx.AsyncClient, id_vb: str) -> list[dict]:
    resp = await client.get(LIST_URL, params={"id_vanban": id_vb, "hstl": "false"})
    resp.raise_for_status()
    body = resp.json()
    data = body.get("Data")
    return data if isinstance(data, list) else []


async def _download(client: httpx.AsyncClient, duong_dan: str, id_nv: str, id_dv: str) -> bytes:
    # DUONG_DAN chứa "\" -> encode %5C (safe='' để mã hoá cả dấu gạch chéo ngược).
    url = (
        f"{DOWNLOAD_URL}?DUONG_DAN={quote(duong_dan, safe='')}"
        f"&ID_NV={id_nv}&ID_DV={id_dv}"
    )
    resp = await client.get(url)
    resp.raise_for_status()
    ctype = resp.headers.get("content-type", "")
    if "application/json" in ctype or "problem+json" in ctype:
        raise RuntimeError(f"trả JSON (không phải file): {resp.text[:200]}")
    return resp.content


async def main() -> None:
    parser = argparse.ArgumentParser(description="Tải file chính văn bản từ DOffice theo id_vb.")
    parser.add_argument("--list", type=Path, default=DEFAULT_LIST)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    auth, id_nv, id_dv = _decode_token()
    ids = _read_id_vb_list(args.list, args.limit)
    args.out.mkdir(parents=True, exist_ok=True)
    print(f"ID_NV={id_nv} ID_DV={id_dv} | {len(ids)} văn bản | out={args.out}")

    ok = skipped = failed = 0
    async with httpx.AsyncClient(
        verify=False, timeout=90.0, headers={"Authorization": auth}
    ) as client:
        for i, id_vb in enumerate(ids, 1):
            try:
                files = await _list_files(client, id_vb)
                chosen = _pick_main_file(files)
                if chosen is None or not chosen.get("DUONG_DAN"):
                    skipped += 1
                    print(f"[{i}/{len(ids)}] id_vb={id_vb}: KHÔNG có file chính -> bỏ qua")
                    continue
                ext = str(chosen.get("LOAI_FILE") or "").lstrip(".") or "bin"
                content = await _download(client, chosen["DUONG_DAN"], id_nv, id_dv)
                if not content:
                    skipped += 1
                    print(f"[{i}/{len(ids)}] id_vb={id_vb}: file rỗng -> bỏ qua")
                    continue
                dest = args.out / f"{id_vb}.{ext}"
                dest.write_bytes(content)
                ok += 1
                print(f"[{i}/{len(ids)}] id_vb={id_vb}: ✓ {dest.name} ({len(content):,} bytes)")
            except Exception as exc:  # noqa: BLE001
                failed += 1
                print(f"[{i}/{len(ids)}] id_vb={id_vb}: LỖI {type(exc).__name__}: {str(exc)[:160]}")
    print(f"\nXONG: tải {ok} | bỏ qua {skipped} | lỗi {failed} | tổng {len(ids)}")


if __name__ == "__main__":
    asyncio.run(main())
