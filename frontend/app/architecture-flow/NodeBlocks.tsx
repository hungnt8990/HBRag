"use client";

import { createContext, useCallback, useContext, useEffect, useState } from "react";
import type { CardData } from "./initialDiagram";
import { noteOps, rowOps, sectionOps, tableOps, tagOps } from "./cardEditing";

// Hàm patch node theo id (commit qua Yjs) được cung cấp từ FlowCanvas.
export const NodeEditContext = createContext<((id: string, patch: Partial<CardData>) => void) | null>(null);

type BlockProps = { data: CardData; editable: boolean; update: (patch: Partial<CardData>) => void };

// -------------------------------------------------------------- helpers UI --
function stop(e: React.MouseEvent) {
  e.stopPropagation();
}

function IconBtn({
  onClick,
  title,
  tone = "danger",
  children,
}: {
  onClick: () => void;
  title: string;
  tone?: "danger" | "add";
  children: React.ReactNode;
}) {
  const palette =
    tone === "danger"
      ? { border: "1px solid #fecaca", background: "#fff5f5", color: "#dc2626" }
      : { border: "1px dashed #93c5fd", background: "#eff6ff", color: "#1d4ed8" };
  return (
    <button
      className="nodrag nopan"
      title={title}
      onMouseDown={stop}
      onClick={(e) => {
        stop(e);
        onClick();
      }}
      style={{
        ...palette,
        width: 16,
        height: 16,
        lineHeight: "13px",
        textAlign: "center",
        borderRadius: 999,
        fontSize: 11,
        fontWeight: 800,
        padding: 0,
        cursor: "pointer",
        flex: "0 0 auto",
      }}
    >
      {children}
    </button>
  );
}

export function AddBtn({ onClick, children }: { onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      className="nodrag nopan"
      onMouseDown={stop}
      onClick={(e) => {
        stop(e);
        onClick();
      }}
      style={{
        fontSize: 10.6,
        fontWeight: 700,
        border: "1px dashed #93c5fd",
        background: "rgba(239,246,255,.92)",
        color: "#1d4ed8",
        borderRadius: 7,
        padding: "2px 8px",
        cursor: "pointer",
        whiteSpace: "nowrap",
      }}
    >
      {children}
    </button>
  );
}

// Text click-để-sửa. Read: span; Edit: input/textarea, commit khi blur/Enter, huỷ khi Esc.
export function EditableText({
  value,
  onCommit,
  editable,
  placeholder,
  multiline = false,
  block = false,
  style,
}: {
  value: string;
  onCommit: (v: string) => void;
  editable: boolean;
  placeholder?: string;
  multiline?: boolean;
  block?: boolean;
  style?: React.CSSProperties;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(value);
  useEffect(() => {
    if (!editing) setDraft(value);
  }, [value, editing]);

  if (!editable) {
    return <span style={style}>{value || placeholder || ""}</span>;
  }

  if (editing) {
    const commit = () => {
      setEditing(false);
      if (draft !== value) onCommit(draft);
    };
    const cancel = () => {
      setDraft(value);
      setEditing(false);
    };
    const shared = {
      autoFocus: true,
      value: draft,
      className: "nodrag nopan",
      onChange: (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => setDraft(e.target.value),
      onBlur: commit,
      onMouseDown: stop,
      onClick: stop,
      style: {
        ...style,
        background: "#fff",
        border: "1px solid #93c5fd",
        borderRadius: 6,
        outline: "none",
        padding: "1px 5px",
        boxSizing: "border-box" as const,
        font: "inherit",
        width: multiline || block ? "100%" : `${Math.max(4, draft.length + 1)}ch`,
        resize: "none" as const,
      },
    };
    return multiline ? (
      <textarea
        {...shared}
        rows={2}
        onKeyDown={(e) => {
          if (e.key === "Escape") cancel();
        }}
      />
    ) : (
      <input
        {...shared}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            e.preventDefault();
            commit();
          }
          if (e.key === "Escape") cancel();
        }}
      />
    );
  }

  return (
    <span
      className="nodrag"
      title="Bấm để sửa"
      onClick={(e) => {
        stop(e);
        setEditing(true);
      }}
      style={{ ...style, cursor: "text", borderBottom: "1px dashed rgba(37,99,235,.45)" }}
    >
      {value || <span style={{ opacity: 0.45 }}>{placeholder || "..."}</span>}
    </span>
  );
}

// ----------------------------------------------------------------- blocks --
export function TagsBlock({ data, editable, update }: BlockProps) {
  const tags = data.tags ?? [];
  if (!tags.length && !editable) return null;
  if (!tags.length) return null; // rỗng: dùng thanh "+ Thêm khối" để tạo
  const centered = data.shape === "circle" || data.shape === "diamond";
  return (
    <div
      style={{
        display: "flex",
        flexWrap: "wrap",
        justifyContent: centered ? "center" : "flex-start",
        gap: 5,
        marginTop: 8,
      }}
    >
      {tags.map((tag, i) => (
        <span
          key={i}
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: 4,
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
          <EditableText value={tag} editable={editable} onCommit={(v) => update(tagOps.set(data, i, v))} />
          {editable ? (
            <IconBtn title="Xoá tag" onClick={() => update(tagOps.remove(data, i))}>
              ×
            </IconBtn>
          ) : null}
        </span>
      ))}
      {editable ? <AddBtn onClick={() => update(tagOps.add(data))}>+ tag</AddBtn> : null}
    </div>
  );
}

export function SectionsBlock({ data, editable, update }: BlockProps) {
  const sections = data.sections ?? [];
  if (!sections.length) return null;
  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: sections.length > 1 ? "1fr 1fr" : "1fr",
        gap: 8,
        marginTop: 10,
      }}
    >
      {sections.map((section, i) => (
        <div
          key={i}
          style={{
            background: "rgba(255,255,255,.7)",
            border: "1px solid rgba(148,163,184,.42)",
            borderRadius: 12,
            padding: "8px 9px",
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 5 }}>
            <EditableText
              value={section.title}
              editable={editable}
              block
              onCommit={(v) => update(sectionOps.setTitle(data, i, v))}
              style={{ color: "#1e293b", fontSize: 11.5, fontWeight: 800 }}
            />
            {editable ? (
              <IconBtn title="Xoá nhóm" onClick={() => update(sectionOps.remove(data, i))}>
                ×
              </IconBtn>
            ) : null}
          </div>
          <ul style={{ margin: "0 0 0 15px", padding: 0, color: "#334155", fontSize: 10.8, lineHeight: 1.35 }}>
            {section.items.map((item, j) => (
              <li key={j} style={{ margin: "2px 0", display: "flex", alignItems: "center", gap: 5 }}>
                <EditableText
                  value={item}
                  editable={editable}
                  block
                  onCommit={(v) => update(sectionOps.setItem(data, i, j, v))}
                />
                {editable ? (
                  <IconBtn title="Xoá mục" onClick={() => update(sectionOps.removeItem(data, i, j))}>
                    ×
                  </IconBtn>
                ) : null}
              </li>
            ))}
          </ul>
          {editable ? (
            <div style={{ marginTop: 5 }}>
              <AddBtn onClick={() => update(sectionOps.addItem(data, i))}>+ mục</AddBtn>
            </div>
          ) : null}
        </div>
      ))}
    </div>
  );
}

export function RowsBlock({ data, editable, update }: BlockProps) {
  const rows = data.rows ?? [];
  if (!rows.length) return null;
  return (
    <div style={{ display: "grid", gap: 7, marginTop: 10 }}>
      {rows.map((row, i) => (
        <div
          key={i}
          style={{
            display: "grid",
            gridTemplateColumns: editable ? "92px 1fr 16px" : "92px 1fr",
            gap: 8,
            alignItems: "start",
            borderTop: "1px solid rgba(148,163,184,.3)",
            paddingTop: 7,
          }}
        >
          <EditableText
            value={row.label}
            editable={editable}
            block
            onCommit={(v) => update(rowOps.setLabel(data, i, v))}
            style={{ color: "#0f172a", fontSize: 10.8, fontWeight: 800 }}
          />
          <EditableText
            value={row.value}
            editable={editable}
            multiline
            onCommit={(v) => update(rowOps.setValue(data, i, v))}
            style={{ color: "#334155", fontSize: 10.8, lineHeight: 1.35 }}
          />
          {editable ? (
            <IconBtn title="Xoá dòng" onClick={() => update(rowOps.remove(data, i))}>
              ×
            </IconBtn>
          ) : null}
        </div>
      ))}
      {editable ? (
        <div style={{ marginTop: 3 }}>
          <AddBtn onClick={() => update(rowOps.add(data))}>+ dòng</AddBtn>
        </div>
      ) : null}
    </div>
  );
}

export function NotesBlock({ data, editable, update }: BlockProps) {
  const notes = data.notes ?? [];
  if (!notes.length) return null;
  return (
    <div style={{ display: "grid", gap: 6, marginTop: 10 }}>
      {notes.map((note, i) => (
        <div key={note.id} style={{ display: "flex", alignItems: "start", gap: 5 }}>
          <EditableText
            value={note.text}
            editable={editable}
            multiline
            block
            onCommit={(v) => update(noteOps.set(data, i, v))}
            style={{ color: "#334155", fontSize: 11, lineHeight: 1.4, flex: 1 }}
          />
          {editable ? (
            <IconBtn title="Xoá khối văn bản" onClick={() => update(noteOps.remove(data, i))}>
              ×
            </IconBtn>
          ) : null}
        </div>
      ))}
    </div>
  );
}

export function TablesBlock({ data, editable, update }: BlockProps) {
  const tables = data.tables ?? [];
  if (!tables.length) return null;
  const cellBorder = "1px solid rgba(148,163,184,.4)";
  return (
    <div style={{ display: "grid", gap: 10, marginTop: 10 }}>
      {tables.map((table, i) => (
        <div key={table.id}>
          <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 4 }}>
            <EditableText
              value={table.title ?? ""}
              editable={editable}
              block
              placeholder="Tiêu đề bảng"
              onCommit={(v) => update(tableOps.setTitle(data, i, v))}
              style={{ color: "#1e293b", fontSize: 11.3, fontWeight: 800 }}
            />
            {editable ? (
              <IconBtn title="Xoá bảng" onClick={() => update(tableOps.remove(data, i))}>
                ×
              </IconBtn>
            ) : null}
          </div>
          <table style={{ borderCollapse: "collapse", width: "100%", fontSize: 10.6, tableLayout: "fixed" }}>
            <thead>
              <tr>
                {table.columns.map((col, c) => (
                  <th
                    key={c}
                    style={{
                      border: cellBorder,
                      background: "rgba(255,255,255,.72)",
                      color: "#1e293b",
                      fontWeight: 800,
                      textAlign: "left",
                      padding: "3px 6px",
                      verticalAlign: "top",
                    }}
                  >
                    <div style={{ display: "flex", alignItems: "center", gap: 3 }}>
                      <EditableText
                        value={col}
                        editable={editable}
                        block
                        onCommit={(v) => update(tableOps.setColumn(data, i, c, v))}
                      />
                      {editable && table.columns.length > 1 ? (
                        <IconBtn title="Xoá cột" onClick={() => update(tableOps.removeColumn(data, i, c))}>
                          ×
                        </IconBtn>
                      ) : null}
                    </div>
                  </th>
                ))}
                {editable ? (
                  <th style={{ border: cellBorder, padding: "2px 4px", width: 24, textAlign: "center" }}>
                    <IconBtn tone="add" title="Thêm cột" onClick={() => update(tableOps.addColumn(data, i))}>
                      +
                    </IconBtn>
                  </th>
                ) : null}
              </tr>
            </thead>
            <tbody>
              {table.rows.map((row, r) => (
                <tr key={r}>
                  {row.map((cell, c) => (
                    <td
                      key={c}
                      style={{ border: cellBorder, color: "#334155", padding: "3px 6px", verticalAlign: "top" }}
                    >
                      <EditableText
                        value={cell}
                        editable={editable}
                        block
                        onCommit={(v) => update(tableOps.setCell(data, i, r, c, v))}
                      />
                    </td>
                  ))}
                  {editable ? (
                    <td style={{ border: cellBorder, padding: "2px 4px", textAlign: "center" }}>
                      <IconBtn title="Xoá hàng" onClick={() => update(tableOps.removeRow(data, i, r))}>
                        ×
                      </IconBtn>
                    </td>
                  ) : null}
                </tr>
              ))}
            </tbody>
          </table>
          {editable ? (
            <div style={{ marginTop: 4 }}>
              <AddBtn onClick={() => update(tableOps.addRow(data, i))}>+ hàng</AddBtn>
            </div>
          ) : null}
        </div>
      ))}
    </div>
  );
}

// Thanh thêm khối mới (hiện khi node đang chọn).
function AddBlockToolbar({ data, update }: { data: CardData; update: (patch: Partial<CardData>) => void }) {
  return (
    <div
      style={{
        display: "flex",
        flexWrap: "wrap",
        gap: 5,
        marginTop: 10,
        paddingTop: 8,
        borderTop: "1px dashed rgba(148,163,184,.5)",
      }}
    >
      <AddBtn onClick={() => update(tagOps.add(data))}>+ Tag</AddBtn>
      <AddBtn onClick={() => update(sectionOps.add(data))}>+ Nhóm</AddBtn>
      <AddBtn onClick={() => update(rowOps.add(data))}>+ Dòng</AddBtn>
      <AddBtn onClick={() => update(noteOps.add(data))}>+ Văn bản</AddBtn>
      <AddBtn onClick={() => update(tableOps.add(data))}>+ Bảng</AddBtn>
    </div>
  );
}

// Nội dung trong node: title + desc + các khối; inline-edit khi selected.
export function NodeContent({ id, data, selected }: { id: string; data: CardData; selected: boolean }) {
  const patch = useContext(NodeEditContext);
  const editable = selected && !!patch;
  const update = useCallback((p: Partial<CardData>) => patch?.(id, p), [patch, id]);
  const textColor = data.textColor ?? "#142033";

  return (
    <>
      <EditableText
        value={data.title}
        editable={editable}
        block
        placeholder="Tiêu đề"
        onCommit={(v) => update({ title: v })}
        style={{ display: "block", fontSize: 14, fontWeight: 700, color: textColor, lineHeight: 1.25 }}
      />
      {data.desc || editable ? (
        <div style={{ marginTop: 5 }}>
          <EditableText
            value={data.desc ?? ""}
            editable={editable}
            multiline
            block
            placeholder="Mô tả (bấm để thêm)"
            onCommit={(v) => update({ desc: v })}
            style={{ display: "block", fontSize: 11.6, color: textColor, opacity: 0.78, lineHeight: 1.4 }}
          />
        </div>
      ) : null}
      <TagsBlock data={data} editable={editable} update={update} />
      <SectionsBlock data={data} editable={editable} update={update} />
      <RowsBlock data={data} editable={editable} update={update} />
      <NotesBlock data={data} editable={editable} update={update} />
      <TablesBlock data={data} editable={editable} update={update} />
      {editable ? <AddBlockToolbar data={data} update={update} /> : null}
    </>
  );
}

// Form động cho panel bên phải (dùng lại cùng block, editable=true).
export function NodePanelEditors({
  data,
  update,
}: {
  data: CardData;
  update: (patch: Partial<CardData>) => void;
}) {
  const groupStyle: React.CSSProperties = {
    display: "grid",
    gap: 6,
    border: "1px solid #e2e8f0",
    borderRadius: 10,
    padding: "9px 10px",
    background: "#f8fafc",
  };
  const labelStyle: React.CSSProperties = {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    color: "#334155",
    fontSize: 12,
    fontWeight: 800,
  };
  return (
    <div style={{ display: "grid", gap: 10 }}>
      <div style={groupStyle}>
        <div style={labelStyle}>
          <span>Tag</span>
          <AddBtn onClick={() => update(tagOps.add(data))}>+ Thêm</AddBtn>
        </div>
        <TagsBlock data={data} editable update={update} />
      </div>
      <div style={groupStyle}>
        <div style={labelStyle}>
          <span>Bảng nhóm</span>
          <AddBtn onClick={() => update(sectionOps.add(data))}>+ Thêm</AddBtn>
        </div>
        <SectionsBlock data={data} editable update={update} />
      </div>
      <div style={groupStyle}>
        <div style={labelStyle}>
          <span>Bảng label / value</span>
          <AddBtn onClick={() => update(rowOps.add(data))}>+ Thêm</AddBtn>
        </div>
        <RowsBlock data={data} editable update={update} />
      </div>
      <div style={groupStyle}>
        <div style={labelStyle}>
          <span>Khối văn bản</span>
          <AddBtn onClick={() => update(noteOps.add(data))}>+ Thêm</AddBtn>
        </div>
        <NotesBlock data={data} editable update={update} />
      </div>
      <div style={groupStyle}>
        <div style={labelStyle}>
          <span>Bảng nhiều cột</span>
          <AddBtn onClick={() => update(tableOps.add(data))}>+ Thêm</AddBtn>
        </div>
        <TablesBlock data={data} editable update={update} />
      </div>
    </div>
  );
}
