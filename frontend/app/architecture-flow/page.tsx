"use client";

import dynamic from "next/dynamic";

// React Flow + Yjs cần window/WebSocket -> chỉ render phía client (tắt SSR).
const CollabFlow = dynamic(() => import("./CollabFlow"), {
  ssr: false,
  loading: () => (
    <div style={{ position: "fixed", inset: 0, display: "grid", placeItems: "center", color: "#64748b", fontFamily: "Inter, Segoe UI, sans-serif" }}>
      Đang tải sơ đồ đồng chỉnh…
    </div>
  ),
});

export default function ArchitectureFlowPage() {
  return <CollabFlow />;
}
