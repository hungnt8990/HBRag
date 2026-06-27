"""Kiểm chứng ĐỘC LẬP file acl_preview_<id>.json: lọc đúng chưa?

Đối chiếu tập người giải nén từ payload với input D-Office, và kiểm tra trực tiếp
từng người bằng subject_can_access (đúng luật filter Qdrant/ES).
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from app.db.session import AsyncSessionLocal, engine
from app.services.security.security_acl_compressor import OrgCatalog
from app.services.security.security_acl_payload import (
    AclSubject,
    from_chunk_payload,
    subject_can_access,
)


async def main(path: str) -> None:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    pq = data["phan_quyen"]
    io = pq["input_doffice"]
    payload = pq["chunk_payload"]

    don_vi = set(io["don_vi_list"])
    phong_ban = set(io["phong_ban_list"])
    ca_nhan = set(io["ca_nhan_list"])

    async with AsyncSessionLocal() as session:
        catalog = await OrgCatalog.from_session(session)

    # Phân loại cá nhân nguồn theo danh mục.
    known = {u for u in ca_nhan if u in catalog.user_location}
    unknown = ca_nhan - known

    # 1) Giải nén payload -> tập người thực tế được xem.
    acl = from_chunk_payload(payload)
    audience = acl.decompress(catalog)

    # 2) Đối chiếu: audience có ĐÚNG bằng tập cá nhân hợp lệ không?
    missing = known - audience       # người trong ca_nhan_list mà KHÔNG xem được
    extra = audience - known         # người KHÔNG ở ca_nhan_list mà LẠI xem được

    # 3) Kiểm tra trực tiếp từng người bằng subject_can_access (luật filter thật).
    def check(uid: int) -> bool:
        dv, pb = catalog.user_location.get(uid, (None, None))
        return subject_can_access(payload, AclSubject(id_nv=uid, id_dv=dv, id_pb=pb))

    sample_known = sorted(known)[:200]
    can_see = sum(1 for u in sample_known if check(u))
    deny_nv = set(payload.get("acl_deny_nv") or [])
    denied_but_visible = [u for u in sorted(deny_nv)[:200] if check(u)]

    print("== INPUT D-OFFICE ==")
    print(f"  don_vi_list   : {len(don_vi)}")
    print(f"  phong_ban_list: {len(phong_ban)}")
    print(f"  ca_nhan_list  : {len(ca_nhan)}  (known={len(known)}, unknown/bo={len(unknown)})")
    print()
    print("== GIAI NEN PAYLOAD ==")
    print(f"  audience_size : {len(audience)}")
    print(f"  thieu (known nhung KHONG xem duoc) : {len(missing)} -> {sorted(missing)[:10]}")
    print(f"  thua  (KHONG o ca_nhan_list ma xem duoc): {len(extra)} -> {sorted(extra)[:10]}")
    print()
    print("== KIEM TRA subject_can_access (luat filter that) ==")
    print(f"  mau {len(sample_known)} nguoi known -> xem duoc: {can_see}/{len(sample_known)}")
    print(f"  nguoi trong deny_nv ma VAN xem duoc: {len(denied_but_visible)} -> {denied_but_visible[:10]}")
    print()
    verdict = (not missing) and (not extra) and (can_see == len(sample_known)) and (not denied_but_visible)
    print("KET LUAN:", "LOC DUNG (snapshot khop tuyet doi)" if verdict else "CO SAI LECH - xem tren")

    await engine.dispose()


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "data/vb/acl_preview_1068586.json"
    asyncio.run(main(path))
