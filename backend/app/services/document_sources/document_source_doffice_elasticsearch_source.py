from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Literal

import httpx

from app.core.config import settings
from app.services.ingestion.ingestion_doffice_content_normalizer import normalize_doffice_source

logger = logging.getLogger(__name__)

DOFFICE_SOURCE_TYPE: Literal["doffice_elasticsearch"] = "doffice_elasticsearch"


class DofficeSourceError(RuntimeError):
    pass


class DofficeDocumentNotFoundError(LookupError):
    pass


@dataclass(frozen=True)
class DofficeDocument:
    id_vb: str
    ky_hieu: str | None
    trich_yeu: str | None
    id_dv_ban_hanh: int | str | None
    noi_ban_hanh: str | None
    nguoi_ky: str | None
    ten_file: str | None
    duong_dan: str | None
    raw_noi_dung: str
    ngay_vb: str | None = None
    ngay_tao: str | None = None
    ngay_capnhat: str | None = None
    nam: int | None = None
    thang: int | None = None
    tom_tat: str | None = None
    clean_text: str = ""
    raw_source: dict[str, Any] | None = None
    source_type: Literal["doffice_elasticsearch"] = DOFFICE_SOURCE_TYPE


class DofficeElasticsearchSource:
    def __init__(
        self,
        *,
        url: str | None = None,
        timeout_seconds: int | float | None = None,
    ) -> None:
        self._url = (url if url is not None else settings.doffice_es_url).strip()
        self._timeout_seconds = float(
            timeout_seconds
            if timeout_seconds is not None
            else settings.doffice_es_timeout_seconds
        )
        self._auth = (
            httpx.BasicAuth(settings.doffice_es_username, settings.doffice_es_password)
            if settings.doffice_es_username and settings.doffice_es_password
            else None
        )
        self._verify_ssl = settings.doffice_es_verify_ssl

    async def fetch_document_by_id_vb(self, id_vb: str) -> DofficeDocument:
        clean_id = " ".join(str(id_vb or "").split()).strip()
        if not clean_id:
            raise ValueError("id_vb is required.")
        if not self._url:
            raise DofficeSourceError("DOFFICE_ES_URL is not configured.")

        payload = {
            "query": {
                "bool": {
                    "must": [{"term": {"id_vb": clean_id}}],
                    "must_not": [],
                    "should": [],
                }
            },
            "from": 0,
            "size": 10,
            "sort": [],
            "aggs": {},
        }

        try:
            async with httpx.AsyncClient(
                timeout=self._timeout_seconds,
                auth=self._auth,
                verify=self._verify_ssl,
            ) as client:
                response = await client.request(
                    "GET",
                    self._url,
                    json=payload,
                    headers={"Content-Type": "application/json"},
                )
                response.raise_for_status()
                data = response.json()
        except httpx.TimeoutException as exc:
            raise DofficeSourceError(f"DOffice Elasticsearch timed out for id_vb={clean_id}.") from exc
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code if exc.response is not None else "unknown"
            raise DofficeSourceError(
                f"DOffice Elasticsearch returned HTTP {status_code} for id_vb={clean_id}."
            ) from exc
        except httpx.HTTPError as exc:
            raise DofficeSourceError(
                f"DOffice Elasticsearch request failed for id_vb={clean_id}: {exc}"
            ) from exc
        except ValueError as exc:
            raise DofficeSourceError(
                f"DOffice Elasticsearch returned invalid JSON for id_vb={clean_id}."
            ) from exc

        source = self._select_source(data, clean_id)
        raw_noi_dung = str(source.get("noi_dung") or "")
        normalized = normalize_doffice_source(source)
        logger.info(
            "Fetched DOffice document id_vb=%s ky_hieu=%s raw_chars=%s",
            clean_id,
            source.get("ky_hieu"),
            len(raw_noi_dung),
        )
        return DofficeDocument(
            id_vb=str(source.get("id_vb") or clean_id),
            ky_hieu=_optional_string(source.get("ky_hieu")),
            trich_yeu=_optional_string(source.get("trich_yeu")),
            id_dv_ban_hanh=source.get("id_dv_ban_hanh"),
            noi_ban_hanh=_optional_string(source.get("noi_ban_hanh")),
            nguoi_ky=_optional_string(source.get("nguoi_ky")),
            ten_file=_optional_string(source.get("ten_file")),
            duong_dan=_optional_string(source.get("duong_dan")),
            ngay_vb=_optional_string(source.get("ngay_vb")),
            ngay_tao=_optional_string(source.get("ngay_tao")),
            ngay_capnhat=_optional_string(source.get("ngay_capnhat")),
            nam=_optional_int(source.get("nam")),
            thang=_optional_int(source.get("thang")),
            tom_tat=_optional_string(source.get("tom_tat")),
            raw_noi_dung=raw_noi_dung,
            clean_text=normalized.clean_text,
            raw_source=source,
        )

    @staticmethod
    def _select_source(data: dict[str, Any], id_vb: str) -> dict[str, Any]:
        raw_hits = data.get("hits", {}).get("hits", []) if isinstance(data, dict) else []
        hits = [hit for hit in raw_hits if isinstance(hit, dict)]
        if not hits:
            raise DofficeDocumentNotFoundError(f"DOffice document id_vb={id_vb} was not found.")

        sources = [dict(hit.get("_source") or {}) for hit in hits]
        exact = next(
            (source for source in sources if str(source.get("id_vb") or "") == id_vb),
            None,
        )
        source = exact or sources[0]
        if not source:
            raise DofficeDocumentNotFoundError(f"DOffice document id_vb={id_vb} was not found.")
        return source


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    clean = " ".join(str(value).split()).strip()
    return clean or None


def _optional_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
