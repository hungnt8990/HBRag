"""Làm sạch văn bản trước khi chunk cho pipeline DOffice.

Mục tiêu của module này là chuẩn hoá nội dung văn bản (prose hoặc markdown bảng)
trước khi đưa vào chunker, nhằm:

1. Bỏ các marker phân trang do OCR/parser chèn (``--- Page N ---``,
   ``--- Trang N ---``, ``[Page N]``...) và NỐI LIỀN các câu bị marker cắt giữa
   chừng (marker nằm giữa câu thì nối bằng khoảng trắng, không tạo dòng trống).
2. Gỡ các ký tự NGOẠI LAI do OCR chèn nhầm (chữ Ả Rập, CJK, Hangul, Thái...),
   nhưng GIỮ NGUYÊN chữ Latin có dấu tiếng Việt, chữ số, dấu câu thông dụng và
   ký tự dựng bảng markdown (``|``, ``-``, ``:``).
3. Chuẩn hoá khoảng trắng: bỏ khoảng trắng cuối dòng và gộp nhiều dòng trống
   liên tiếp thành một.

Hàm :func:`clean_for_chunking` được thiết kế *idempotent*: chạy nhiều lần cho ra
cùng kết quả.
"""

from __future__ import annotations

import re
import unicodedata

# --- Marker phân trang -------------------------------------------------------
# Lõi nhận diện marker: "--- Page 3 ---", "--- Trang 12 ---", "[Page 4]",
# "[Trang 4]". Không phân biệt hoa/thường.
_PAGE_MARKER_CORE = r"(?:-{2,}\s*(?:page|trang)\s+\d+\s*-{2,}|\[\s*(?:page|trang)\s+\d+\s*\])"

# Bắt thêm 1 ký tự liền kề (nếu có) ở hai bên + khoảng trắng/xuống dòng bao quanh
# để quyết định cách nối lại hai mảnh văn bản bị marker chen vào.
_PAGE_MARKER_RE = re.compile(
    rf"(?P<before>\S)?[ \t]*\n*[ \t]*{_PAGE_MARKER_CORE}[ \t]*\n*[ \t]*(?P<after>\S)?",
    re.IGNORECASE,
)

# --- Ký tự ngoại lai / điều khiển -------------------------------------------
# Loại các block chữ viết ngoài hệ Latin thường gặp trong rác OCR. KHÔNG đụng tới
# dải U+0300-U+036F (dấu kết hợp) phòng trường hợp tiếng Việt còn ở dạng NFD —
# tuy nhiên ta đã NFC hoá trước nên tiếng Việt là ký tự dựng sẵn, an toàn.
_FOREIGN_SCRIPT_RE = re.compile(
    "["
    "Ͱ-Ͽ"  # Greek
    "Ѐ-ԯ"  # Cyrillic (+ Supplement)
    "԰-֏"  # Armenian
    "֐-׿"  # Hebrew
    "؀-ۿ"  # Arabic
    "܀-ݏ"  # Syriac
    "ݐ-ݿ"  # Arabic Supplement
    "ހ-޿"  # Thaana
    "ࠀ-ࣿ"  # Samaritan / Arabic Extended-A
    "ऀ-ॿ"  # Devanagari
    "฀-๿"  # Thai
    "　-〿"  # CJK Symbols & Punctuation
    "぀-ヿ"  # Hiragana / Katakana
    "㐀-䶿"  # CJK Extension A
    "一-鿿"  # CJK Unified Ideographs
    "ꥠ-꥿"  # Hangul Jamo Extended-A
    "가-힯"  # Hangul Syllables
    "豈-﫿"  # CJK Compatibility Ideographs
    "יִ-﷿"  # Hebrew / Arabic presentation forms-A
    "ﹰ-﻿"  # Arabic presentation forms-B
    "＀-￯"  # Halfwidth & Fullwidth forms
    "]+"
)

# Ký tự điều khiển và ký tự "vô hình" (zero-width, BOM, dấu định hướng bidi)
# nhưng GIỮ tab (\t) và xuống dòng (\n).
_CONTROL_RE = re.compile(
    "[\x00-\x08\x0b\x0c\x0e-\x1f\x7f​-‏‪-‮⁠﻿]"
)


def _replace_page_marker(match: re.Match[str]) -> str:
    """Quyết định cách nối lại hai mảnh quanh marker phân trang."""

    before = match.group("before") or ""
    after = match.group("after") or ""
    # Marker nằm GIỮA CÂU: ký tự trước là chữ/số và ký tự sau là chữ thường ->
    # nối liền bằng một khoảng trắng để câu không bị đứt đoạn.
    if before and after and before.isalnum() and after.islower():
        separator = " "
    else:
        # Còn lại coi như ranh giới đoạn -> dùng xuống dòng (sẽ được chuẩn hoá sau).
        separator = "\n"
    return f"{before}{separator}{after}"


def clean_for_chunking(text: str) -> str:
    """Làm sạch ``text`` trước khi chunk.

    Các bước: NFC hoá -> bỏ ký tự điều khiển -> bỏ marker phân trang (nối câu nếu
    marker cắt giữa câu) -> gỡ ký tự ngoại lai -> chuẩn hoá khoảng trắng. Hàm
    idempotent: ``clean_for_chunking(clean_for_chunking(x)) == clean_for_chunking(x)``.
    """

    if not text:
        return ""

    # 1) Chuẩn hoá Unicode về dạng dựng sẵn (NFC) + thống nhất ký tự xuống dòng.
    cleaned = unicodedata.normalize("NFC", str(text))
    cleaned = cleaned.replace("\r\n", "\n").replace("\r", "\n").replace(" ", " ")

    # 2) Bỏ ký tự điều khiển / vô hình.
    cleaned = _CONTROL_RE.sub("", cleaned)

    # 3) Bỏ marker phân trang, nối lại hai mảnh theo ngữ cảnh.
    cleaned = _PAGE_MARKER_RE.sub(_replace_page_marker, cleaned)

    # 4) Gỡ các block ký tự ngoại lai do OCR chèn.
    cleaned = _FOREIGN_SCRIPT_RE.sub("", cleaned)

    # 5) Chuẩn hoá khoảng trắng theo từng dòng.
    lines = [re.sub(r"[ \t]+", " ", line).rstrip() for line in cleaned.split("\n")]

    # 6) Gộp >1 dòng trống liên tiếp thành 1 dòng trống.
    normalized: list[str] = []
    previous_blank = False
    for line in lines:
        if not line:
            if normalized and not previous_blank:
                normalized.append("")
            previous_blank = True
            continue
        normalized.append(line)
        previous_blank = False

    return "\n".join(normalized).strip()


def _selfcheck() -> None:
    """Vài kiểm tra nhanh, chạy bằng ``python -m app.services.chunkers.chunker_text_cleaning``."""

    # Marker giữa câu -> nối bằng khoảng trắng.
    out = clean_for_chunking("Đây là một câu bị --- Page 2 --- cắt ngang giữa chừng.")
    assert "câu bị cắt ngang" in out, out
    assert "Page" not in out, out

    # Marker ở ranh giới đoạn -> xuống dòng, không để dòng trống kép.
    out = clean_for_chunking("Kết thúc đoạn.\n--- Trang 3 ---\nĐoạn mới bắt đầu.")
    assert "Kết thúc đoạn." in out and "Đoạn mới bắt đầu." in out, out
    assert "\n\n\n" not in out and "Trang" not in out, out

    # Dạng [Page N] cũng bị bỏ.
    out = clean_for_chunking("Nội dung [Page 10] tiếp theo")
    assert "Page" not in out, out

    # Ký tự Ả Rập / CJK bị gỡ, dấu tiếng Việt được giữ.
    out = clean_for_chunking("Phần mềm ứng dụng العربية 中文 đầy đủ")
    assert "Phần mềm ứng dụng" in out and "đầy đủ" in out, out
    assert "العربية" not in out and "中文" not in out, out

    # Giữ ký tự dựng bảng markdown.
    table = "| STT | Tên |\n| --- | --- |\n| 1 | Nguyễn Văn A |"
    out = clean_for_chunking(table)
    assert "| STT | Tên |" in out and "| --- | --- |" in out, out

    # Gộp dòng trống.
    out = clean_for_chunking("Dòng 1\n\n\n\nDòng 2   ")
    assert out == "Dòng 1\n\nDòng 2", repr(out)

    # Idempotent.
    sample = "Câu bị --- Page 1 --- cắt. \n\n\n中文\n| a | b |"
    assert clean_for_chunking(clean_for_chunking(sample)) == clean_for_chunking(sample)

    print("clean_for_chunking: tất cả self-check PASS")


if __name__ == "__main__":
    _selfcheck()
