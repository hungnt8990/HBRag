"use client";

import {
  addEdge,
  applyEdgeChanges,
  applyNodeChanges,
  Background,
  BaseEdge,
  Controls,
  EdgeLabelRenderer,
  Handle,
  MarkerType,
  MiniMap,
  NodeResizer,
  Position,
  ReactFlow,
  ReactFlowProvider,
  getSmoothStepPath,
  useReactFlow,
  useStore,
  useViewport,
} from "@xyflow/react";
import type {
  Connection,
  Edge,
  EdgeChange,
  EdgeProps,
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
import { NodeContent, NodeEditContext, NodePanelEditors } from "./NodeBlocks";

const ROOM = "architecture-v12-editor";
const LOCAL_ORIGIN = "local-edit";

// ws(s)://<api-host>/collab — y-websocket sẽ nối thêm "/<room>".
function wsBaseUrl(): string {
  const api = (process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000").replace(/\/$/, "");
  return `${api.replace(/^http/, "ws")}/collab`;
}

const COLORS = ["#2563eb", "#dc2626", "#059669", "#d97706", "#7c3aed", "#db2777", "#0891b2"];
const COLOR_SWATCHES = ["#eaf1ff", "#e8f7f1", "#fff1e8", "#fff7df", "#f2ebff", "#fff0f0", "#ecfdf5", "#ffffff", "#f8fafc"];
const BORDER_SWATCHES = ["#b8ccff", "#a6ddc7", "#fed0b7", "#f5d28e", "#d6c2ff", "#fecaca", "#a7f3d0", "#94a3b8", "#334155"];
const TEXT_SWATCHES = ["#142033", "#1d4ed8", "#047857", "#c2410c", "#6d28d9", "#b91c1c", "#334155", "#ffffff"];
const EDGE_SWATCHES = ["#64748b", "#1d4ed8", "#047857", "#c2410c", "#6d28d9", "#b91c1c", "#0f172a"];

type Peer = { clientId: number; name: string; color: string; cursor?: { x: number; y: number }; editing?: boolean };

function stripEphemeral(node: Node): Node {
  const clone = { ...node } as Record<string, unknown>;
  delete clone.selected;
  delete clone.dragging;
  return clone as Node;
}

function stripEphemeralEdge(edge: Edge): Edge {
  const clone = { ...edge } as Record<string, unknown>;
  delete clone.selected;
  return clone as Edge;
}

function nodesFromY(yNodes: Y.Map<Node>, prev: Node[]): Node[] {
  const sel = new Map(prev.map((n) => [n.id, n.selected]));
  return Array.from(yNodes.values()).map((n) => ({ ...n, selected: sel.get(n.id) ?? false }));
}

// Giữ `selected` local (phù du, không đồng bộ) khi tái dựng từ Yjs -> tránh echo qua selection.
function edgesFromY(yEdges: Y.Map<Edge>, prev: Edge[]): Edge[] {
  const sel = new Map(prev.map((e) => [e.id, e.selected]));
  return Array.from(yEdges.values()).map((e) => ({ ...e, selected: sel.get(e.id) ?? false }));
}

const HIDDEN_HANDLE_STYLE = { opacity: 0 };
const HANDLE_STYLE = {
  width: 8,
  height: 8,
  background: "#64748b",
  border: "2px solid #fff",
  opacity: 0.6,
  zIndex: 20,
};

function DualHandle({ position, sourceId, targetId }: { position: Position; sourceId: string; targetId: string }) {
  return (
    <>
      <Handle type="target" position={position} id={targetId} style={{ ...HANDLE_STYLE, ...HIDDEN_HANDLE_STYLE }} />
      <Handle type="source" position={position} id={sourceId} style={HANDLE_STYLE} />
    </>
  );
}

// -------------------------------------------------------------- custom node --
function CardNode({ id, data, selected }: NodeProps<Node<CardData>>) {
  if (data.invisible) {
    return <div style={{ width: data.width ?? 1, height: data.minHeight ?? 1, opacity: 0, pointerEvents: "none" }} />;
  }

  const tone = TONE_STYLE[data.tone ?? "process"];
  const shape = data.shape ?? "rounded";
  const fillColor = data.fillColor ?? tone.bg;
  const borderColor = data.borderColor ?? tone.border;
  const width = data.width ?? (shape === "circle" || shape === "diamond" ? 190 : 210);
  const minHeight = data.minHeight ?? (shape === "circle" || shape === "diamond" ? width : undefined);
  const content = <NodeContent id={id} data={data} selected={selected} />;

  if (shape === "diamond") {
    return (
      <div style={{ position: "relative", width, height: minHeight }}>
        <NodeResizer color="#2563eb" isVisible={selected} minWidth={120} minHeight={120} />
        <DualHandle position={Position.Top} sourceId="top" targetId="top-target" />
        <DualHandle position={Position.Left} sourceId="left" targetId="left-target" />
        <DualHandle position={Position.Right} sourceId="right" targetId="right-target" />
        <div
          style={{
            position: "absolute",
            inset: 18,
            background: fillColor,
            border: `1px solid ${borderColor}`,
            boxShadow: selected ? "0 0 0 2px #1d4ed8" : "0 6px 14px rgba(15,23,42,.08)",
            transform: "rotate(45deg)",
          }}
        />
        <div
          style={{
            position: "absolute",
            inset: 28,
            display: "grid",
            placeItems: "center",
            fontFamily: "Inter, Segoe UI, Roboto, Arial, sans-serif",
            textAlign: "center",
          }}
        >
          <div style={{ maxWidth: width * 0.64 }}>{content}</div>
        </div>
        <DualHandle position={Position.Bottom} sourceId="bottom" targetId="bottom-target" />
      </div>
    );
  }

  return (
    <div
      style={{
        position: "relative",
        width,
        minHeight,
        background: fillColor,
        border: `1px solid ${borderColor}`,
        borderRadius: shape === "circle" ? "50%" : shape === "square" ? 4 : 14,
        padding: "10px 12px",
        boxShadow: selected ? "0 0 0 2px #1d4ed8" : "0 6px 14px rgba(15,23,42,.08)",
        display: shape === "circle" ? "grid" : "block",
        placeItems: shape === "circle" ? "center" : undefined,
        fontFamily: "Inter, Segoe UI, Roboto, Arial, sans-serif",
        textAlign: shape === "circle" ? "center" : "left",
      }}
    >
      <NodeResizer color="#2563eb" isVisible={selected} minWidth={80} minHeight={60} />
      <DualHandle position={Position.Top} sourceId="top" targetId="top-target" />
      <DualHandle position={Position.Left} sourceId="left" targetId="left-target" />
      <DualHandle position={Position.Right} sourceId="right" targetId="right-target" />
      <div style={{ maxWidth: shape === "circle" ? width * 0.74 : undefined }}>{content}</div>
      <DualHandle position={Position.Bottom} sourceId="bottom" targetId="bottom-target" />
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

function EditableEdge({
  id,
  sourceX,
  sourceY,
  targetX,
  targetY,
  sourcePosition,
  targetPosition,
  style = {},
  label,
  markerEnd,
  selected,
  labelStyle,
  data,
}: EdgeProps) {
  const { setEdges } = useReactFlow();
  const zoom = useStore((store) => store.transform[2]) || 1;
  const [isEditing, setIsEditing] = useState(false);
  const rawLabel = typeof label === "string" ? label : "";
  const textStyle = (labelStyle ?? {}) as { fill?: string; fontSize?: number; fontStyle?: string; fontWeight?: number | string };
  const hasOffset = (data as { segOffset?: number } | undefined)?.segOffset !== undefined;
  const segOffset = Number((data as { segOffset?: number } | undefined)?.segOffset ?? 0);
  const isVertical = sourcePosition === Position.Bottom || sourcePosition === Position.Top;
  const midY = (sourceY + targetY) / 2 + (isVertical ? segOffset : 0);
  const midX = (sourceX + targetX) / 2 + (!isVertical ? segOffset : 0);
  const [fallbackPath] = getSmoothStepPath({
    sourceX,
    sourceY,
    sourcePosition,
    targetX,
    targetY,
    targetPosition,
    borderRadius: 12,
  });
  const edgePath = isVertical
    ? `M ${sourceX} ${sourceY} L ${sourceX} ${midY} L ${targetX} ${midY} L ${targetX} ${targetY}`
    : `M ${sourceX} ${sourceY} L ${midX} ${sourceY} L ${midX} ${targetY} L ${targetX} ${targetY}`;
  const finalPath = hasOffset ? edgePath : fallbackPath;
  const labelX = hasOffset ? (isVertical ? (sourceX + targetX) / 2 : midX) : (sourceX + targetX) / 2;
  const labelY = hasOffset ? (isVertical ? midY : (sourceY + targetY) / 2) : (sourceY + targetY) / 2;
  const handleX = isVertical ? labelX : midX;
  const handleY = isVertical ? midY : labelY;

  useEffect(() => {
    if (!selected) setIsEditing(false);
  }, [selected]);

  const updateEdge = useCallback((patch: Partial<Edge>) => {
    setEdges((current) => current.map((edge) => (edge.id === id ? { ...edge, ...patch } : edge)));
  }, [id, setEdges]);

  const updateOffset = useCallback((nextOffset: number) => {
    setEdges((current) =>
      current.map((edge) =>
        edge.id === id ? { ...edge, data: { ...(edge.data ?? {}), segOffset: nextOffset } } : edge,
      ),
    );
  }, [id, setEdges]);

  const onHandleDrag = useCallback((event: React.MouseEvent) => {
    event.stopPropagation();
    event.preventDefault();
    const start = isVertical ? event.clientY : event.clientX;
    const startOffset = segOffset;

    const onMove = (moveEvent: MouseEvent) => {
      const current = isVertical ? moveEvent.clientY : moveEvent.clientX;
      updateOffset(startOffset + (current - start) / zoom);
    };
    const onUp = () => {
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
    };
    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
  }, [isVertical, segOffset, updateOffset, zoom]);

  return (
    <>
      <BaseEdge path={finalPath} markerEnd={markerEnd} style={{ ...style, strokeWidth: Number(style.strokeWidth ?? 2) }} />
      <path d={finalPath} fill="none" stroke="transparent" strokeWidth={18} style={{ pointerEvents: "stroke" }} />
      {selected ? (
        <EdgeLabelRenderer>
          <div
            className="nodrag nopan"
            onMouseDown={onHandleDrag}
            onDoubleClick={(event) => {
              event.stopPropagation();
              updateOffset(0);
            }}
            title={isVertical ? "Kéo lên/xuống để chỉnh đoạn nối" : "Kéo trái/phải để chỉnh đoạn nối"}
            style={{
              position: "absolute",
              transform: `translate(-50%, -50%) translate(${handleX}px,${handleY}px)`,
              width: 12,
              height: 12,
              borderRadius: 999,
              background: "#2563eb",
              border: "2px solid #fff",
              boxShadow: "0 2px 8px rgba(37,99,235,.35)",
              cursor: isVertical ? "ns-resize" : "ew-resize",
              pointerEvents: "all",
              zIndex: 20,
            }}
          />
        </EdgeLabelRenderer>
      ) : null}
      {(rawLabel.trim() || selected) ? (
        <EdgeLabelRenderer>
          <div
            className="nodrag nopan"
            onDoubleClick={(event) => {
              event.stopPropagation();
              setIsEditing(true);
            }}
            style={{
              position: "absolute",
              transform: `translate(-50%, -50%) translate(${labelX}px,${labelY}px)`,
              pointerEvents: "all",
              background: "#fff",
              border: selected ? "2px solid #2563eb" : "1px solid #cbd5e1",
              borderRadius: 8,
              boxShadow: "0 6px 16px rgba(15,23,42,.12)",
              padding: "4px 8px",
              zIndex: 21,
            }}
          >
            {isEditing ? (
              <input
                autoFocus
                value={rawLabel}
                onChange={(event) => updateEdge({ label: event.target.value })}
                onBlur={() => setIsEditing(false)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" || event.key === "Escape") setIsEditing(false);
                }}
                style={{
                  width: Math.max(70, rawLabel.length * 8 + 24),
                  border: 0,
                  outline: 0,
                  color: textStyle.fill ?? "#334155",
                  fontSize: textStyle.fontSize ?? 11,
                  fontStyle: textStyle.fontStyle ?? "normal",
                  fontWeight: textStyle.fontWeight ?? 800,
                  textAlign: "center",
                }}
                placeholder="Nhãn"
              />
            ) : (
              <span
                style={{
                  color: textStyle.fill ?? "#334155",
                  fontSize: textStyle.fontSize ?? 11,
                  fontStyle: textStyle.fontStyle ?? "normal",
                  fontWeight: textStyle.fontWeight ?? 800,
                  whiteSpace: "nowrap",
                }}
              >
                {rawLabel || "Nhãn"}
              </span>
            )}
          </div>
        </EdgeLabelRenderer>
      ) : null}
    </>
  );
}

const edgeTypes = { editable: EditableEdge };

// --------------------------------------------------------------- main canvas --
function FlowCanvas() {
  const [nodes, setNodes] = useState<Node[]>(initialNodes);
  const [edges, setEdges] = useState<Edge[]>(initialEdges);
  const [status, setStatus] = useState<"connecting" | "connected" | "disconnected">("connecting");
  const [peers, setPeers] = useState<Peer[]>([]);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [selectedEdgeId, setSelectedEdgeId] = useState<string | null>(null);
  // Mô hình KHÓA: mặc định chỉ xem. `editing` = MÌNH đang giữ quyền sửa (đồng chỉnh bị chặn:
  // chỉ 1 người sửa tại 1 thời điểm). Khóa "mềm" phát qua awareness -> tự nhả khi đóng tab/mất mạng.
  const [editing, setEditing] = useState(false);

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

  const commitNode = useCallback((node: Node) => {
    setNodes((current) => current.map((item) => (item.id === node.id ? node : item)));
    const doc = docRef.current;
    const yNodes = yNodesRef.current;
    if (doc && yNodes) {
      doc.transact(() => yNodes.set(node.id, stripEphemeral(node)), LOCAL_ORIGIN);
    }
  }, []);

  // Patch data của 1 node theo id (dùng cho inline-edit trong node) + commit Yjs.
  const patchNodeData = useCallback((nodeId: string, patch: Partial<CardData>) => {
    let changed: Node | null = null;
    setNodes((current) =>
      current.map((item) => {
        if (item.id !== nodeId) return item;
        changed = { ...item, data: { ...(item.data as CardData), ...patch } };
        return changed;
      }),
    );
    const doc = docRef.current;
    const yNodes = yNodesRef.current;
    if (changed && doc && yNodes) {
      doc.transact(() => yNodes.set(nodeId, stripEphemeral(changed!)), LOCAL_ORIGIN);
    }
  }, []);

  const commitEdge = useCallback((edge: Edge) => {
    setEdges((current) => current.map((item) => (item.id === edge.id ? edge : item)));
    const doc = docRef.current;
    const yEdges = yEdgesRef.current;
    if (doc && yEdges) {
      doc.transact(() => yEdges.set(edge.id, edge), LOCAL_ORIGIN);
    }
  }, []);

  const selectedNode = useMemo(
    () => nodes.find((node) => node.id === selectedNodeId) as Node<CardData> | undefined,
    [nodes, selectedNodeId],
  );
  const selectedEdge = useMemo(
    () => edges.find((edge) => edge.id === selectedEdgeId),
    [edges, selectedEdgeId],
  );

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
      setEdges((prev) => edgesFromY(yEdges, prev));
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
      if (txn.origin !== LOCAL_ORIGIN) setEdges((prev) => edgesFromY(yEdges, prev));
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
          editing: Boolean(state.editing),
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

  // Bắt thay đổi edge phát sinh trong EditableEdge (segOffset/nhãn) mà không đi qua commitEdge.
  // CHỈ ghi edge THỰC SỰ khác nội dung Yjs (đã bỏ `selected` phù du) -> idempotent, phá vòng echo:
  // khi state đến từ remote nó y hệt yEdges nên không ghi lại -> không broadcast ngược -> không OOM.
  useEffect(() => {
    const doc = docRef.current;
    const yEdges = yEdgesRef.current;
    if (!doc || !yEdges) return;
    const changed = edges
      .map(stripEphemeralEdge)
      .filter((edge) => {
        const prev = yEdges.get(edge.id);
        return !prev || JSON.stringify(stripEphemeralEdge(prev)) !== JSON.stringify(edge);
      });
    if (changed.length === 0) return;
    doc.transact(() => {
      changed.forEach((edge) => yEdges.set(edge.id, edge));
    }, LOCAL_ORIGIN);
  }, [edges]);

  // Phát trạng thái "mình đang giữ quyền sửa" cho những người khác qua awareness (ephemeral).
  useEffect(() => {
    providerRef.current?.awareness.setLocalStateField("editing", editing);
  }, [editing]);

  // Ai (KHÁC mình) đang giữ quyền sửa -> dùng để chặn & hiển thị "có người đang sửa".
  const lockedByOther = useMemo(() => peers.find((p) => p.editing) ?? null, [peers]);

  // Bấm "Sửa": chỉ giành quyền khi đã kết nối & chưa ai giữ. Tie-break race bằng clientID nhỏ nhất.
  const requestEdit = useCallback(() => {
    if (status !== "connected") {
      window.alert("Chưa kết nối máy chủ đồng chỉnh — không thể sửa (thay đổi sẽ không được lưu). Vui lòng thử lại khi đã kết nối.");
      return;
    }
    const provider = providerRef.current;
    if (!provider) return;
    const awareness = provider.awareness;
    const holder = Array.from(awareness.getStates().entries()).find(
      ([id, st]) => id !== awareness.clientID && (st as { editing?: boolean }).editing,
    );
    if (holder) {
      const name = ((holder[1] as { user?: { name?: string } }).user?.name) ?? "Người khác";
      window.alert(`🔒 ${name} đang chỉnh sửa. Vui lòng đợi họ bấm "Lưu & Xong".`);
      return;
    }
    awareness.setLocalStateField("editing", true);
    setEditing(true);
    // Chờ awareness lan truyền rồi kiểm tra tranh chấp: nếu có người clientID nhỏ hơn cũng vừa
    // bật editing -> nhường họ (mình quay về chỉ xem), tránh 2 người sửa cùng lúc do race.
    window.setTimeout(() => {
      const editors = Array.from(awareness.getStates().entries()).filter(
        ([, st]) => (st as { editing?: boolean }).editing,
      );
      if (editors.length > 1) {
        const minId = Math.min(...editors.map(([id]) => id));
        if (minId !== awareness.clientID) {
          awareness.setLocalStateField("editing", false);
          setEditing(false);
          window.alert("Có người khác vừa bắt đầu sửa cùng lúc. Vui lòng đợi họ lưu xong.");
        }
      }
    }, 400);
  }, [status]);

  // Bấm "Lưu & Xong": nhả khóa (thay đổi đã sync real-time + backend tự lưu ≤2s). Về chế độ xem.
  const finishEdit = useCallback(() => {
    providerRef.current?.awareness.setLocalStateField("editing", false);
    setEditing(false);
    setSelectedNodeId(null);
    setSelectedEdgeId(null);
  }, []);

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
            else if (ch.type !== "select" && "id" in ch) {
              const n = next.find((x) => x.id === ch.id);
              if (n) yNodes.set(n.id, stripEphemeral(n));
            }
          }
        }, LOCAL_ORIGIN);
      }
      return next;
    });
    if (changes.some((ch) => ch.type === "remove" && ch.id === selectedNodeId)) {
      setSelectedNodeId(null);
    }
  }, [selectedNodeId]);

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
    if (changes.some((ch) => ch.type === "remove" && ch.id === selectedEdgeId)) {
      setSelectedEdgeId(null);
    }
  }, [selectedEdgeId]);

  const onConnect = useCallback((conn: Connection) => {
    if (!conn.source || !conn.target) return;
    const id = `e-${conn.source}-${conn.sourceHandle ?? "out"}-${conn.target}-${conn.targetHandle ?? "in"}-${Date.now()}`;
    const edge: Edge = {
      ...conn,
      id,
      type: "editable",
      animated: false,
      markerEnd: { type: MarkerType.ArrowClosed, color: "#64748b" },
      style: { stroke: "#64748b", strokeWidth: 2 },
    };
    setEdges((eds) => {
      const next = addEdge(edge, eds);
      const doc = docRef.current;
      const yEdges = yEdgesRef.current;
      if (doc && yEdges) doc.transact(() => yEdges.set(edge.id, edge), LOCAL_ORIGIN);
      return next;
    });
  }, []);

  const onNodeDoubleClick = useCallback((_e: React.MouseEvent, node: Node) => {
    setSelectedNodeId(node.id);
    setSelectedEdgeId(null);
  }, []);

  const onSelectionChange = useCallback(({ nodes: selectedNodes, edges: selectedEdges }: { nodes: Node[]; edges: Edge[] }) => {
    const node = selectedNodes.find((item) => !(item.data as CardData | undefined)?.invisible);
    setSelectedNodeId(node?.id ?? null);
    setSelectedEdgeId(node ? null : selectedEdges[0]?.id ?? null);
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

  const addCard = useCallback((shape: CardData["shape"] = "rounded", position?: { x: number; y: number }) => {
    const id = `n-${Math.random().toString(36).slice(2, 10)}`;
    const node: Node<CardData> = {
      id,
      type: "card",
      position: position ?? { x: 120, y: 200 + Math.random() * 60 },
      data: {
        title: "Node mới",
        desc: "Chọn node để sửa nội dung, màu sắc và hình dạng.",
        tone: "process",
        shape,
        width: shape === "circle" || shape === "diamond" ? 190 : 210,
        minHeight: shape === "circle" || shape === "diamond" ? 190 : undefined,
      },
    };
    setNodes((current) => [...current, node]);
    setSelectedNodeId(id);
    setSelectedEdgeId(null);
    const doc = docRef.current;
    const yNodes = yNodesRef.current;
    if (doc && yNodes) doc.transact(() => yNodes.set(id, node), LOCAL_ORIGIN);
  }, []);

  const onShapeDragStart = useCallback((event: React.DragEvent, shape: NonNullable<CardData["shape"]>) => {
    event.dataTransfer.setData("application/hbrag-architecture-shape", shape);
    event.dataTransfer.effectAllowed = "move";
  }, []);

  const onDragOver = useCallback((event: React.DragEvent) => {
    event.preventDefault();
    event.dataTransfer.dropEffect = "move";
  }, []);

  const onDrop = useCallback((event: React.DragEvent) => {
    event.preventDefault();
    if (!editing) return;
    const shape = event.dataTransfer.getData("application/hbrag-architecture-shape") as CardData["shape"];
    if (!shape) return;
    addCard(shape, screenToFlowPosition({ x: event.clientX, y: event.clientY }));
  }, [editing, addCard, screenToFlowPosition]);

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

  const updateSelectedNodeData = useCallback((patch: Partial<CardData>) => {
    if (!selectedNode) return;
    commitNode({
      ...selectedNode,
      data: { ...selectedNode.data, ...patch },
    });
  }, [commitNode, selectedNode]);

  const updateSelectedNodeSize = useCallback((patch: { width?: number; minHeight?: number }) => {
    if (!selectedNode) return;
    commitNode({
      ...selectedNode,
      data: { ...selectedNode.data, ...patch },
    });
  }, [commitNode, selectedNode]);

  const deleteSelectedNode = useCallback(() => {
    if (!selectedNode) return;
    setNodes((current) => current.filter((node) => node.id !== selectedNode.id));
    setEdges((current) => current.filter((edge) => edge.source !== selectedNode.id && edge.target !== selectedNode.id));
    const doc = docRef.current;
    const yNodes = yNodesRef.current;
    const yEdges = yEdgesRef.current;
    if (doc && yNodes && yEdges) {
      doc.transact(() => {
        yNodes.delete(selectedNode.id);
        Array.from(yEdges.values()).forEach((edge) => {
          if (edge.source === selectedNode.id || edge.target === selectedNode.id) yEdges.delete(edge.id);
        });
      }, LOCAL_ORIGIN);
    }
    setSelectedNodeId(null);
  }, [selectedNode]);

  const updateSelectedEdge = useCallback((patch: Partial<Edge>) => {
    if (!selectedEdge) return;
    commitEdge({ ...selectedEdge, ...patch });
  }, [commitEdge, selectedEdge]);

  const updateSelectedEdgeColor = useCallback((color: string) => {
    if (!selectedEdge) return;
    const hasMarker = selectedEdge.markerEnd !== undefined;
    commitEdge({
      ...selectedEdge,
      style: { ...(selectedEdge.style ?? {}), stroke: color },
      markerEnd: hasMarker ? { type: MarkerType.ArrowClosed, color } : undefined,
    });
  }, [commitEdge, selectedEdge]);

  const updateSelectedEdgeWidth = useCallback((strokeWidth: number) => {
    if (!selectedEdge) return;
    commitEdge({
      ...selectedEdge,
      style: { ...(selectedEdge.style ?? {}), strokeWidth },
    });
  }, [commitEdge, selectedEdge]);

  const updateSelectedEdgeDashed = useCallback((dashed: boolean) => {
    if (!selectedEdge) return;
    commitEdge({
      ...selectedEdge,
      style: {
        ...(selectedEdge.style ?? {}),
        strokeDasharray: dashed ? "6 4" : undefined,
      },
    });
  }, [commitEdge, selectedEdge]);

  const deleteSelectedEdge = useCallback(() => {
    if (!selectedEdge) return;
    setEdges((current) => current.filter((edge) => edge.id !== selectedEdge.id));
    const doc = docRef.current;
    const yEdges = yEdgesRef.current;
    if (doc && yEdges) doc.transact(() => yEdges.delete(selectedEdge.id), LOCAL_ORIGIN);
    setSelectedEdgeId(null);
  }, [selectedEdge]);

  const statusInfo = {
    connecting: { text: "Đang kết nối…", color: "#d97706" },
    connected: { text: "Đã kết nối", color: "#059669" },
    disconnected: { text: "Mất kết nối", color: "#dc2626" },
  }[status];
  const selectedNodeData = selectedNode?.data;
  const edgeColor = String(selectedEdge?.style?.stroke ?? "#64748b");
  const edgeWidth = Number(selectedEdge?.style?.strokeWidth ?? 2);
  const edgeDashed = Boolean(selectedEdge?.style?.strokeDasharray);

  return (
    <NodeEditContext.Provider value={patchNodeData}>
    <div style={{ position: "fixed", inset: 0 }}>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onConnect={onConnect}
        onNodeDoubleClick={onNodeDoubleClick}
        onPaneMouseMove={onPaneMouseMove}
        onSelectionChange={onSelectionChange}
        onDrop={onDrop}
        onDragOver={onDragOver}
        nodesDraggable={editing}
        nodesConnectable={editing}
        elementsSelectable={editing}
        nodesFocusable={editing}
        edgesFocusable={editing}
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
        {editing ? (
          <>
            <button draggable onDragStart={(event) => onShapeDragStart(event, "rounded")} onClick={() => addCard("rounded")} title="Bấm để thêm, hoặc kéo thả vào canvas" style={{ fontSize: 12.5, fontWeight: 700, border: "1px solid #b8ccff", background: "#eaf1ff", color: "#1d4ed8", borderRadius: 8, padding: "5px 10px", cursor: "grab" }}>
              + Bo góc
            </button>
            <button draggable onDragStart={(event) => onShapeDragStart(event, "square")} onClick={() => addCard("square")} title="Bấm để thêm, hoặc kéo thả vào canvas" style={{ fontSize: 12.5, fontWeight: 700, border: "1px solid #d8e1ee", background: "#fff", color: "#334155", borderRadius: 8, padding: "5px 10px", cursor: "grab" }}>
              □ Vuông
            </button>
            <button draggable onDragStart={(event) => onShapeDragStart(event, "circle")} onClick={() => addCard("circle")} title="Bấm để thêm, hoặc kéo thả vào canvas" style={{ fontSize: 12.5, fontWeight: 700, border: "1px solid #d8e1ee", background: "#fff", color: "#334155", borderRadius: 999, padding: "5px 10px", cursor: "grab" }}>
              ○ Tròn
            </button>
            <button draggable onDragStart={(event) => onShapeDragStart(event, "diamond")} onClick={() => addCard("diamond")} title="Bấm để thêm, hoặc kéo thả vào canvas" style={{ fontSize: 12.5, fontWeight: 700, border: "1px solid #d8e1ee", background: "#fff", color: "#334155", borderRadius: 8, padding: "5px 10px", cursor: "grab" }}>
              ◇ Thoi
            </button>
            <button onClick={restoreDefaultDiagram} style={{ fontSize: 12.5, fontWeight: 700, border: "1px solid #d6c2ff", background: "#f2ebff", color: "#6d28d9", borderRadius: 8, padding: "5px 10px", cursor: "pointer" }}>
              Khôi phục sơ đồ v11
            </button>
            <button onClick={finishEdit} title="Nhả quyền sửa để người khác có thể chỉnh (thay đổi đã tự lưu)" style={{ fontSize: 12.5, fontWeight: 800, border: "1px solid #a7f3d0", background: "#059669", color: "#fff", borderRadius: 8, padding: "5px 12px", cursor: "pointer" }}>
              ✓ Lưu &amp; Xong
            </button>
          </>
        ) : lockedByOther ? (
          <span title={`${lockedByOther.name} đang giữ quyền chỉnh sửa`} style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 12.5, fontWeight: 700, border: "1px solid #fed7aa", background: "#fff7ed", color: "#c2410c", borderRadius: 8, padding: "5px 10px" }}>
            🔒 {lockedByOther.name} đang chỉnh sửa…
          </span>
        ) : (
          <button onClick={requestEdit} title="Giành quyền chỉnh sửa sơ đồ" style={{ fontSize: 12.5, fontWeight: 800, border: "1px solid #1d4ed8", background: "#1d4ed8", color: "#fff", borderRadius: 8, padding: "5px 12px", cursor: "pointer" }}>
            ✏️ Sửa sơ đồ
          </button>
        )}
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

      <aside
        style={{
          position: "fixed",
          top: 78,
          right: 14,
          width: 340,
          maxHeight: "calc(100vh - 110px)",
          overflowY: "auto",
          background: "rgba(255,255,255,.96)",
          border: "1px solid #d8e1ee",
          borderRadius: 14,
          boxShadow: "0 12px 26px rgba(15,23,42,.12)",
          padding: 14,
          fontFamily: "Inter, Segoe UI, Roboto, Arial, sans-serif",
          zIndex: 39,
        }}
      >
        {!editing ? (
          <div style={{ display: "grid", gap: 8, color: "#64748b", fontSize: 12.5, lineHeight: 1.45 }}>
            <strong style={{ color: "#142033", fontSize: 14 }}>Chế độ xem</strong>
            {lockedByOther ? (
              <span style={{ color: "#c2410c", fontWeight: 600 }}>🔒 {lockedByOther.name} đang chỉnh sửa. Bạn sẽ sửa được sau khi họ bấm “Lưu &amp; Xong”.</span>
            ) : (
              <span>Bấm “✏️ Sửa sơ đồ” ở góc trên để giành quyền chỉnh sửa. Mỗi thời điểm chỉ một người được sửa.</span>
            )}
            <span>Bạn vẫn có thể phóng to/thu nhỏ và kéo nền để xem toàn bộ sơ đồ.</span>
          </div>
        ) : selectedNode && selectedNodeData ? (
          <div style={{ display: "grid", gap: 12 }}>
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 10 }}>
              <strong style={{ color: "#142033", fontSize: 14 }}>Chỉnh node</strong>
              <button onClick={deleteSelectedNode} style={{ border: "1px solid #fecaca", background: "#fff0f0", color: "#b91c1c", borderRadius: 8, padding: "5px 8px", fontSize: 12, fontWeight: 700, cursor: "pointer" }}>
                Xóa
              </button>
            </div>

            <label style={{ display: "grid", gap: 5, color: "#334155", fontSize: 12, fontWeight: 700 }}>
              Tiêu đề
              <input value={selectedNodeData.title} onChange={(event) => updateSelectedNodeData({ title: event.target.value })} style={{ border: "1px solid #cbd5e1", borderRadius: 8, padding: "8px 9px", fontSize: 13 }} />
            </label>

            <label style={{ display: "grid", gap: 5, color: "#334155", fontSize: 12, fontWeight: 700 }}>
              Mô tả
              <textarea value={selectedNodeData.desc ?? ""} onChange={(event) => updateSelectedNodeData({ desc: event.target.value })} rows={3} style={{ border: "1px solid #cbd5e1", borderRadius: 8, padding: "8px 9px", fontSize: 13, resize: "vertical" }} />
            </label>

            <label style={{ display: "grid", gap: 5, color: "#334155", fontSize: 12, fontWeight: 700 }}>
              Hình dạng
              <select value={selectedNodeData.shape ?? "rounded"} onChange={(event) => updateSelectedNodeData({ shape: event.target.value as CardData["shape"] })} style={{ border: "1px solid #cbd5e1", borderRadius: 8, padding: "8px 9px", fontSize: 13 }}>
                <option value="rounded">Bo góc</option>
                <option value="square">Vuông</option>
                <option value="circle">Tròn</option>
                <option value="diamond">Hình thoi</option>
              </select>
            </label>

            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
              <label style={{ display: "grid", gap: 5, color: "#334155", fontSize: 12, fontWeight: 700 }}>
                Rộng
                <input type="number" min={80} max={760} value={selectedNodeData.width ?? 210} onChange={(event) => updateSelectedNodeSize({ width: Number(event.target.value) })} style={{ border: "1px solid #cbd5e1", borderRadius: 8, padding: "8px 9px", fontSize: 13 }} />
              </label>
              <label style={{ display: "grid", gap: 5, color: "#334155", fontSize: 12, fontWeight: 700 }}>
                Cao tối thiểu
                <input type="number" min={60} max={760} value={selectedNodeData.minHeight ?? 120} onChange={(event) => updateSelectedNodeSize({ minHeight: Number(event.target.value) })} style={{ border: "1px solid #cbd5e1", borderRadius: 8, padding: "8px 9px", fontSize: 13 }} />
              </label>
            </div>

            <div style={{ display: "grid", gap: 9 }}>
              <strong style={{ color: "#334155", fontSize: 12 }}>Màu nền</strong>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 7 }}>
                {COLOR_SWATCHES.map((color) => (
                  <button key={color} onClick={() => updateSelectedNodeData({ fillColor: color })} title={color} style={{ width: 24, height: 24, borderRadius: 7, border: `2px solid ${selectedNodeData.fillColor === color ? "#0f172a" : "#cbd5e1"}`, background: color, cursor: "pointer" }} />
                ))}
                <input type="color" value={selectedNodeData.fillColor ?? TONE_STYLE[selectedNodeData.tone ?? "process"].bg} onChange={(event) => updateSelectedNodeData({ fillColor: event.target.value })} style={{ width: 32, height: 26, border: "0", background: "transparent" }} />
              </div>
            </div>

            <div style={{ display: "grid", gap: 9 }}>
              <strong style={{ color: "#334155", fontSize: 12 }}>Màu viền</strong>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 7 }}>
                {BORDER_SWATCHES.map((color) => (
                  <button key={color} onClick={() => updateSelectedNodeData({ borderColor: color })} title={color} style={{ width: 24, height: 24, borderRadius: 7, border: `2px solid ${selectedNodeData.borderColor === color ? "#0f172a" : "#cbd5e1"}`, background: color, cursor: "pointer" }} />
                ))}
                <input type="color" value={selectedNodeData.borderColor ?? TONE_STYLE[selectedNodeData.tone ?? "process"].border} onChange={(event) => updateSelectedNodeData({ borderColor: event.target.value })} style={{ width: 32, height: 26, border: "0", background: "transparent" }} />
              </div>
            </div>

            <div style={{ display: "grid", gap: 9 }}>
              <strong style={{ color: "#334155", fontSize: 12 }}>Màu chữ</strong>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 7 }}>
                {TEXT_SWATCHES.map((color) => (
                  <button key={color} onClick={() => updateSelectedNodeData({ textColor: color })} title={color} style={{ width: 24, height: 24, borderRadius: 7, border: `2px solid ${selectedNodeData.textColor === color ? "#0f172a" : "#cbd5e1"}`, background: color, cursor: "pointer" }} />
                ))}
                <input type="color" value={selectedNodeData.textColor ?? "#142033"} onChange={(event) => updateSelectedNodeData({ textColor: event.target.value })} style={{ width: 32, height: 26, border: "0", background: "transparent" }} />
              </div>
            </div>

            <div style={{ display: "grid", gap: 6 }}>
              <strong style={{ color: "#334155", fontSize: 12 }}>Nội dung khối</strong>
              <span style={{ color: "#94a3b8", fontSize: 11, lineHeight: 1.4 }}>
                Thêm/sửa/xoá từng tag, dòng, ô bảng — hoặc bấm thẳng vào node để sửa tại chỗ.
              </span>
              <NodePanelEditors data={selectedNodeData} update={updateSelectedNodeData} />
            </div>
          </div>
        ) : selectedEdge ? (
          <div style={{ display: "grid", gap: 12 }}>
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 10 }}>
              <strong style={{ color: "#142033", fontSize: 14 }}>Chỉnh mũi tên</strong>
              <button onClick={deleteSelectedEdge} style={{ border: "1px solid #fecaca", background: "#fff0f0", color: "#b91c1c", borderRadius: 8, padding: "5px 8px", fontSize: 12, fontWeight: 700, cursor: "pointer" }}>
                Xóa
              </button>
            </div>

            <label style={{ display: "grid", gap: 5, color: "#334155", fontSize: 12, fontWeight: 700 }}>
              Nhãn mũi tên
              <input value={String(selectedEdge.label ?? "")} onChange={(event) => updateSelectedEdge({ label: event.target.value })} style={{ border: "1px solid #cbd5e1", borderRadius: 8, padding: "8px 9px", fontSize: 13 }} />
            </label>

            <label style={{ display: "flex", alignItems: "center", gap: 8, color: "#334155", fontSize: 12, fontWeight: 700 }}>
              <input type="checkbox" checked={Boolean(selectedEdge.animated)} onChange={(event) => updateSelectedEdge({ animated: event.target.checked })} />
              Chạy animation
            </label>

            <label style={{ display: "flex", alignItems: "center", gap: 8, color: "#334155", fontSize: 12, fontWeight: 700 }}>
              <input type="checkbox" checked={edgeDashed} onChange={(event) => updateSelectedEdgeDashed(event.target.checked)} />
              Nét đứt
            </label>

            <label style={{ display: "grid", gap: 5, color: "#334155", fontSize: 12, fontWeight: 700 }}>
              Đầu mũi tên
              <select value={selectedEdge.markerEnd ? "arrow" : "none"} onChange={(event) => updateSelectedEdge({ markerEnd: event.target.value === "arrow" ? { type: MarkerType.ArrowClosed, color: edgeColor } : undefined })} style={{ border: "1px solid #cbd5e1", borderRadius: 8, padding: "8px 9px", fontSize: 13 }}>
                <option value="arrow">Có đầu mũi tên</option>
                <option value="none">Không đầu mũi tên</option>
              </select>
            </label>

            <label style={{ display: "grid", gap: 5, color: "#334155", fontSize: 12, fontWeight: 700 }}>
              Độ dày: {edgeWidth}px
              <input type="range" min={1} max={8} value={edgeWidth} onChange={(event) => updateSelectedEdgeWidth(Number(event.target.value))} />
            </label>

            <div style={{ display: "grid", gap: 9 }}>
              <strong style={{ color: "#334155", fontSize: 12 }}>Màu mũi tên</strong>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 7 }}>
                {EDGE_SWATCHES.map((color) => (
                  <button key={color} onClick={() => updateSelectedEdgeColor(color)} title={color} style={{ width: 24, height: 24, borderRadius: 7, border: `2px solid ${edgeColor === color ? "#0f172a" : "#cbd5e1"}`, background: color, cursor: "pointer" }} />
                ))}
                <input type="color" value={edgeColor} onChange={(event) => updateSelectedEdgeColor(event.target.value)} style={{ width: 32, height: 26, border: "0", background: "transparent" }} />
              </div>
            </div>
          </div>
        ) : (
          <div style={{ display: "grid", gap: 8, color: "#64748b", fontSize: 12.5, lineHeight: 1.45 }}>
            <strong style={{ color: "#142033", fontSize: 14 }}>Bảng chỉnh sửa</strong>
            <span>Chọn một node để sửa chữ, tag, nhóm con, dòng chi tiết, màu sắc và hình dạng.</span>
            <span>Chọn một mũi tên để sửa nhãn, màu, nét đứt, độ dày, animation và đầu mũi tên.</span>
          </div>
        )}
      </aside>

      <div style={{ position: "fixed", bottom: 14, left: 14, fontSize: 11.5, color: "#64748b", background: "rgba(255,255,255,.9)", border: "1px solid #e2e8f0", borderRadius: 10, padding: "6px 10px", fontFamily: "Inter, Segoe UI, Roboto, Arial, sans-serif", zIndex: 40 }}>
        {editing
          ? "Bấm chọn node để sửa chữ/tag/bảng · kéo node để di chuyển · kéo từ chấm bên phải sang node khác để nối. Thay đổi tự lưu & đồng bộ; bấm “Lưu & Xong” để nhả quyền cho người khác."
          : "Chế độ chỉ xem — bấm “✏️ Sửa sơ đồ” ở góc trên để chỉnh (mỗi thời điểm chỉ một người được sửa)."}
      </div>
    </div>
    </NodeEditContext.Provider>
  );
}

export default function CollabFlow() {
  return (
    <ReactFlowProvider>
      <FlowCanvas />
    </ReactFlowProvider>
  );
}
