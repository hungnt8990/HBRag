"""Client scroll index ``doffice_vanban`` (search_after pagination, self-signed SSL)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import httpx

_SOURCE = [
    "id_vb", "ky_hieu", "trich_yeu", "noi_ban_hanh", "nguoi_ky",
    "ten_file", "tom_tat", "noi_dung", "ngay_vb", "ngay_capnhat",
    "nam", "thang", "id_dv_ban_hanh",
]


@dataclass
class VanbanRecord:
    id_vb: str
    ky_hieu: str | None = None
    trich_yeu: str | None = None
    noi_ban_hanh: str | None = None
    nguoi_ky: str | None = None
    ten_file: str | None = None
    tom_tat: str | None = None
    noi_dung: str | None = None
    ngay_vb: str | None = None
    ngay_capnhat: str | None = None
    nam: int | None = None

    @property
    def embed_text(self) -> str:
        """Text để embed BBQ: trich_yeu + tom_tat."""
        return " ".join(
            p for p in [self.trich_yeu or "", self.tom_tat or ""] if p.strip()
        ).strip()

    @property
    def noi_dung_truncated(self) -> str | None:
        """noi_dung giới hạn 50K ký tự cho ES BM25."""
        return (self.noi_dung or "")[:50_000] or None

    @classmethod
    def from_source(cls, src: dict[str, Any]) -> "VanbanRecord":
        return cls(
            id_vb=str(src.get("id_vb")),
            ky_hieu=src.get("ky_hieu"),
            trich_yeu=src.get("trich_yeu"),
            noi_ban_hanh=src.get("noi_ban_hanh"),
            nguoi_ky=src.get("nguoi_ky"),
            ten_file=src.get("ten_file"),
            tom_tat=src.get("tom_tat"),
            noi_dung=src.get("noi_dung"),
            ngay_vb=src.get("ngay_vb"),
            ngay_capnhat=src.get("ngay_capnhat"),
            nam=src.get("nam") if isinstance(src.get("nam"), int) else None,
        )


class VanbanEsClient:
    def __init__(
        self,
        *,
        url: str,
        user: str | None = None,
        password: str | None = None,
        verify_ssl: bool = False,
        index: str = "doffice_vanban",
        timeout_seconds: float = 60.0,
    ) -> None:
        self._url = url.rstrip("/")
        self._index = index
        self._auth = (user, password) if user else None
        self._verify = verify_ssl
        self._timeout = timeout_seconds

    def _build_body(
        self,
        *,
        batch_size: int,
        don_vi_filter: list[int] | None,
        updated_after: str | None,
        search_after: list | None,
    ) -> dict[str, Any]:
        must_not = [{"term": {"da_xoa": True}}]
        filters: list[dict] = []
        if updated_after:
            # ngay_capnhat là text -> dùng sub-field keyword (so sánh lexicographic,
            # đúng thứ tự thời gian vì format cố định "YYYY-MM-DD HH:MM:SS").
            filters.append({"range": {"ngay_capnhat.keyword": {"gte": updated_after}}})
        if don_vi_filter:
            # Lọc theo đơn vị quản lý văn bản (don_vi_list) — khớp luồng nghiệp vụ
            # DOffice: VB thuộc đơn vị nào thì đơn vị đó có record quyền tương ứng.
            filters.append({"terms": {"don_vi_list": list(don_vi_filter)}})
        body: dict[str, Any] = {
            "size": batch_size,
            "query": {"bool": {"must_not": must_not, "filter": filters}},
            "sort": [{"ngay_capnhat.keyword": "asc"}, {"id_vb": "asc"}],
            "_source": _SOURCE,
        }
        if search_after:
            body["search_after"] = search_after
        return body

    async def scroll_batches(
        self,
        *,
        batch_size: int = 500,
        don_vi_filter: list[int] | None = None,
        updated_after: str | None = None,
        search_after: list | None = None,
    ) -> AsyncIterator[tuple[list[VanbanRecord], list | None]]:
        """Yield (records, sort_values). ``sort_values=None`` ở batch cuối (hết docs)."""
        async with httpx.AsyncClient(
            verify=self._verify, auth=self._auth, timeout=self._timeout
        ) as client:
            while True:
                body = self._build_body(
                    batch_size=batch_size,
                    don_vi_filter=don_vi_filter,
                    updated_after=updated_after,
                    search_after=search_after,
                )
                resp = await client.post(f"{self._url}/{self._index}/_search", json=body)
                if resp.status_code >= 400:
                    raise RuntimeError(
                        f"Scroll doffice_vanban lỗi: HTTP {resp.status_code} {resp.text[:300]}"
                    )
                hits = resp.json().get("hits", {}).get("hits", [])
                if not hits:
                    return
                records = [VanbanRecord.from_source(h.get("_source") or {}) for h in hits]
                last_sort = hits[-1].get("sort")
                if len(hits) < batch_size:
                    yield records, None
                    return
                yield records, last_sort
                search_after = last_sort

    async def fetch_by_id_vb(self, id_vb_list: list[str]) -> list[VanbanRecord]:
        """Lấy trực tiếp các VB theo id_vb (dùng cho --id-vb / retry)."""
        if not id_vb_list:
            return []
        body = {
            "size": len(id_vb_list),
            "query": {"terms": {"id_vb": [int(v) for v in id_vb_list if str(v).isdigit()]}},
            "_source": _SOURCE,
        }
        async with httpx.AsyncClient(
            verify=self._verify, auth=self._auth, timeout=self._timeout
        ) as client:
            resp = await client.post(f"{self._url}/{self._index}/_search", json=body)
            if resp.status_code >= 400:
                raise RuntimeError(
                    f"Fetch doffice_vanban lỗi: HTTP {resp.status_code} {resp.text[:300]}"
                )
            hits = resp.json().get("hits", {}).get("hits", [])
        return [VanbanRecord.from_source(h.get("_source") or {}) for h in hits]
