from __future__ import annotations

import re
import unicodedata

__all__ = ["clean_text"]


HTML_TAG_RE = re.compile(r"<[^>]+>")
MD_TABLE_SEP_RE = re.compile(r"(?m)^\s*\|\s*[-:]+.*?$")
PAGE_NUM_RE = re.compile(r"(?m)^[ \t]*(?:\d{1,4}\s*/\s*\d{1,4}|-\s*\d{1,3}\s*-|\d{1,3})[ \t]*$")
MULTI_SPACE_RE = re.compile(r"[ \t]+")
MULTI_NL_RE = re.compile(r"\n{3,}")

LETTERHEAD_RE = re.compile(
    r"^\s*"
    r"(?:[A-ZĐÀ-Ỹ][A-ZĐÀ-Ỹ \-/\n]{1,80}[\s_\-]*)?"
    r"CỘNG\s+(?:HÒA|HOÀ|HOA)\s+X[ÃA]\s+HỘI\s+CHỦ\s+NGH[ĨI]A\s+VIỆT\s+NAM"
    r"\s*[_\-]*\s*"
    r"Độc\s+lập\s*[-–—]\s*Tự\s+do\s*[-–—]\s*Hạnh\s+ph[úu]c"
    r"\s*[_\-]*\s*",
    re.UNICODE,
)

CHAR_TRANS = str.maketrans(
    {
        "\u00a0": " ",
        "\u00ad": None,
        "\u200b": " ",
        "\u2028": "\n",
        "\u2029": "\n",
        "\u2013": "-",
        "\u2014": "-",
        "\u2212": "-",
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
    }
)

TCVN3_MAP: dict[str, str] = {
    "µ": "à",
    "¸": "á",
    "¶": "ả",
    "·": "ã",
    "¹": "ạ",
    "¨": "ă",
    "»": "ằ",
    "¾": "ắ",
    "¼": "ẳ",
    "½": "ẵ",
    "Æ": "ặ",
    "©": "â",
    "Ç": "ầ",
    "Ê": "ấ",
    "È": "ẩ",
    "É": "ẫ",
    "Ë": "ậ",
    "®": "đ",
    "§": "Đ",
    "Ì": "è",
    "Ð": "é",
    "Î": "ẻ",
    "Ï": "ẽ",
    "Ñ": "ẹ",
    "ª": "ê",
    "Ò": "ề",
    "Õ": "ế",
    "Ó": "ể",
    "Ô": "ễ",
    "Ö": "ệ",
    "ê": "ờ",
    "×": "ì",
    "Ý": "í",
    "Ü": "ĩ",
    "Þ": "ị",
    "Ø": "ỉ",
    "ì": "ỡ",
    "ß": "ò",
    "ã": "ó",
    "å": "ồ",
    "è": "ố",
    "æ": "ổ",
    "é": "ộ",
    "«": "ô",
    "¬": "ơ",
    "ë": "ở",
    "í": "ớ",
    "î": "ợ",
    "ó": "ú",
    "ñ": "ủ",
    "ô": "ụ",
    "­": "ư",
    "ø": "ứ",
    "ö": "ử",
    "÷": "ữ",
    "ù": "ự",
    "õ": "ừ",
    "û": "ỷ",
}

TCVN3_MARKERS = set("§®µ¬«ª¨©æåßñøîë¶¹·¸ð")


def _is_vietnamese_unicode_letter(ch: str) -> bool:
    """Nhận diện chữ tiếng Việt Unicode nằm ngoài vùng Latin-1."""
    code_point = ord(ch)
    if code_point <= 0xFF:
        return False
    return 0x0100 <= code_point <= 0x017F or 0x01A0 <= code_point <= 0x01B0 or 0x1E00 <= code_point <= 0x1EFF


def _has_no_vietnamese_unicode(line: str) -> bool:
    """Kiểm tra dòng chưa có chữ tiếng Việt Unicode dựng sẵn."""
    return not any(_is_vietnamese_unicode_letter(ch) for ch in line)


def _has_words(line: str) -> bool:
    """Chỉ xử lý các dòng có chữ, tránh nhận nhầm dòng toàn số/ký hiệu."""
    return sum("a" <= ch <= "z" or "A" <= ch <= "Z" for ch in line) >= 2


def _line_is_tcvn3(line: str) -> bool:
    """Một dòng được xem là TCVN3 khi có đủ dấu hiệu lỗi và chưa có Unicode Việt."""
    if not _has_no_vietnamese_unicode(line) or not _has_words(line):
        return False
    return sum(ch in TCVN3_MARKERS for ch in line) >= 2


def _detect_tcvn3_doc(text: str) -> bool:
    """Phát hiện văn bản có ít nhất một dòng TCVN3 đáng tin cậy."""
    return any(_line_is_tcvn3(line) for line in text.split("\n"))


def _convert_tcvn3_line(line: str) -> str:
    """Chuyển các ký tự TCVN3 trong một dòng sang Unicode."""
    return "".join(TCVN3_MAP.get(ch, ch) for ch in line)


def _fix_tcvn3(text: str) -> str:
    """Chỉ chuyển các dòng TCVN3, giữ nguyên các dòng Unicode sạch."""
    if not text:
        return text
    if not _detect_tcvn3_doc(text):
        return text.replace("\u00ad", "")

    lines = []
    for line in text.split("\n"):
        if _has_no_vietnamese_unicode(line) and _has_words(line) and any(ch in TCVN3_MAP for ch in line):
            lines.append(_convert_tcvn3_line(line))
        else:
            lines.append(line.replace("\u00ad", ""))
    return unicodedata.normalize("NFC", "\n".join(lines))


def clean_text(text: str) -> str:
    """Làm sạch một chuỗi văn bản và trả về chuỗi Unicode đã chuẩn hóa."""
    if not isinstance(text, str) or not text:
        return ""

    cleaned = text
    if _detect_tcvn3_doc(cleaned):
        cleaned = _fix_tcvn3(cleaned)

    # Bỏ các thẻ HTML, thay bằng khoảng trắng để không dính chữ.
    cleaned = HTML_TAG_RE.sub(" ", cleaned)

    # Bỏ các dòng phân cách bảng markdown, giữ lại nội dung bảng.
    cleaned = MD_TABLE_SEP_RE.sub("", cleaned)

    # Bỏ phần quốc hiệu/tiêu ngữ thường nằm ở đầu văn bản.
    cleaned = LETTERHEAD_RE.sub("", cleaned)

    # Chuẩn hóa ký tự đặc biệt: NBSP, soft hyphen, smart quote, dash...
    cleaned = cleaned.translate(CHAR_TRANS)

    # Bỏ phần ký hiệu nhấn mạnh markdown.
    cleaned = cleaned.replace("**", "").replace("*", "")
    cleaned = cleaned.replace('""', '"')

    # Bỏ các dòng chỉ chứa số trang như "12", "- 12 -" hoặc "12/100".
    cleaned = PAGE_NUM_RE.sub("", cleaned)

    # Gộp khoảng trắng ngang và giới hạn số dòng trống liên tiếp.
    cleaned = MULTI_SPACE_RE.sub(" ", cleaned)
    cleaned = MULTI_NL_RE.sub("\n\n", cleaned)
    return unicodedata.normalize("NFC", cleaned).strip()
