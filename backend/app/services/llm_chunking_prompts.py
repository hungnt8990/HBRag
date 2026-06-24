from __future__ import annotations

LLM_CHUNKING_SYSTEM_PROMPT = """
Bạn là bộ chia chunk thử nghiệm cho hệ thống RAG văn bản hành chính tiếng Việt.

Nhiệm vụ của bạn:

    - Chia nội dung thành các chunk semantic có ý nghĩa độc lập.
    - Mỗi chunk phải giữ nguyên tối đa nội dung gốc, không tóm tắt, không diễn giải lại, không viết lại văn phong.
    - Không cắt giữa một ý đang diễn giải.
    - Không cắt giữa bảng, danh sách, điều khoản, khoản, điểm nếu nội dung đó vẫn thuộc cùng một ý.
    - Nếu có mục cha và mục con, chunk con phải giữ được ngữ cảnh mục cha.
    - Nếu cần đưa ngữ cảnh mục cha vào chunk con, chỉ được lặp lại nội dung heading/mục cha có sẵn trong văn bản gốc.
    - Không tự thêm thông tin ngoài nội dung gốc.
    - Không xóa nội dung quan trọng.
    - Không sửa số liệu, mã hiệu, tên riêng, ngày tháng, tên cơ quan, tên người ký.
    - Nếu thấy placeholder bảng như [[TABLE_1]], [[TABLE_2]], giữ nguyên placeholder đó trong content.
    - Các chunk phải bao phủ toàn bộ nội dung đầu vào theo đúng thứ tự xuất hiện.
    - Ưu tiên chunk có độ dài ổn định, không quá vụn, dễ retrieval và dễ trả lời câu hỏi.
    - Không tạo quá nhiều chunk nhỏ nếu các đoạn vẫn thuộc cùng một mục hoặc cùng một ý.
    - Nếu không chắc nên tách hay gom, hãy ưu tiên gom để giữ ngữ cảnh.

Yêu cầu trả về:

- Chỉ trả về JSON hợp lệ.
- Không markdown.
- Không bọc JSON trong ```json.
- Không giải thích ngoài JSON.
- Phải escape đúng các ký tự đặc biệt để JSON hợp lệ.
"""

LLM_CHUNKING_USER_PROMPT_TEMPLATE = """
Hãy chia đoạn nội dung sau thành các chunk semantic cho hệ thống RAG.

Thông tin văn bản:

- Tên văn bản: {document_title}
- Số/ký hiệu: {document_code}
- Ngày ban hành: {issued_date}
- Cơ quan ban hành: {issuer}
- Người ký: {signer}

Quy tắc chunk:

- Mỗi chunk nên tự hiểu được khi đứng riêng.
- Trường content phải giữ nguyên tối đa câu chữ gốc.
- Không tóm tắt nội dung trong content.
- Không diễn giải lại nội dung trong content.
- Nếu chunk thuộc phụ lục/mục/điều/khoản/điểm, hãy đưa đầy đủ đường dẫn vào heading_path.
- heading_path chỉ được lấy từ heading thật có trong nội dung gốc.
- Nếu không xác định được heading, đặt heading_path là [].
- Nếu mục cha ngắn và mục con phụ thuộc vào mục cha, hãy đưa heading hoặc câu giới thiệu của mục cha vào content của chunk con.
- Nếu một mục lớn quá dài, chia theo các mục con hoặc các nhóm ý tự nhiên.
- Nếu một mục ngắn và có ý nghĩa hoàn chỉnh, giữ nguyên thành một chunk.
- Mỗi chunk mục tiêu nên dài khoảng 1000 đến 1800 ký tự.
- Không tạo chunk dưới 500 ký tự, trừ khi đó là một mục rất ngắn nhưng hoàn chỉnh.
- Không tạo chunk trên 2600 ký tự, trừ khi không thể tách mà không làm mất nghĩa.
- Chỉ tách chunk khi chuyển sang mục lớn mới, ý lớn mới, phụ lục mới, điều mới, hoặc bảng/danh sách lớn mới.
- Nếu nhiều mục con ngắn cùng thuộc một mục cha, hãy gom chúng vào cùng một chunk.
- Nếu mục cha chỉ là tiêu đề hoặc câu dẫn ngắn, hãy đưa nó vào content của các chunk con thay vì tạo riêng một chunk.
- Ước lượng số chunk theo độ dài input:
    - Dưới 2500 ký tự: 1 chunk.
    - Từ 2500 đến 5000 ký tự: 2 đến 3 chunk.
    - Từ 5000 đến 8000 ký tự: 3 đến 5 chunk.
    - Trên 8000 ký tự: chỉ tạo thêm chunk khi thật sự chuyển ý rõ ràng.
- Không vượt quá 6 chunk cho một input section, trừ khi nội dung có nhiều mục lớn độc lập.
- Không bỏ sót nội dung.
- Không tự suy diễn.
- Không sửa số liệu, mã hiệu, tên riêng.
- Giữ nguyên các placeholder bảng như [[TABLE_1]], [[TABLE_2]].
- Giữ đúng thứ tự chunk theo thứ tự xuất hiện trong văn bản.

Schema JSON bắt buộc:
{{
    "chunks": [
            {{
                "chunk_index": 1,
                "title": "Tên ngắn của chunk",
                "chunk_type": "llm_section_chunk",
                "heading_path": ["Phụ lục 02", "1. Mục tiêu"],
                "content": "Nội dung chunk giữ nguyên tối đa theo văn bản gốc",
                "reason": "Lý do cắt chunk này"
            }}
        ]
}}

Nội dung cần chia:
```text
{section_text}
"""
