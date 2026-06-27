"""Kiem chung viec GOP (rollup): nguoi -> phong -> don vi co dung khong.

Mong muon nghiep vu:
  - Ca phong duoc xem  -> chi luu id PHONG, khong liet ke tung nguoi.
  - Tat ca phong cua don vi duoc xem -> chi luu id DON VI, khong liet ke tung phong.

Script tu dung lai 'su that' tu danh muc + tap known (ca_nhan_list hop le) roi
doi chieu voi payload da nen.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from app.db.session import AsyncSessionLocal, engine
from app.services.security.security_acl_compressor import OrgCatalog


async def main(path: str) -> None:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    pq = data["phan_quyen"]
    io = pq["input_doffice"]
    payload = pq["chunk_payload"]
    ca_nhan = set(io["ca_nhan_list"])

    async with AsyncSessionLocal() as session:
        catalog = await OrgCatalog.from_session(session)

    known = {u for u in ca_nhan if u in catalog.user_location}

    allow_dv = set(payload["acl_allow_dv"])
    allow_pb = set(payload["acl_allow_pb"])
    allow_nv = set(payload["acl_allow_nv"])

    # Phong/don vi cua moi nguoi known.
    depts_of_known = {catalog.user_location[u][1] for u in known if catalog.user_location[u][1] is not None}
    units_of_known = {catalog.user_location[u][0] for u in known if catalog.user_location[u][0] is not None}

    def dept_full(pb: int) -> bool:
        m = catalog.department_members.get(pb, frozenset())
        return bool(m) and m <= known

    def unit_full(dv: int) -> bool:
        # Tat ca phong cua don vi deu duoc xem TOAN BO.
        depts = catalog.unit_departments.get(dv, frozenset())
        return bool(depts) and all(dept_full(pb) for pb in depts)

    # ---- BAT BIEN 1: khong liet ke thua (nguoi da nam trong pb/dv allow) ----
    redundant_nv = [
        u for u in allow_nv
        if (catalog.user_location.get(u, (None, None))[1] in allow_pb)
        or (catalog.user_location.get(u, (None, None))[0] in allow_dv)
    ]
    redundant_pb = [pb for pb in allow_pb if _unit_of_dept(catalog, pb) in allow_dv]

    # ---- BAT BIEN 2: phong duoc xem TOAN BO -> phai gop (khong de dang ca nhan) ----
    full_depts = {pb for pb in depts_of_known if dept_full(pb)}
    full_dept_not_rolled = [
        pb for pb in full_depts
        if pb not in allow_pb and _unit_of_dept(catalog, pb) not in allow_dv
    ]
    full_dept_listed_as_users = [
        pb for pb in full_depts
        if any(u in allow_nv for u in catalog.department_members.get(pb, frozenset()))
    ]

    # ---- BAT BIEN 3: don vi co TAT CA phong duoc xem toan bo -> phai gop len don vi ----
    full_units = {dv for dv in units_of_known if unit_full(dv)}
    full_unit_not_rolled = [dv for dv in full_units if dv not in allow_dv]

    print("== TONG QUAN ==")
    print(f"  known (nguoi hop le)     : {len(known)}")
    print(f"  payload: allow_dv={len(allow_dv)}  allow_pb={len(allow_pb)}  allow_nv={len(allow_nv)}")
    print(f"  phong CHAM toi          : {len(depts_of_known)} (trong do duoc xem TOAN BO: {len(full_depts)})")
    print(f"  don vi CHAM toi         : {len(units_of_known)} (trong do TAT CA phong toan bo: {len(full_units)})")
    print()
    print("== BAT BIEN 1: khong liet ke thua ==")
    print(f"  nguoi liet ke thua (da o pb/dv allow): {len(redundant_nv)} -> {redundant_nv[:10]}")
    print(f"  phong liet ke thua (da o dv allow)   : {len(redundant_pb)} -> {redundant_pb[:10]}")
    print()
    print("== BAT BIEN 2: phong toan bo phai GOP len phong (nguoi->phong) ==")
    print(f"  phong toan-bo nhung KHONG gop        : {len(full_dept_not_rolled)} -> {full_dept_not_rolled[:10]}")
    print(f"  phong toan-bo ma VAN liet ke tung nguoi: {len(full_dept_listed_as_users)} -> {full_dept_listed_as_users[:10]}")
    print()
    print("== BAT BIEN 3: don vi (moi phong toan bo) phai GOP len don vi (phong->don vi) ==")
    print(f"  don vi du dieu kien gop nhung KHONG gop: {len(full_unit_not_rolled)} -> {full_unit_not_rolled[:10]}")
    print()
    # Chi tiet 3 don vi da gop.
    print("== CHI TIET don vi da gop (acl_allow_dv) ==")
    for dv in sorted(allow_dv):
        depts = catalog.unit_departments.get(dv, frozenset())
        total = len(catalog.unit_members.get(dv, frozenset()))
        seen = len(catalog.unit_members.get(dv, frozenset()) & known)
        print(f"  dv {dv}: {seen}/{total} nguoi duoc xem, {len(depts)} phong; toan_bo={unit_full(dv)}")

    ok = not (redundant_nv or redundant_pb or full_dept_not_rolled or full_dept_listed_as_users or full_unit_not_rolled)
    print()
    print("KET LUAN:", "GOP DUNG nhu mong muon" if ok else "CO SAI LECH - xem tren")
    await engine.dispose()


def _unit_of_dept(catalog: OrgCatalog, pb: int) -> int | None:
    for dv, depts in catalog.unit_departments.items():
        if pb in depts:
            return dv
    return None


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "data/vb/acl_preview_1068586.json"
    asyncio.run(main(path))
