"use client";

import { Loader2, RefreshCw, Search, Upload } from "lucide-react";
import type { ChangeEvent } from "react";

import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import type { DocumentDetailResponse, DocumentListItem } from "@/lib/api";
import { cn } from "@/lib/utils";

type PipelineAction = "parse" | "chunk" | "index" | "graph";

export function DocumentLibraryPanel({
  documents,
  graphEnabled,
  isLoading,
  search,
  selectedDocument,
  selectedDocumentId,
  statusFilter,
  uploadFiles,
  uploading,
  onRefresh,
  onRunAction,
  onSearchChange,
  onSelectDocument,
  onStatusFilterChange,
  onUpload,
  onUploadFilesChange,
}: {
  documents: DocumentListItem[];
  graphEnabled: boolean;
  isLoading: boolean;
  search: string;
  selectedDocument: DocumentDetailResponse | null;
  selectedDocumentId: string | null;
  statusFilter: string;
  uploadFiles: File[];
  uploading: boolean;
  onRefresh: () => void;
  onRunAction: (action: PipelineAction, documentId: string) => void;
  onSearchChange: (value: string) => void;
  onSelectDocument: (documentId: string) => void;
  onStatusFilterChange: (value: string) => void;
  onUpload: () => void;
  onUploadFilesChange: (event: ChangeEvent<HTMLInputElement>) => void;
}) {
  return (
    <div className="grid gap-5 xl:grid-cols-[minmax(0,1.3fr)_minmax(360px,0.9fr)]">
      <Card className="bg-white shadow-sm">
        <CardHeader className="pb-4">
          <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
            <div>
              <CardTitle>Document Library</CardTitle>
              <CardDescription>
                Upload nhiều tài liệu, giữ trạng thái pipeline, và chọn tài liệu làm workspace.
              </CardDescription>
            </div>
            <Button
              className="border-slate-200 bg-white text-slate-700 hover:bg-slate-50"
              onClick={onRefresh}
              type="button"
              variant="outline"
            >
              <RefreshCw className="h-4 w-4" />
              Refresh
            </Button>
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="rounded-2xl border border-slate-200 bg-slate-50 p-4">
            <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_220px_auto]">
              <label className="block">
                <span className="text-xs font-semibold uppercase tracking-wider text-slate-500">
                  Upload files
                </span>
                <input
                  className="mt-2 block w-full cursor-pointer rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700 file:mr-3 file:rounded-lg file:border-0 file:bg-slate-100 file:px-3 file:py-1.5 file:text-sm file:font-medium file:text-slate-700"
                  multiple
                  onChange={onUploadFilesChange}
                  type="file"
                />
              </label>
              <label className="block">
                <span className="text-xs font-semibold uppercase tracking-wider text-slate-500">
                  Status filter
                </span>
                <select
                  className="mt-2 h-10 w-full rounded-xl border border-slate-200 bg-white px-3 text-sm text-slate-700"
                  onChange={(event) => onStatusFilterChange(event.target.value)}
                  value={statusFilter}
                >
                  <option value="all">All statuses</option>
                  <option value="uploaded">uploaded</option>
                  <option value="parsed">parsed</option>
                  <option value="chunked">chunked</option>
                  <option value="indexed">indexed</option>
                  <option value="failed">failed</option>
                </select>
              </label>
              <div className="flex items-end">
                <Button
                  className="w-full bg-[#0d3b4c] text-white hover:bg-[#114e63]"
                  disabled={uploadFiles.length === 0 || uploading}
                  onClick={onUpload}
                  type="button"
                >
                  {uploading ? (
                    <Loader2 className="h-4 w-4 animate-spin" />
                  ) : (
                    <Upload className="h-4 w-4" />
                  )}
                  {uploadFiles.length > 1 ? "Upload batch" : "Upload"}
                </Button>
              </div>
            </div>
            <div className="mt-3 flex flex-wrap gap-2">
              {uploadFiles.length === 0 ? (
                <span className="text-xs text-slate-500">No files selected.</span>
              ) : (
                uploadFiles.map((file) => (
                  <span
                    className="rounded-full border border-slate-200 bg-white px-2.5 py-1 text-xs text-slate-600"
                    key={`${file.name}-${file.size}`}
                  >
                    {file.name}
                  </span>
                ))
              )}
            </div>
          </div>

          <label className="block">
            <span className="text-xs font-semibold uppercase tracking-wider text-slate-500">
              Search
            </span>
            <div className="relative mt-2">
              <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
              <Input
                className="border-slate-200 bg-white pl-9"
                onChange={(event) => onSearchChange(event.target.value)}
                placeholder="Search by title or file name"
                value={search}
              />
            </div>
          </label>

          <div className="space-y-3">
            {isLoading ? (
              <div className="rounded-xl border border-dashed border-slate-200 bg-slate-50 px-4 py-10 text-center text-sm text-slate-500">
                <Loader2 className="mx-auto mb-3 h-4 w-4 animate-spin text-cyan-700" />
                Loading documents...
              </div>
            ) : documents.length === 0 ? (
              <div className="rounded-xl border border-dashed border-slate-200 bg-slate-50 px-4 py-10 text-center text-sm text-slate-500">
                No uploaded documents found.
              </div>
            ) : (
              documents.map((document) => (
                <article
                  className={cn(
                    "rounded-2xl border p-4 transition-colors",
                    document.document_id === selectedDocumentId
                      ? "border-cyan-300 bg-cyan-50/60"
                      : "border-slate-200 bg-white",
                  )}
                  key={document.document_id}
                >
                  <div className="flex flex-col gap-3 xl:flex-row xl:items-start xl:justify-between">
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-2">
                        <h3 className="truncate text-sm font-semibold text-slate-900">
                          {document.title}
                        </h3>
                        <StatusPill status={document.status} />
                        {document.graph_indexed ? (
                          <span className="rounded-full bg-emerald-100 px-2 py-0.5 text-[11px] font-semibold text-emerald-700">
                            graph indexed
                          </span>
                        ) : null}
                      </div>
                      <p className="mt-1 text-xs text-slate-500">
                        {document.filename ?? "No file name"} ·{" "}
                        {document.organization?.ten_dviqly ?? "No organization"} ·{" "}
                        {document.uploaded_by?.full_name ??
                          document.uploaded_by?.username ??
                          "Unknown uploader"}
                      </p>
                    </div>
                    <div className="flex flex-wrap gap-2">
                      <Button
                        className="border-slate-200 bg-white text-slate-700 hover:bg-slate-50"
                        onClick={() => onSelectDocument(document.document_id)}
                        type="button"
                        variant="outline"
                      >
                        Select
                      </Button>
                      <SmallActionButton
                        disabled={document.status !== "uploaded"}
                        label="Parse"
                        onClick={() => onRunAction("parse", document.document_id)}
                      />
                      <SmallActionButton
                        disabled={!["parsed", "chunked"].includes(document.status)}
                        label="Chunk"
                        onClick={() => onRunAction("chunk", document.document_id)}
                      />
                      <SmallActionButton
                        disabled={!["chunked", "indexed"].includes(document.status)}
                        label="Index Vector"
                        onClick={() => onRunAction("index", document.document_id)}
                      />
                      <SmallActionButton
                        disabled={!graphEnabled || document.status !== "indexed"}
                        label="Graph Index"
                        onClick={() => onRunAction("graph", document.document_id)}
                      />
                    </div>
                  </div>

                  <dl className="mt-4 grid gap-2 text-xs text-slate-600 sm:grid-cols-3 lg:grid-cols-6">
                    <Metric label="Parsed chars" value={document.parsed_character_count.toLocaleString()} />
                    <Metric label="Chunks" value={document.chunk_count.toLocaleString()} />
                    <Metric
                      label="Vector indexed"
                      value={
                        document.vector_indexed_count === null
                          ? "--"
                          : document.vector_indexed_count.toLocaleString()
                      }
                    />
                    <Metric label="Pipeline logs" value={document.pipeline_logs_count.toLocaleString()} />
                    <Metric label="Visibility" value={document.visibility} />
                    <Metric label="Updated" value={formatDateTime(document.updated_at)} />
                  </dl>
                </article>
              ))
            )}
          </div>
        </CardContent>
      </Card>

      <Card className="bg-white shadow-sm">
        <CardHeader>
          <CardTitle>Selected Document</CardTitle>
          <CardDescription>
            Detail, preview, pipeline logs, và graph status cho workspace hiện tại.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {!selectedDocument ? (
            <div className="rounded-xl border border-dashed border-slate-200 bg-slate-50 px-4 py-10 text-center text-sm text-slate-500">
              Select a document to inspect detail.
            </div>
          ) : (
            <>
              <div className="rounded-2xl border border-slate-200 bg-slate-50 p-4">
                <div className="flex flex-wrap items-center gap-2">
                  <h3 className="text-sm font-semibold text-slate-900">
                    {selectedDocument.title}
                  </h3>
                  <StatusPill status={selectedDocument.status} />
                </div>
                <dl className="mt-4 grid gap-2 text-xs text-slate-600 sm:grid-cols-2">
                  <Metric label="File" value={selectedDocument.filename ?? "--"} />
                  <Metric label="Organization" value={selectedDocument.organization?.ten_dviqly ?? "--"} />
                  <Metric label="Parsed chars" value={selectedDocument.parsed_character_count.toLocaleString()} />
                  <Metric label="Chunks" value={selectedDocument.chunk_count.toLocaleString()} />
                  <Metric
                    label="Vector indexed"
                    value={
                      selectedDocument.vector_indexed_count === null
                        ? "--"
                        : selectedDocument.vector_indexed_count.toLocaleString()
                    }
                  />
                  <Metric
                    label="Graph"
                    value={selectedDocument.graph_status?.graph_indexed ? "indexed" : "not indexed"}
                  />
                </dl>
              </div>

              <section>
                <h4 className="mb-2 text-xs font-semibold uppercase tracking-wider text-slate-500">
                  Preview
                </h4>
                <div className="max-h-48 overflow-auto rounded-xl bg-slate-950 p-4 text-sm leading-6 text-slate-100">
                  {selectedDocument.preview_text || "No parsed preview available."}
                </div>
              </section>

              <section>
                <h4 className="mb-2 text-xs font-semibold uppercase tracking-wider text-slate-500">
                  Pipeline logs
                </h4>
                <div className="max-h-64 space-y-2 overflow-auto">
                  {selectedDocument.pipeline_logs.length === 0 ? (
                    <div className="rounded-xl border border-dashed border-slate-200 bg-slate-50 px-4 py-6 text-center text-sm text-slate-500">
                      No pipeline logs.
                    </div>
                  ) : (
                    selectedDocument.pipeline_logs.map((log) => (
                      <article
                        className="rounded-xl border border-slate-200 bg-white p-3"
                        key={`${log.action}-${log.created_at}`}
                      >
                        <div className="flex items-center justify-between gap-3">
                          <div className="flex items-center gap-2">
                            <span className="text-sm font-semibold text-slate-800">{log.action}</span>
                            <StatusPill status={log.status} />
                          </div>
                          <span className="text-xs text-slate-500">
                            {formatDateTime(log.created_at)}
                          </span>
                        </div>
                        {log.message ? (
                          <p className="mt-2 text-sm text-slate-600">{log.message}</p>
                        ) : null}
                      </article>
                    ))
                  )}
                </div>
              </section>
            </>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function SmallActionButton({
  disabled,
  label,
  onClick,
}: {
  disabled: boolean;
  label: string;
  onClick: () => void;
}) {
  return (
    <Button
      className="border-slate-200 bg-white text-slate-700 hover:bg-slate-50"
      disabled={disabled}
      onClick={onClick}
      type="button"
      variant="outline"
    >
      {label}
    </Button>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="font-semibold uppercase tracking-wider text-slate-400">{label}</dt>
      <dd className="mt-1 text-sm text-slate-700">{value}</dd>
    </div>
  );
}

function StatusPill({ status }: { status: string }) {
  const tone =
    status === "indexed"
      ? "bg-emerald-100 text-emerald-700"
      : status === "chunked"
        ? "bg-cyan-100 text-cyan-700"
        : status === "parsed"
          ? "bg-amber-100 text-amber-700"
          : status === "failed"
            ? "bg-rose-100 text-rose-700"
            : "bg-slate-100 text-slate-700";

  return (
    <span className={cn("rounded-full px-2 py-0.5 text-[11px] font-semibold", tone)}>
      {status}
    </span>
  );
}

function formatDateTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return date.toLocaleString("en-GB", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}
