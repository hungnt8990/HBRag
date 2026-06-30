import type { Edge, Node } from "@xyflow/react";

export type CardTone =
  | "source"
  | "process"
  | "raw"
  | "pg"
  | "es"
  | "qdrant"
  | "rag"
  | "warn"
  | "answer";

export type CardData = {
  title: string;
  desc?: string;
  tone?: CardTone;
  shape?: "rounded" | "square" | "circle" | "diamond";
  tags?: string[];
  sections?: { title: string; items: string[] }[];
  rows?: { label: string; value: string }[];
  width?: number;
  minHeight?: number;
  fillColor?: string;
  borderColor?: string;
  textColor?: string;
  invisible?: boolean;
};

// Sơ đồ kiến trúc RAG dựng sẵn (kéo thả/sửa được, đồng bộ real-time qua Yjs).
// ID cố định -> nhiều người seed cùng lúc vẫn merge về cùng node, không nhân đôi.
export const initialNodes: Node<CardData>[] = [
  {
    id: "top-spacer",
    type: "card",
    position: { x: 0, y: -220 },
    data: { title: "", width: 1, minHeight: 1, invisible: true },
    draggable: false,
    selectable: false,
  },
  {
    id: "ingest-lane",
    type: "card",
    position: { x: 0, y: 60 },
    data: {
      title: "01. Luồng xử lý dữ liệu & xây dựng chỉ mục",
      desc: "Mục tiêu: chuẩn hóa tài liệu, metadata, phân quyền, chunk và vector trước khi phục vụ truy hồi.",
      tone: "source",
      width: 560,
      minHeight: 86,
      tags: ["Ingestion / Indexing flow"],
    },
  },
  {
    id: "src",
    type: "card",
    position: { x: 0, y: 210 },
    data: {
      title: "📄 Nguồn văn bản",
      desc: "Hệ thống phát sinh/quản lý văn bản.",
      tone: "source",
      tags: ["D-Office", "E-Office", "Pháp chế", "Quy chế", "Hồ sơ khác"],
    },
  },
  {
    id: "conn",
    type: "card",
    position: { x: 250, y: 210 },
    data: {
      title: "🔌 Connector / API",
      desc: "Lấy dữ liệu, giữ mã nguồn, mã văn bản, metadata và quyền truy cập gốc.",
      tone: "process",
      tags: ["source_id", "raw_acl", "source metadata"],
    },
  },
  {
    id: "raw",
    type: "card",
    position: { x: 500, y: 210 },
    data: {
      title: "🗂️ Kho tài liệu gốc",
      desc: "Lưu bản gốc để kiểm tra, tái xử lý, re-index hoặc đổi embedding model.",
      tone: "raw",
      tags: ["file gốc", "text OCR", "hash", "version nếu có"],
    },
  },
  {
    id: "pipe",
    type: "card",
    position: { x: 760, y: 210 },
    data: {
      title: "⚙️ Processing Pipeline",
      desc: "Chuẩn hóa tài liệu trước khi index.",
      tone: "process",
      width: 260,
      tags: ["OCR / Parser", "Clean text", "Metadata schema", "Tóm tắt / trích yếu", "Chunking theo cấu trúc", "Contextual chunking", "Embedding"],
    },
  },
  {
    id: "pg",
    type: "card",
    position: { x: 1100, y: 110 },
    data: {
      title: "🐘 PostgreSQL",
      desc: "Danh mục và phân quyền dùng để tạo scope truy hồi.",
      tone: "pg",
      tags: ["dm_don_vi", "dm_phong_ban", "dm_user", "role/group", "document_acl"],
    },
  },
  {
    id: "es",
    type: "card",
    position: { x: 1100, y: 280 },
    data: {
      title: "🔎 Elasticsearch",
      desc: "Full-text/BM25, lọc metadata và tìm keyword theo cả văn bản full lẫn từng chunk.",
      tone: "es",
      width: 360,
      minHeight: 230,
      sections: [
        { title: "ES Full Index", items: ["id_vb, số/ký hiệu", "tiêu đề, trích yếu, tóm tắt", "nội dung full, từ khóa", "metadata nghiệp vụ + ACL"] },
        { title: "ES Chunk Index", items: ["chunk_id, id_vb", "chunk text, contextual text", "heading path, vị trí/trang", "metadata chunk + ACL"] },
      ],
    },
  },
  {
    id: "qd",
    type: "card",
    position: { x: 1510, y: 280 },
    data: {
      title: "🧠 Qdrant",
      desc: "Vector search ở cấp văn bản và cấp đoạn; payload dùng để filter quyền, hiệu lực, lĩnh vực.",
      tone: "qdrant",
      width: 360,
      minHeight: 230,
      sections: [
        { title: "Full Collection", items: ["point_id = id_vb", "vector_full", "embed: tiêu đề + trích yếu + tóm tắt", "+ loại VB, lĩnh vực, từ khóa"] },
        { title: "Chunk Collection", items: ["point_id = chunk_id", "vector_chunk", "embed: heading + chunk text", "+ tiêu đề/trích yếu ngắn"] },
      ],
    },
  },
  {
    id: "query-lane",
    type: "card",
    position: { x: 0, y: 580 },
    data: {
      title: "02. Luồng truy hồi & sinh trả lời",
      desc: "Mục tiêu: hiểu câu hỏi, áp filter phân quyền, truy hồi hybrid, kiểm tra evidence và trả lời có dẫn nguồn.",
      tone: "rag",
      width: 560,
      minHeight: 86,
      tags: ["Query / Retrieval flow"],
    },
  },
  {
    id: "user",
    type: "card",
    position: { x: 0, y: 740 },
    data: { title: "👤 Người dùng hỏi", desc: "Câu hỏi tự nhiên, có thể kèm đơn vị, lĩnh vực, thời gian, loại văn bản.", tone: "rag" },
  },
  {
    id: "auth",
    type: "card",
    position: { x: 245, y: 740 },
    data: { title: "🔐 Auth / Scope", desc: "Lấy quyền từ PostgreSQL để tạo filter trước khi search.", tone: "rag", tags: ["user_scope", "ACL", "effective_status"] },
  },
  {
    id: "qu",
    type: "card",
    position: { x: 490, y: 740 },
    data: { title: "🧭 Query Understanding", desc: "Không đưa câu hỏi thô đi search ngay.", tone: "rag", width: 240, tags: ["rewrite", "multi-query", "intent", "metadata filter", "router full/chunk/hybrid"] },
  },
  {
    id: "hybrid",
    type: "card",
    position: { x: 770, y: 740 },
    data: { title: "⚡ Hybrid Retrieval", desc: "Tìm song song theo điều hướng, luôn áp filter quyền/hiệu lực.", tone: "rag", width: 250, tags: ["ES BM25", "ES Chunk", "Qdrant Full", "Qdrant Chunk"] },
  },
  {
    id: "fusion",
    type: "card",
    position: { x: 1060, y: 740 },
    data: { title: "🧮 Fusion + Reranker", desc: "Gộp BM25/vector bằng RRF hoặc weighted fusion, loại trùng và xếp hạng lại.", tone: "rag" },
  },
  {
    id: "crag",
    type: "card",
    position: { x: 1305, y: 740 },
    data: { title: "🧪 CRAG-lite", desc: "Kiểm tra evidence: mạnh/yếu/mơ hồ. Yếu thì retrieve lại hoặc báo thiếu căn cứ.", tone: "warn" },
  },
  {
    id: "ctx",
    type: "card",
    position: { x: 1550, y: 740 },
    data: { title: "📦 Context Builder", desc: "Ghép chunk, kéo metadata full, mở rộng parent-child: heading cha, điều/mục, đoạn trước/sau.", tone: "rag" },
  },
  {
    id: "ans",
    type: "card",
    position: { x: 1795, y: 740 },
    data: { title: "✅ RAG Answer", desc: "LLM trả lời có dẫn nguồn, trạng thái hiệu lực và đoạn trích làm căn cứ.", tone: "answer" },
  },
  {
    id: "es-fields",
    type: "card",
    position: { x: 0, y: 1060 },
    data: {
      title: "🔎 Elasticsearch — mô tả trường index",
      desc: "ES lưu text để tìm chính xác/từ khóa/BM25; metadata dùng để filter, sort, facet và kiểm soát quyền.",
      tone: "es",
      width: 470,
      minHeight: 260,
      rows: [
        { label: "ES Full", value: "title, so_ky_hieu, trich_yeu, tom_tat, noi_dung_full, keywords. Boost title/trích yếu/tóm tắt cao hơn nội dung full." },
        { label: "ES Chunk", value: "chunk_text, contextual_text, heading_path, số điều/khoản/mục. Dùng để tìm đúng đoạn/căn cứ." },
        { label: "Filter", value: "id_vb, source_system, loại VB, lĩnh vực, đơn vị ban hành, ngày hiệu lực, trạng thái hiệu lực, độ mật, acl_scope." },
      ],
    },
  },
  {
    id: "qd-fields",
    type: "card",
    position: { x: 510, y: 1060 },
    data: {
      title: "🧠 Qdrant — embed trường nào",
      desc: "Qdrant lưu vector + payload; không embed quyền, ngày tháng, ID kỹ thuật.",
      tone: "qdrant",
      width: 470,
      minHeight: 260,
      rows: [
        { label: "Full", value: "representative_text = tiêu đề + số/ký hiệu + trích yếu + tóm tắt + loại VB + lĩnh vực/chủ đề + từ khóa chính." },
        { label: "Chunk", value: "contextual_chunk_text = tiêu đề/trích yếu ngắn + heading path/chương/mục/điều + chunk text." },
        { label: "Payload", value: "id_vb, loại VB, lĩnh vực, đơn vị ban hành, ngày hiệu lực, trạng thái hiệu lực, độ mật, acl_scope, quan hệ VB." },
      ],
    },
  },
  {
    id: "meta-fields",
    type: "card",
    position: { x: 1020, y: 1060 },
    data: {
      title: "🧾 Metadata chung nên chuẩn hóa",
      desc: "Áp dụng nhất quán cho PostgreSQL, ES và Qdrant payload để filter không lệch nhau.",
      tone: "pg",
      width: 410,
      minHeight: 260,
      rows: [
        { label: "Định danh", value: "id_vb, source_system, source_doc_id, so_ky_hieu, hash, version nếu có." },
        { label: "Nghiệp vụ", value: "title, trich_yeu, loai_vb, linh_vuc, don_vi_ban_hanh, nguoi_ky." },
        { label: "Hiệu lực", value: "ngay_ban_hanh, ngay_hieu_luc, ngay_het_hieu_luc, trang_thai_hieu_luc." },
        { label: "Phân quyền", value: "don_vi_id, phong_ban_id, security_level, permission_group, acl_scope, visibility_scope." },
      ],
    },
  },
  {
    id: "field-use",
    type: "card",
    position: { x: 1470, y: 1060 },
    data: {
      title: "🧭 Cách dùng trường khi truy hồi",
      desc: "Tách rõ search field, vector field và metadata filter để tránh nhầm trường nào cũng embedding.",
      tone: "rag",
      width: 470,
      minHeight: 260,
      rows: [
        { label: "Keyword/BM25", value: "ES: title, so_ky_hieu, trich_yeu, tom_tat, noi_dung_full, chunk_text, heading_path." },
        { label: "Vector Full", value: "Qdrant: representative_text → vector_full." },
        { label: "Vector Chunk", value: "Qdrant: contextual_chunk_text → vector_chunk." },
        { label: "Filter", value: "acl_scope, security_level, don_vi_id, phong_ban_id, trang_thai_hieu_luc, ngày hiệu lực." },
        { label: "Dẫn nguồn", value: "id_vb, chunk_id, chunk_index, heading_path, page/position, source_system." },
      ],
    },
  },
  {
    id: "keys-note",
    type: "card",
    position: { x: 0, y: 1390 },
    data: {
      title: "🔑 Khóa liên kết & schema chung",
      tone: "process",
      width: 600,
      minHeight: 170,
      rows: [
        { label: "id_vb", value: "Khóa chung giữa kho gốc, PostgreSQL, ES Full/Chunk và Qdrant Full/Chunk." },
        { label: "chunk_id", value: "Truy vết từng đoạn về văn bản gốc qua chunk_index, heading_path, page/position." },
        { label: "schema", value: "Số/ký hiệu, loại VB, lĩnh vực, đơn vị ban hành, hiệu lực, ACL, nguồn dữ liệu." },
      ],
    },
  },
  {
    id: "validity-note",
    type: "card",
    position: { x: 650, y: 1390 },
    data: {
      title: "📌 Hiệu lực & quan hệ văn bản",
      tone: "process",
      width: 600,
      minHeight: 170,
      rows: [
        { label: "Hiệu lực", value: "Gắn ngày ban hành, ngày hiệu lực, ngày hết hiệu lực, trạng thái hiệu lực vào cả full và chunk." },
        { label: "Quan hệ", value: "can_cu_vb, tham_chieu_vb, vb_thay_the, vb_bi_thay_the, vb_lien_quan." },
        { label: "Filter sớm", value: "Quyền và hiệu lực phải áp ngay khi search ES/Qdrant, không chỉ lọc sau." },
      ],
    },
  },
  {
    id: "algo-note",
    type: "card",
    position: { x: 1300, y: 1390 },
    data: {
      title: "🧩 Thuật toán RAG giữ lại",
      tone: "rag",
      width: 600,
      minHeight: 170,
      rows: [
        { label: "Multi-query", value: "Rewrite thành nhiều truy vấn tương đương, truy hồi song song rồi fusion/rerank." },
        { label: "Contextual chunking", value: "Chunk kèm tiêu đề, chương/mục/điều và trích yếu ngắn trước khi index." },
        { label: "CRAG-lite", value: "Kiểm tra kết quả truy hồi; nếu yếu thì retrieve lại hoặc trả lời không đủ căn cứ." },
      ],
    },
  },
];

const e = (id: string, source: string, target: string, animated = false): Edge => ({
  id,
  source,
  target,
  animated,
});

export const initialEdges: Edge[] = [
  e("src-conn", "src", "conn"),
  e("conn-raw", "conn", "raw"),
  e("raw-pipe", "raw", "pipe"),
  e("pipe-pg", "pipe", "pg"),
  e("pipe-es", "pipe", "es"),
  e("pipe-qd", "pipe", "qd"),
  e("user-auth", "user", "auth"),
  e("auth-qu", "auth", "qu"),
  e("qu-hybrid", "qu", "hybrid"),
  e("hybrid-fusion", "hybrid", "fusion", true),
  e("fusion-crag", "fusion", "crag"),
  e("crag-ctx", "crag", "ctx"),
  e("ctx-ans", "ctx", "ans", true),
  e("es-esfields", "es", "es-fields"),
  e("qd-qdfields", "qd", "qd-fields"),
  e("pg-metafields", "pg", "meta-fields"),
  e("hybrid-fielduse", "hybrid", "field-use"),
  e("field-use-keys", "field-use", "keys-note"),
  e("field-use-validity", "field-use", "validity-note"),
  e("field-use-algo", "field-use", "algo-note"),
];

export const TONE_STYLE: Record<CardTone, { bg: string; border: string }> = {
  source: { bg: "#eaf1ff", border: "#b8ccff" },
  process: { bg: "#f8fafc", border: "#cad6e4" },
  raw: { bg: "#eff9ff", border: "#b9e2f7" },
  pg: { bg: "#e8f7f1", border: "#a6ddc7" },
  es: { bg: "#fff1e8", border: "#fed0b7" },
  qdrant: { bg: "#fff7df", border: "#f5d28e" },
  rag: { bg: "#f2ebff", border: "#d6c2ff" },
  warn: { bg: "#fff0f0", border: "#fecaca" },
  answer: { bg: "#ecfdf5", border: "#a7f3d0" },
};
