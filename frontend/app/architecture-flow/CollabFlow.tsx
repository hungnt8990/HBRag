"use client";

import {
  addEdge,
  applyEdgeChanges,
  applyNodeChanges,
  Background,
  Controls,
  Handle,
  MiniMap,
  Position,
  ReactFlow,
  ReactFlowProvider,
  useReactFlow,
  useViewport,
} from "@xyflow/react";
import type {
  Connection,
  Edge,
  EdgeChange,
  Node,
  NodeChange,
  NodeProps,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import * as Y from "yjs";
import { WebsocketProvider } from "y-websocket";

import {
  CardData,
  initialEdges,
  initialNodes,
  TONE_STYLE,
} from "./initialDiagram";

const ROOM = "architecture-v11-fields-3";
const LOCAL_ORIGIN = "local-edit";

// ws(s)://<api-host>/collab — y-websocket sẽ nối thêm "/<room>".
function wsBaseUrl(): string {
  const api = (process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000").replace(/\/$/, "");
  return `${api.replace(/^http/, "ws")}/collab`;
}

const COLORS = ["#2563eb", "#dc2626", "#059669", "#d97706", "#7c3aed", "#db2777", "#0891b2"];

type Peer = { clientId: number; name: string; color: string; cursor?: { x: number; y: number } };

function stripEphemeral(node: Node): Node {
  const clone = { ...node } as Record<string, unknown>;
  delete clone.selected;
  delete clone.dragging;
  return clone as Node;
}

function nodesFromY(yNodes: Y.Map<Node>, prev: Node[]): Node[] {
  const sel = new Map(prev.map((n) => [n.id, n.selected]));
  return Array.from(yNodes.values()).map((n) => ({ ...n, selected: sel.get(n.id) ?? false }));
}

function edgesFromY(yEdges: Y.Map<Edge>): Edge[] {
  return Array.from(yEdges.values());
}

// -------------------------------------------------------------- custom node --
function CardNode({ data, selected }: NodeProps<Node<CardData>>) {
  if (data.invisible) {
    return <div style={{ width: data.width ?? 1, height: data.minHeight ?? 1, opacity: 0, pointerEvents: "none" }} />;
  }

  const tone = TONE_STYLE[data.tone ?? "process"];
  return (
    <div
      style={{
        width: data.width ?? 210,
        minHeight: data.minHeight,
        background: tone.bg,
        border: `1px solid ${tone.border}`,
        borderRadius: 14,
        padding: "10px 12px",
        boxShadow: selected ? "0 0 0 2px #1d4ed8" : "0 6px 14px rgba(15,23,42,.08)",
        fontFamily: "Inter, Segoe UI, Roboto, Arial, sans-serif",
      }}
    >
      <Handle type="target" position={Position.Left} style={{ background: "#64748b" }} />
      <div style={{ fontSize: 14, fontWeight: 700, color: "#142033", lineHeight: 1.25 }}>{data.title}</div>
      {data.desc ? (
        <div style={{ marginTop: 5, fontSize: 11.6, color: "#475569", lineHeight: 1.4 }}>{data.desc}</div>
      ) : null}
      {data.tags?.length ? (
        <div style={{ display: "flex", flexWrap: "wrap", gap: 5, marginTop: 8 }}>
          {data.tags.map((tag) => (
            <span
              key={tag}
              style={{
                border: "1px solid rgba(148,163,184,.52)",
                background: "rgba(255,255,255,.78)",
                borderRadius: 999,
                color: "#334155",
                fontSize: 10.4,
                fontWeight: 700,
                lineHeight: 1.1,
                padding: "4px 7px",
                whiteSpace: "nowrap",
              }}
            >
              {tag}
            </span>
          ))}
        </div>
      ) : null}
      {data.sections?.length ? (
        <div style={{ display: "grid", gridTemplateColumns: data.sections.length > 1 ? "1fr 1fr" : "1fr", gap: 8, marginTop: 10 }}>
          {data.sections.map((section) => (
            <div
              key={section.title}
              style={{
                background: "rgba(255,255,255,.7)",
                border: "1px solid rgba(148,163,184,.42)",
                borderRadius: 12,
                padding: "8px 9px",
              }}
            >
              <div style={{ color: "#1e293b", fontSize: 11.5, fontWeight: 800, marginBottom: 5 }}>{section.title}</div>
              <ul style={{ margin: "0 0 0 15px", padding: 0, color: "#334155", fontSize: 10.8, lineHeight: 1.35 }}>
                {section.items.map((item) => (
                  <li key={item} style={{ margin: "2px 0" }}>
                    {item}
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      ) : null}
      {data.rows?.length ? (
        <div style={{ display: "grid", gap: 7, marginTop: 10 }}>
          {data.rows.map((row) => (
            <div
              key={row.label}
              style={{
                display: "grid",
                gridTemplateColumns: "92px 1fr",
                gap: 8,
                borderTop: "1px solid rgba(148,163,184,.3)",
                paddingTop: 7,
              }}
            >
              <span style={{ color: "#0f172a", fontSize: 10.8, fontWeight: 800 }}>{row.label}</span>
              <span style={{ color: "#334155", fontSize: 10.8, lineHeight: 1.35 }}>{row.value}</span>
            </div>
          ))}
        </div>
      ) : null}
      <Handle type="source" position={Position.Right} style={{ background: "#64748b" }} />
    </div>
  );
}

const nodeTypes = { card: CardNode };

// ----------------------------------------------------------- remote cursors --
function RemoteCursors({ peers }: { peers: Peer[] }) {
  const { flowToScreenPosition } = useReactFlow();
  useViewport(); // re-render khi pan/zoom để con trỏ bám đúng vị trí.
  return (
    <>
      {peers
        .filter((p) => p.cursor)
        .map((p) => {
          const s = flowToScreenPosition(p.cursor!);
          return (
            <div
              key={p.clientId}
              style={{ position: "fixed", left: s.x, top: s.y, transform: "translate(-2px,-2px)", pointerEvents: "none", zIndex: 50 }}
            >
              <svg width="16" height="16" viewBox="0 0 16 16" fill={p.color}>
                <path d="M0 0l5 14 2-6 6-2z" />
              </svg>
              <span style={{ marginLeft: 8, background: p.color, color: "#fff", fontSize: 10.5, fontWeight: 700, padding: "1px 6px", borderRadius: 6, whiteSpace: "nowrap" }}>
                {p.name}
              </span>
            </div>
          );
        })}
    </>
  );
}

// --------------------------------------------------------------- main canvas --
function FlowCanvas() {
  const [nodes, setNodes] = useState<Node[]>(initialNodes);
  const [edges, setEdges] = useState<Edge[]>(initialEdges);
  const [status, setStatus] = useState<"connecting" | "connected" | "disconnected">("connecting");
  const [peers, setPeers] = useState<Peer[]>([]);

  const me = useMemo(
    () => ({
      name: `Người dùng ${Math.floor(Math.random() * 900 + 100)}`,
      color: COLORS[Math.floor(Math.random() * COLORS.length)],
    }),
    [],
  );

  // Tài nguyên Yjs giữ trong ref -> bền với StrictMode (mỗi lần effect chạy tạo mới + dọn sạch).
  const docRef = useRef<Y.Doc | null>(null);
  const providerRef = useRef<WebsocketProvider | null>(null);
  const yNodesRef = useRef<Y.Map<Node> | null>(null);
  const yEdgesRef = useRef<Y.Map<Edge> | null>(null);

  // --- vòng đời: tạo doc/provider, đồng bộ, presence; dọn khi unmount ---
  useEffect(() => {
    const doc = new Y.Doc();
    const provider = new WebsocketProvider(wsBaseUrl(), ROOM, doc, { connect: true });
    const yNodes = doc.getMap<Node>("nodes");
    const yEdges = doc.getMap<Edge>("edges");
    docRef.current = doc;
    providerRef.current = provider;
    yNodesRef.current = yNodes;
    yEdgesRef.current = yEdges;

    const refresh = () => {
      setNodes((prev) => nodesFromY(yNodes, prev));
      setEdges(edgesFromY(yEdges));
    };

    const onStatus = (e: { status: string }) =>
      setStatus(e.status === "connected" ? "connected" : e.status === "disconnected" ? "disconnected" : "connecting");

    const onSync = (isSynced: boolean) => {
      if (isSynced && yNodes.size === 0) {
        // Seed sơ đồ mặc định CHỈ khi room trống. ID cố định -> nhiều người seed vẫn không nhân đôi.
        doc.transact(() => {
          initialNodes.forEach((n) => yNodes.set(n.id, n));
          initialEdges.forEach((ed) => yEdges.set(ed.id, ed));
        }, LOCAL_ORIGIN);
      }
      refresh();
    };

    const onNodesY = (_e: Y.YMapEvent<Node>, txn: Y.Transaction) => {
      if (txn.origin !== LOCAL_ORIGIN) setNodes((prev) => nodesFromY(yNodes, prev));
    };
    const onEdgesY = (_e: Y.YMapEvent<Edge>, txn: Y.Transaction) => {
      if (txn.origin !== LOCAL_ORIGIN) setEdges(edgesFromY(yEdges));
    };

    // presence
    const awareness = provider.awareness;
    awareness.setLocalStateField("user", me);
    const onAwareness = () => {
      const list: Peer[] = [];
      awareness.getStates().forEach((state, clientId) => {
        if (clientId === awareness.clientID) return;
        const u = (state.user ?? {}) as { name?: string; color?: string };
        list.push({
          clientId,
          name: u.name ?? "Ẩn danh",
          color: u.color ?? "#64748b",
          cursor: state.cursor as { x: number; y: number } | undefined,
        });
      });
      setPeers(list);
    };

    provider.on("status", onStatus);
    provider.on("sync", onSync);
    yNodes.observe(onNodesY);
    yEdges.observe(onEdgesY);
    awareness.on("change", onAwareness);
    if (yNodes.size > 0) refresh();
    onAwareness();

    return () => {
      provider.off("status", onStatus);
      provider.off("sync", onSync);
      yNodes.unobserve(onNodesY);
      yEdges.unobserve(onEdgesY);
      awareness.off("change", onAwareness);
      provider.destroy();
      doc.destroy();
      docRef.current = null;
      providerRef.current = null;
      yNodesRef.current = null;
      yEdgesRef.current = null;
    };
  }, [me]);

  // --- handlers React Flow -> Yjs (đọc tài nguyên từ ref) ---
  const onNodesChange = useCallback((changes: NodeChange[]) => {
    const doc = docRef.current;
    const yNodes = yNodesRef.current;
    setNodes((nds) => {
      const next = applyNodeChanges(changes, nds);
      if (doc && yNodes) {
        doc.transact(() => {
          for (const ch of changes) {
            if (ch.type === "remove") yNodes.delete(ch.id);
            else if (ch.type === "position") {
              const n = next.find((x) => x.id === ch.id);
              if (n) yNodes.set(n.id, stripEphemeral(n));
            }
          }
        }, LOCAL_ORIGIN);
      }
      return next;
    });
  }, []);

  const onEdgesChange = useCallback((changes: EdgeChange[]) => {
    const doc = docRef.current;
    const yEdges = yEdgesRef.current;
    setEdges((eds) => {
      const next = applyEdgeChanges(changes, eds);
      if (doc && yEdges) {
        doc.transact(() => {
          for (const ch of changes) if (ch.type === "remove") yEdges.delete(ch.id);
        }, LOCAL_ORIGIN);
      }
      return next;
    });
  }, []);

  const onConnect = useCallback((conn: Connection) => {
    const doc = docRef.current;
    const yEdges = yEdgesRef.current;
    if (!doc || !yEdges) return;
    setEdges((eds) => {
      const next = addEdge(conn, eds);
      const added = next.find((e) => !eds.some((x) => x.id === e.id));
      if (added) doc.transact(() => yEdges.set(added.id, added), LOCAL_ORIGIN);
      return next;
    });
  }, []);

  const onNodeDoubleClick = useCallback((_e: React.MouseEvent, node: Node) => {
    const doc = docRef.current;
    const yNodes = yNodesRef.current;
    if (!doc || !yNodes) return;
    const data = node.data as CardData;
    const title = window.prompt("Sửa tiêu đề node:", data.title);
    if (title == null) return;
    const desc = window.prompt("Sửa mô tả:", data.desc ?? "") ?? "";
    doc.transact(
      () => yNodes.set(node.id, { ...stripEphemeral(node), data: { ...data, title, desc } }),
      LOCAL_ORIGIN,
    );
  }, []);

  const { screenToFlowPosition } = useReactFlow();
  const onPaneMouseMove = useCallback(
    (e: React.MouseEvent) => {
      const provider = providerRef.current;
      if (!provider) return;
      const p = screenToFlowPosition({ x: e.clientX, y: e.clientY });
      provider.awareness.setLocalStateField("cursor", { x: p.x, y: p.y });
    },
    [screenToFlowPosition],
  );

  const addCard = useCallback(() => {
    const doc = docRef.current;
    const yNodes = yNodesRef.current;
    if (!doc || !yNodes) return;
    const id = `n-${Math.random().toString(36).slice(2, 10)}`;
    const node: Node<CardData> = {
      id,
      type: "card",
      position: { x: 120, y: 200 + Math.random() * 60 },
      data: { title: "Node mới", desc: "Nhấp đúp để sửa", tone: "process" },
    };
    doc.transact(() => yNodes.set(id, node), LOCAL_ORIGIN);
  }, []);

  const restoreDefaultDiagram = useCallback(() => {
    const doc = docRef.current;
    const yNodes = yNodesRef.current;
    const yEdges = yEdgesRef.current;
    if (!doc || !yNodes || !yEdges) return;
    if (!window.confirm("Khôi phục sơ đồ kiến trúc v11? Các chỉnh sửa hiện tại trong room này sẽ được thay bằng bản mặc định.")) {
      return;
    }
    doc.transact(() => {
      yNodes.clear();
      yEdges.clear();
      initialNodes.forEach((n) => yNodes.set(n.id, n));
      initialEdges.forEach((ed) => yEdges.set(ed.id, ed));
    }, LOCAL_ORIGIN);
    setNodes(initialNodes);
    setEdges(initialEdges);
  }, []);

  const statusInfo = {
    connecting: { text: "Đang kết nối…", color: "#d97706" },
    connected: { text: "Đã kết nối", color: "#059669" },
    disconnected: { text: "Mất kết nối", color: "#dc2626" },
  }[status];

  return (
    <div style={{ position: "fixed", inset: 0 }}>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onConnect={onConnect}
        onNodeDoubleClick={onNodeDoubleClick}
        onPaneMouseMove={onPaneMouseMove}
        fitView
        fitViewOptions={{ padding: 0.42 }}
        proOptions={{ hideAttribution: true }}
      >
        <Background gap={18} color="#e2e8f0" />
        <Controls />
        <MiniMap pannable zoomable />
        <RemoteCursors peers={peers} />
      </ReactFlow>

      {/* Thanh công cụ */}
      <div
        style={{
          position: "fixed", top: 14, right: 14, display: "flex", gap: 10, alignItems: "center",
          background: "rgba(255,255,255,.92)", border: "1px solid #d8e1ee", borderRadius: 12,
          padding: "8px 12px", boxShadow: "0 8px 18px rgba(15,23,42,.08)",
          fontFamily: "Inter, Segoe UI, Roboto, Arial, sans-serif", zIndex: 40,
        }}
      >
        <strong style={{ fontSize: 14, color: "#142033" }}>Kiến trúc RAG văn bản v11 · đồng chỉnh</strong>
        <span style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 12, color: statusInfo.color, fontWeight: 700 }}>
          <span style={{ width: 9, height: 9, borderRadius: "50%", background: statusInfo.color, display: "inline-block" }} />
          {statusInfo.text}
        </span>
        <button onClick={addCard} style={{ fontSize: 12.5, fontWeight: 700, border: "1px solid #b8ccff", background: "#eaf1ff", color: "#1d4ed8", borderRadius: 8, padding: "5px 10px", cursor: "pointer" }}>
          + Thêm node
        </button>
        <button onClick={restoreDefaultDiagram} style={{ fontSize: 12.5, fontWeight: 700, border: "1px solid #d6c2ff", background: "#f2ebff", color: "#6d28d9", borderRadius: 8, padding: "5px 10px", cursor: "pointer" }}>
          Khôi phục sơ đồ v11
        </button>
        <button onClick={() => navigator.clipboard?.writeText(window.location.href)} style={{ fontSize: 12.5, fontWeight: 700, border: "1px solid #d8e1ee", background: "#fff", color: "#334155", borderRadius: 8, padding: "5px 10px", cursor: "pointer" }}>
          Sao chép link chia sẻ
        </button>
        <div style={{ display: "flex", alignItems: "center", gap: 4, marginLeft: 4 }}>
          <span title={me.name} style={{ width: 22, height: 22, borderRadius: "50%", background: me.color, color: "#fff", fontSize: 10, fontWeight: 700, display: "grid", placeItems: "center", border: "2px solid #fff", boxShadow: "0 1px 3px rgba(0,0,0,.2)" }}>
            Bạn
          </span>
          {peers.map((p) => (
            <span key={p.clientId} title={p.name} style={{ width: 22, height: 22, borderRadius: "50%", background: p.color, color: "#fff", fontSize: 10, fontWeight: 700, display: "grid", placeItems: "center", border: "2px solid #fff", boxShadow: "0 1px 3px rgba(0,0,0,.2)" }}>
              {p.name.replace(/[^0-9]/g, "").slice(-2) || "?"}
            </span>
          ))}
          <span style={{ fontSize: 11.5, color: "#64748b", marginLeft: 4 }}>{peers.length + 1} online</span>
        </div>
      </div>

      <div style={{ position: "fixed", bottom: 14, left: 14, fontSize: 11.5, color: "#64748b", background: "rgba(255,255,255,.9)", border: "1px solid #e2e8f0", borderRadius: 10, padding: "6px 10px", fontFamily: "Inter, Segoe UI, Roboto, Arial, sans-serif", zIndex: 40 }}>
        Kéo node để di chuyển · kéo từ chấm bên phải sang node khác để nối · nhấp đúp để sửa · Delete để xóa. Mọi thay đổi tự lưu &amp; đồng bộ real-time.
      </div>
    </div>
  );
}

export default function CollabFlow() {
  return (
    <ReactFlowProvider>
      <FlowCanvas />
    </ReactFlowProvider>
  );
}
