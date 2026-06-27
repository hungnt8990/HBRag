"""Client batch query index ``doffice_vanban_quyen`` lấy ACL theo id_vb."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx

_SOURCE = [
    "id_vb", "don_vi_list", "phong_ban_list", "ca_nhan_list",
    "quyen_checksum", "quyen_ngay_capnhat",
]


@dataclass
class QuyenRecord:
    id_vb: str
    don_vi_list: list[int] = field(default_factory=list)
    phong_ban_list: list[int] = field(default_factory=list)
    ca_nhan_list: list[int] = field(default_factory=list)
    quyen_checksum: str | None = None
    quyen_ngay_capnhat: str | None = None

    @property
    def has_acl(self) -> bool:
        return bool(self.don_vi_list or self.phong_ban_list or self.ca_nhan_list)

    @classmethod
    def from_source(cls, src: dict[str, Any]) -> "QuyenRecord":
        def _ints(value: Any) -> list[int]:
            return [int(x) for x in (value or []) if str(x).lstrip("-").isdigit()]

        return cls(
            id_vb=str(src.get("id_vb")),
            don_vi_list=_ints(src.get("don_vi_list")),
            phong_ban_list=_ints(src.get("phong_ban_list")),
            ca_nhan_list=_ints(src.get("ca_nhan_list")),
            quyen_checksum=src.get("quyen_checksum"),
            quyen_ngay_capnhat=src.get("quyen_ngay_capnhat"),
        )


class QuyenEsClient:
    def __init__(
        self,
        *,
        url: str,
        user: str | None = None,
        password: str | None = None,
        verify_ssl: bool = False,
        index: str = "doffice_vanban_quyen",
        timeout_seconds: float = 60.0,
    ) -> None:
        self._url = url.rstrip("/")
        self._index = index
        self._auth = (user, password) if user else None
        self._verify = verify_ssl
        self._timeout = timeout_seconds

    async def get_batch(self, id_vb_list: list[str]) -> dict[str, QuyenRecord]:
        """Trả dict key=id_vb (str). VB không có record quyền -> không có trong dict."""
        if not id_vb_list:
            return {}
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
                    f"Query doffice_vanban_quyen lỗi: HTTP {resp.status_code} {resp.text[:300]}"
                )
            hits = resp.json().get("hits", {}).get("hits", [])
        result: dict[str, QuyenRecord] = {}
        for hit in hits:
            record = QuyenRecord.from_source(hit.get("_source") or {})
            result[record.id_vb] = record
        return result
