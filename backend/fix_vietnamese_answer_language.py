from pathlib import Path

TARGET = Path("app/services/rag_answer_service.py")

if not TARGET.exists():
    raise SystemExit(f"Không tìm thấy {TARGET}. Hãy chạy script tại thư mục backend.")

raw = TARGET.read_bytes()
newline = "\r\n" if b"\r\n" in raw else "\n"
text = raw.decode("utf-8")
# normalize internally
text_lf = text.replace("\r\n", "\n")

old_language_rules = '''LANGUAGE_OUTPUT_RULES = (
    "Answer in the same language as the user's question unless the user asks for a "
    "different language. Preserve proper names, document titles, identifiers, and quoted "
    "source text exactly as written in the document text. Do not create a separate "
    "Sources, References, or Documents section; the application renders the source list separately. Never output "
    "hidden reasoning, chain-of-thought, scratchpad text, internal labels, search mechanics, or <think> tags."
)
'''
new_language_rules = '''LANGUAGE_OUTPUT_RULES = (
    "Default output language is Vietnamese for this application. Answer in Vietnamese "
    "for numeric-only, code-only, identifier-only, or ambiguous queries, because those "
    "queries do not carry a reliable language signal. Only answer in another language "
    "when the user's question is clearly written in that language or explicitly asks "
    "for that language. Never answer in Chinese unless the user asks in Chinese or asks "
    "for Chinese. Preserve proper names, document titles, identifiers, and quoted "
    "source text exactly as written in the document text. Do not create a separate "
    "Sources, References, or Documents section; the application renders the source list separately. Never output "
    "hidden reasoning, chain-of-thought, scratchpad text, internal labels, search mechanics, or <think> tags."
)
'''
if old_language_rules in text_lf:
    text_lf = text_lf.replace(old_language_rules, new_language_rules)
elif "Default output language is Vietnamese for this application" not in text_lf:
    print("CẢNH BÁO: Không tìm thấy block LANGUAGE_OUTPUT_RULES chuẩn để thay thế.")

old_dynamic = '''            "Dynamic answer requirements:\\n"
            "- Follow the Language constraint above; otherwise answer in the same language as the user's question unless the question asks otherwise.\\n"
'''
new_dynamic = '''            "Dynamic answer requirements:\\n"
            "- Follow the Language constraint above. For numeric/code-only queries, answer in Vietnamese. Never switch to Chinese unless the user explicitly asks for Chinese.\\n"
'''
if old_dynamic in text_lf:
    text_lf = text_lf.replace(old_dynamic, new_dynamic)

old_clean_call = '''                answer = self._clean_llm_answer(answer)
            assistant_message = await self._chat_repository.create_message(
'''
new_clean_call = '''                answer = self._clean_llm_answer(answer)
                answer = await self._repair_unrequested_answer_language(
                    query=query,
                    answer=answer,
                )
            assistant_message = await self._chat_repository.create_message(
'''
if old_clean_call in text_lf and "answer = await self._repair_unrequested_answer_language(\n                    query=query," not in text_lf:
    text_lf = text_lf.replace(old_clean_call, new_clean_call, 1)

old_stream_clean = '''                answer = self._clean_llm_answer("".join(answer_parts))
                if answer:
                    yield RagStreamEvent(event="token", data={"delta": answer})
'''
new_stream_clean = '''                answer = self._clean_llm_answer("".join(answer_parts))
                answer = await self._repair_unrequested_answer_language(
                    query=query,
                    answer=answer,
                )
                if answer:
                    yield RagStreamEvent(event="token", data={"delta": answer})
'''
if old_stream_clean in text_lf and text_lf.count("_repair_unrequested_answer_language(") < 2:
    text_lf = text_lf.replace(old_stream_clean, new_stream_clean, 1)

helper_marker = '''    @staticmethod
    def _answer_language_instruction(query: str) -> str:
'''
helper_code = '''    @staticmethod
    def _expects_vietnamese_answer(query: str) -> bool:
        return (
            RagAnswerService._looks_vietnamese_query(query)
            or RagAnswerService._looks_identifier_only_query(query)
            or not any(char.isalpha() for char in query or "")
        )

    @staticmethod
    def _contains_unrequested_cjk(answer: str) -> bool:
        return bool(re.search(r"[\\u3400-\\u4DBF\\u4E00-\\u9FFF]", answer or ""))

    async def _repair_unrequested_answer_language(self, *, query: str, answer: str) -> str:
        if not answer or not self._expects_vietnamese_answer(query):
            return answer
        if not self._contains_unrequested_cjk(answer):
            return answer

        repair_prompt = (
            "Bạn là bộ sửa ngôn ngữ đầu ra cho hệ thống RAG tiếng Việt. "
            "Viết lại bản nháp sau hoàn toàn bằng tiếng Việt tự nhiên. "
            "Giữ nguyên mã văn bản, số hiệu, ngày tháng, tên riêng, tên đơn vị, URL, "
            "tên sản phẩm và các dữ kiện đã có. Không thêm thông tin mới, không giải thích "
            "về việc dịch/sửa ngôn ngữ, không dùng tiếng Trung. Nếu có nội dung không chắc, "
            "giữ ý ở mức trung lập."
        )
        user_prompt = (
            f"Câu hỏi gốc:\\n{query}\\n\\n"
            f"Bản nháp cần sửa ngôn ngữ:\\n{answer}\\n\\n"
            "Yêu cầu: trả về duy nhất câu trả lời tiếng Việt đã sửa."
        )
        try:
            repaired = await self._llm_provider.generate(
                system_prompt=repair_prompt,
                user_prompt=user_prompt,
            )
            repaired = self._clean_llm_answer(repaired)
            if repaired and not self._contains_unrequested_cjk(repaired):
                return repaired
        except Exception:
            logger.warning("Failed to repair non-Vietnamese answer language", exc_info=True)
        return answer

'''
if helper_marker in text_lf and "def _expects_vietnamese_answer" not in text_lf:
    text_lf = text_lf.replace(helper_marker, helper_code + helper_marker)

TARGET.write_bytes(text_lf.replace("\n", newline).encode("utf-8"))
print("Đã fix rule trả lời tiếng Việt trong app/services/rag_answer_service.py")
