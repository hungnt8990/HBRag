# HBRag Frontend — Ghi nhớ trạng thái (để khôi phục ngữ cảnh sau khi clear session)

> Súc tích cố ý (tiết kiệm token). Next.js 15 (App Router) + React 19 + TypeScript + Tailwind.
> Base URL backend: `NEXT_PUBLIC_API_BASE_URL` (`.env.local`, mặc định http://127.0.0.1:8000).
> Mọi request qua `lib/api.ts` (`requestJson`, Bearer token ở localStorage `hbrag_access_token`).

## Cấu trúc
- SPA 1 trang `app/page.tsx` (state `activeView`), nav nội bộ. View danh sách văn bản = `DocumentSearchView`
  (trong `app/page.tsx`) — KHÔNG phải `DocumentLibraryPanel`/`AutoQueueView` (2 cái này code chết, đừng sửa).
- `app/login`, `app/layout.tsx`, `app/globals.css`.

## Tính năng đã thêm trong dự án
### 1. Trang đồng chỉnh sơ đồ kiến trúc — `/architecture-flow`
- File: `app/architecture-flow/` (`page.tsx` dynamic ssr:false, `CollabFlow.tsx`, `initialDiagram.ts`,
  `cardEditing.ts`, `NodeBlocks.tsx`).
- Deps: `@xyflow/react` (React Flow v12) + `yjs` + `y-websocket`.
- Inline-edit trong node: chọn node -> bấm thẳng vào tiêu đề/tag/ô bảng để sửa tại chỗ, hover hiện +/× thêm-xoá.
  `CardData` có thêm `notes` (khối văn bản tự do) + `tables` (bảng nhiều cột) ngoài `tags`/`sections`/`rows`.
  - `cardEditing.ts`: ops thuần add/set/remove cho mọi khối (một logic dùng chung inline + panel).
  - `NodeBlocks.tsx`: `EditableText` (click-để-sửa, commit onBlur/Enter, huỷ Esc), các *Block render read+edit,
    `NodeContent` (nội dung node), `NodePanelEditors` (form động panel), `NodeEditContext` (patch theo id).
  - `CollabFlow.tsx` cấp `patchNodeData(id, patch)` qua context -> commit Yjs như commitNode. Inline dùng
    class `nodrag nopan` + stopPropagation để không kéo/pan node khi đang sửa. Panel tái dùng chính các *Block.
- Real-time đồng chỉnh (kéo thả/nối/sửa node thấy nhau + con trỏ + presence) qua Yjs, nối WS
  `${API}/collab/<ROOM>` (đổi http->ws). `ROOM` khai báo trong `CollabFlow.tsx`.
- Yjs binding: Y.Map "nodes"/"edges" + origin guard `LOCAL_ORIGIN` tránh echo; tạo doc/provider trong `useEffect`
  + dọn khi unmount (bền với React StrictMode). Seed initialDiagram khi room trống (ID cố định -> không nhân đôi).

### 2. Filter "đã có point Qdrant" ở danh sách văn bản
- `lib/api.ts` `listDocuments({qdrantIndexed})` -> query `qdrant_indexed` cho `GET /api/documents`.
- `DocumentSearchView`: checkbox "Chỉ văn bản đã có point trên Qdrant" + state `embeddedOnly`.
- `DocumentListItem` (lib/api.ts) có sẵn `qdrant_point_count`, `chunk_count`, `vector_indexed_count`, `status`.

## Lệnh
- `npm install` (deps đã ghi package.json). `npm run dev` (port cố định qua scripts/dev-fixed-port.mjs).
- `npx tsc --noEmit` + `npx eslint .` (cảnh báo unused ở code chết AutoQueueView là CÓ SẴN, bỏ qua).

## Lưu ý token
- Phần kéo-thả real-time chỉ verify được khi mở 2 tab trình duyệt (mình không tự test browser được).
- Để nhiều người ở máy khác cùng chỉnh: backend `--host 0.0.0.0` + `NEXT_PUBLIC_API_BASE_URL` trỏ IP thật + mở firewall.
