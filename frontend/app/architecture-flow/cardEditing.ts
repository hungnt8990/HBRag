import type { CardData } from "./initialDiagram";

// Sinh id ngắn cho khối văn bản/bảng (chạy ở trình duyệt nên Math.random ổn).
export function uid(prefix = "b"): string {
  return `${prefix}-${Math.random().toString(36).slice(2, 9)}`;
}

type Patch = Partial<CardData>;

// Mọi thao tác trả về "patch" (mảng đầy đủ đã thay đổi) để cả inline node lẫn
// panel dùng chung một logic -> luôn đồng bộ 2 chiều qua updateNodeData/Yjs.

// ---- tags: string[] ----
export const tagOps = {
  add: (d: CardData): Patch => ({ tags: [...(d.tags ?? []), "tag mới"] }),
  set: (d: CardData, i: number, v: string): Patch => ({
    tags: (d.tags ?? []).map((t, idx) => (idx === i ? v : t)),
  }),
  remove: (d: CardData, i: number): Patch => ({
    tags: (d.tags ?? []).filter((_, idx) => idx !== i),
  }),
};

// ---- sections: { title, items[] }[] (bảng nhóm dạng cột) ----
export const sectionOps = {
  add: (d: CardData): Patch => ({
    sections: [...(d.sections ?? []), { title: "Nhóm mới", items: ["Mục 1"] }],
  }),
  remove: (d: CardData, i: number): Patch => ({
    sections: (d.sections ?? []).filter((_, idx) => idx !== i),
  }),
  setTitle: (d: CardData, i: number, v: string): Patch => ({
    sections: (d.sections ?? []).map((s, idx) => (idx === i ? { ...s, title: v } : s)),
  }),
  addItem: (d: CardData, i: number): Patch => ({
    sections: (d.sections ?? []).map((s, idx) =>
      idx === i ? { ...s, items: [...s.items, "Mục mới"] } : s,
    ),
  }),
  setItem: (d: CardData, i: number, j: number, v: string): Patch => ({
    sections: (d.sections ?? []).map((s, idx) =>
      idx === i ? { ...s, items: s.items.map((it, jdx) => (jdx === j ? v : it)) } : s,
    ),
  }),
  removeItem: (d: CardData, i: number, j: number): Patch => ({
    sections: (d.sections ?? []).map((s, idx) =>
      idx === i ? { ...s, items: s.items.filter((_, jdx) => jdx !== j) } : s,
    ),
  }),
};

// ---- rows: { label, value }[] (bảng 2 cột) ----
export const rowOps = {
  add: (d: CardData): Patch => ({ rows: [...(d.rows ?? []), { label: "Nhãn", value: "Giá trị" }] }),
  remove: (d: CardData, i: number): Patch => ({
    rows: (d.rows ?? []).filter((_, idx) => idx !== i),
  }),
  setLabel: (d: CardData, i: number, v: string): Patch => ({
    rows: (d.rows ?? []).map((r, idx) => (idx === i ? { ...r, label: v } : r)),
  }),
  setValue: (d: CardData, i: number, v: string): Patch => ({
    rows: (d.rows ?? []).map((r, idx) => (idx === i ? { ...r, value: v } : r)),
  }),
};

// ---- notes: { id, text }[] (khối văn bản tự do) ----
export const noteOps = {
  add: (d: CardData): Patch => ({
    notes: [...(d.notes ?? []), { id: uid("note"), text: "Ghi chú mới" }],
  }),
  remove: (d: CardData, i: number): Patch => ({
    notes: (d.notes ?? []).filter((_, idx) => idx !== i),
  }),
  set: (d: CardData, i: number, v: string): Patch => ({
    notes: (d.notes ?? []).map((n, idx) => (idx === i ? { ...n, text: v } : n)),
  }),
};

// ---- tables: { id, title?, columns[], rows[][] }[] (bảng nhiều cột) ----
export const tableOps = {
  add: (d: CardData): Patch => ({
    tables: [
      ...(d.tables ?? []),
      { id: uid("tbl"), title: "Bảng mới", columns: ["Cột 1", "Cột 2"], rows: [["", ""]] },
    ],
  }),
  remove: (d: CardData, i: number): Patch => ({
    tables: (d.tables ?? []).filter((_, idx) => idx !== i),
  }),
  setTitle: (d: CardData, i: number, v: string): Patch => ({
    tables: (d.tables ?? []).map((t, idx) => (idx === i ? { ...t, title: v } : t)),
  }),
  addColumn: (d: CardData, i: number): Patch => ({
    tables: (d.tables ?? []).map((t, idx) =>
      idx === i
        ? {
            ...t,
            columns: [...t.columns, `Cột ${t.columns.length + 1}`],
            rows: t.rows.map((r) => [...r, ""]),
          }
        : t,
    ),
  }),
  removeColumn: (d: CardData, i: number, c: number): Patch => ({
    tables: (d.tables ?? []).map((t, idx) =>
      idx === i
        ? {
            ...t,
            columns: t.columns.filter((_, cc) => cc !== c),
            rows: t.rows.map((r) => r.filter((_, cc) => cc !== c)),
          }
        : t,
    ),
  }),
  setColumn: (d: CardData, i: number, c: number, v: string): Patch => ({
    tables: (d.tables ?? []).map((t, idx) =>
      idx === i ? { ...t, columns: t.columns.map((h, cc) => (cc === c ? v : h)) } : t,
    ),
  }),
  addRow: (d: CardData, i: number): Patch => ({
    tables: (d.tables ?? []).map((t, idx) =>
      idx === i ? { ...t, rows: [...t.rows, t.columns.map(() => "")] } : t,
    ),
  }),
  removeRow: (d: CardData, i: number, r: number): Patch => ({
    tables: (d.tables ?? []).map((t, idx) =>
      idx === i ? { ...t, rows: t.rows.filter((_, rr) => rr !== r) } : t,
    ),
  }),
  setCell: (d: CardData, i: number, r: number, c: number, v: string): Patch => ({
    tables: (d.tables ?? []).map((t, idx) =>
      idx === i
        ? {
            ...t,
            rows: t.rows.map((row, rr) =>
              rr === r ? row.map((cell, cc) => (cc === c ? v : cell)) : row,
            ),
          }
        : t,
    ),
  }),
};
