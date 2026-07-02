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
import { useCallback, useEffect, useMemo, useState } from "react";

import {
  CardData,
  initialEdges,
  initialNodes,
  TONE_STYLE,
} from "./initialDiagram";
import { NodeContent, NodeEditContext, NodePanelEditors } from "./NodeBlocks";

const LOCAL_STORAGE_KEY = "hbrag:architecture-flow:v12";
const COLOR_SWATCHES = ["#eaf1ff", "#e8f7f1", "#fff1e8", "#fff7df", "#f2ebff", "#fff0f0", "#ecfdf5", "#ffffff", "#f8fafc"];
const BORDER_SWATCHES = ["#b8ccff", "#a6ddc7", "#fed0b7", "#f5d28e", "#d6c2ff", "#fecaca", "#a7f3d0", "#94a3b8", "#334155"];
const TEXT_SWATCHES = ["#142033", "#1d4ed8", "#047857", "#c2410c", "#6d28d9", "#b91c1c", "#334155", "#ffffff"];
const EDGE_SWATCHES = ["#64748b", "#1d4ed8", "#047857", "#c2410c", "#6d28d9", "#b91c1c", "#0f172a"];

type DiagramSnapshot = { nodes: Node[]; edges: Edge[] };

// Bỏ trạng thái UI phù du trước khi lưu cục bộ.
function stripEphemeral(node: Node): Node {
  const clone = { ...node } as Record<string, unknown>;
  delete clone.selected;
  delete clone.dragging;
  delete clone.measured;
  delete clone.resizing;
  return clone as Node;
}

function stripEphemeralEdge(edge: Edge): Edge {
  const clone = { ...edge } as Record<string, unknown>;
  delete clone.selected;
  return clone as Edge;
}

function defaultDiagram(): DiagramSnapshot {
  return { nodes: initialNodes, edges: initialEdges };
}

function loadLocalDiagram(): DiagramSnapshot {
  if (typeof window === "undefined") return defaultDiagram();
  try {
    const raw = window.localStorage.getItem(LOCAL_STORAGE_KEY);
    if (!raw) return defaultDiagram();
    const parsed = JSON.parse(raw) as Partial<DiagramSnapshot>;
    if (!Array.isArray(parsed.nodes) || !Array.isArray(parsed.edges)) return defaultDiagram();
    return {
      nodes: parsed.nodes.map(stripEphemeral),
      edges: parsed.edges.map(stripEphemeralEdge),
    };
  } catch {
    return defaultDiagram();
  }
}

function saveLocalDiagram(nodes: Node[], edges: Edge[]) {
  try {
    window.localStorage.setItem(
      LOCAL_STORAGE_KEY,
      JSON.stringify({
        nodes: nodes.map(stripEphemeral),
        edges: edges.map(stripEphemeralEdge),
      }),
    );
  } catch {
    // Không chặn editor nếu browser chặn localStorage/quota đầy.
  }
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
  const [initialDiagram] = useState<DiagramSnapshot>(() => loadLocalDiagram());
  const [nodes, setNodes] = useState<Node[]>(initialDiagram.nodes);
  const [edges, setEdges] = useState<Edge[]>(initialDiagram.edges);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [selectedEdgeId, setSelectedEdgeId] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);

  const commitNode = useCallback((node: Node) => {
    setNodes((current) => current.map((item) => (item.id === node.id ? node : item)));
  }, []);

  // Patch data của 1 node theo id (dùng cho inline-edit trong node).
  const patchNodeData = useCallback((nodeId: string, patch: Partial<CardData>) => {
    setNodes((current) =>
      current.map((item) => (item.id === nodeId ? { ...item, data: { ...(item.data as CardData), ...patch } } : item)),
    );
  }, []);

  const commitEdge = useCallback((edge: Edge) => {
    setEdges((current) => current.map((item) => (item.id === edge.id ? edge : item)));
  }, []);

  const selectedNode = useMemo(
    () => nodes.find((node) => node.id === selectedNodeId) as Node<CardData> | undefined,
    [nodes, selectedNodeId],
  );
  const selectedEdge = useMemo(
    () => edges.find((edge) => edge.id === selectedEdgeId),
    [edges, selectedEdgeId],
  );

  useEffect(() => {
    saveLocalDiagram(nodes, edges);
  }, [edges, nodes]);

  const requestEdit = useCallback(() => {
    setEditing(true);
  }, []);

  const finishEdit = useCallback(() => {
    setEditing(false);
    setSelectedNodeId(null);
    setSelectedEdgeId(null);
  }, []);

  const onNodesChange = useCallback((changes: NodeChange[]) => {
    setNodes((nds) => applyNodeChanges(changes, nds));
    if (changes.some((ch) => ch.type === "remove" && ch.id === selectedNodeId)) {
      setSelectedNodeId(null);
    }
  }, [selectedNodeId]);

  const onEdgesChange = useCallback((changes: EdgeChange[]) => {
    setEdges((eds) => applyEdgeChanges(changes, eds));
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
    setEdges((eds) => addEdge(edge, eds));
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
    if (!window.confirm("Khôi phục sơ đồ kiến trúc v11? Các chỉnh sửa cục bộ trong trình duyệt này sẽ được thay bằng bản mặc định.")) {
      return;
    }
    setNodes(initialNodes);
    setEdges(initialEdges);
    setSelectedNodeId(null);
    setSelectedEdgeId(null);
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
    setSelectedEdgeId(null);
  }, [selectedEdge]);

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
        <strong style={{ fontSize: 14, color: "#142033" }}>Kiến trúc RAG văn bản v11</strong>
        <span style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 12, color: "#334155", fontWeight: 700 }}>
          <span style={{ width: 9, height: 9, borderRadius: "50%", background: "#64748b", display: "inline-block" }} />
          Cục bộ
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
            <button onClick={finishEdit} title="Lưu trong trình duyệt này và quay về chế độ xem" style={{ fontSize: 12.5, fontWeight: 800, border: "1px solid #a7f3d0", background: "#059669", color: "#fff", borderRadius: 8, padding: "5px 12px", cursor: "pointer" }}>
              ✓ Lưu &amp; Xong
            </button>
          </>
        ) : (
          <button onClick={requestEdit} title="Chỉnh sửa sơ đồ trong trình duyệt này" style={{ fontSize: 12.5, fontWeight: 800, border: "1px solid #1d4ed8", background: "#1d4ed8", color: "#fff", borderRadius: 8, padding: "5px 12px", cursor: "pointer" }}>
            ✏️ Sửa sơ đồ
          </button>
        )}
        <button onClick={() => navigator.clipboard?.writeText(window.location.href)} style={{ fontSize: 12.5, fontWeight: 700, border: "1px solid #d8e1ee", background: "#fff", color: "#334155", borderRadius: 8, padding: "5px 10px", cursor: "pointer" }}>
          Sao chép link chia sẻ
        </button>
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
            <span>Bấm “✏️ Sửa sơ đồ” ở góc trên để chỉnh trong trình duyệt này. Không còn đồng bộ realtime hay khóa người sửa.</span>
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
          ? "Bấm chọn node để sửa chữ/tag/bảng · kéo node để di chuyển · kéo từ chấm bên phải sang node khác để nối. Thay đổi tự lưu trong trình duyệt này; bấm “Lưu & Xong” để quay về chế độ xem."
          : "Chế độ chỉ xem — bấm “✏️ Sửa sơ đồ” ở góc trên để chỉnh cục bộ trong trình duyệt này."}
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
