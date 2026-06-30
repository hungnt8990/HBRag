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
};

// Sơ đồ kiến trúc RAG dựng sẵn (kéo thả/sửa được, đồng bộ real-time qua Yjs).
// ID cố định -> nhiều người seed cùng lúc vẫn merge về cùng node, không nhân đôi.
export const initialNodes: Node<CardData>[] = [
  // ----- Luồng ingest -----
  { id: "src", type: "card", position: { x: 0, y: 80 }, data: { title: "📄 Nguồn văn bản", desc: "D-Office, E-Office, pháp chế, quy chế, hồ sơ khác.", tone: "source" } },
  { id: "conn", type: "card", position: { x: 250, y: 80 }, data: { title: "🔌 Connector / API", desc: "Lấy dữ liệu, giữ mã nguồn, mã văn bản, metadata, ACL gốc.", tone: "process" } },
  { id: "raw", type: "card", position: { x: 500, y: 80 }, data: { title: "🗂️ Kho tài liệu gốc", desc: "Bản gốc để kiểm tra, tái xử lý, re-index, đổi embedding model.", tone: "raw" } },
  { id: "pipe", type: "card", position: { x: 750, y: 80 }, data: { title: "⚙️ Processing Pipeline", desc: "OCR/Parser, clean text, metadata, tóm tắt, chunking, contextual, embedding.", tone: "process" } },
  { id: "pg", type: "card", position: { x: 1040, y: -40 }, data: { title: "🐘 PostgreSQL", desc: "Danh mục + phân quyền tạo scope truy hồi (đơn vị, phòng ban, user, ACL).", tone: "pg" } },
  { id: "es", type: "card", position: { x: 1040, y: 90 }, data: { title: "🔎 Elasticsearch", desc: "BM25/keyword + filter metadata, theo cả văn bản full lẫn từng chunk.", tone: "es" } },
  { id: "qd", type: "card", position: { x: 1040, y: 220 }, data: { title: "🧠 Qdrant", desc: "Vector search cấp văn bản + cấp đoạn; payload để filter quyền/hiệu lực.", tone: "qdrant" } },

  // ----- Luồng truy hồi -----
  { id: "user", type: "card", position: { x: 0, y: 440 }, data: { title: "👤 Người dùng hỏi", desc: "Câu hỏi tự nhiên, có thể kèm đơn vị, lĩnh vực, thời gian, loại VB.", tone: "rag" } },
  { id: "auth", type: "card", position: { x: 230, y: 440 }, data: { title: "🔐 Auth / Scope", desc: "Lấy quyền từ PostgreSQL để tạo filter trước khi search.", tone: "rag" } },
  { id: "qu", type: "card", position: { x: 460, y: 440 }, data: { title: "🧭 Query Understanding", desc: "Rewrite, multi-query, intent, metadata filter, router full/chunk/hybrid.", tone: "rag" } },
  { id: "hybrid", type: "card", position: { x: 710, y: 440 }, data: { title: "⚡ Hybrid Retrieval", desc: "ES BM25 + ES Chunk + Qdrant Full + Qdrant Chunk, luôn áp filter quyền.", tone: "rag" } },
  { id: "fusion", type: "card", position: { x: 960, y: 440 }, data: { title: "🧮 Fusion + Reranker", desc: "RRF/weighted fusion, loại trùng, xếp hạng lại.", tone: "rag" } },
  { id: "crag", type: "card", position: { x: 1190, y: 440 }, data: { title: "🧪 CRAG-lite", desc: "Kiểm tra evidence: mạnh/yếu/mơ hồ. Yếu thì retrieve lại / báo thiếu căn cứ.", tone: "warn" } },
  { id: "ctx", type: "card", position: { x: 1420, y: 440 }, data: { title: "📦 Context Builder", desc: "Ghép chunk, kéo metadata, mở rộng parent-child (heading cha, điều/mục).", tone: "rag" } },
  { id: "ans", type: "card", position: { x: 1650, y: 440 }, data: { title: "✅ RAG Answer", desc: "LLM trả lời có dẫn nguồn, trạng thái hiệu lực, đoạn trích căn cứ.", tone: "answer" } },
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
