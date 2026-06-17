"use client";

import { Download, ExternalLink, FileText, Quote } from "lucide-react";
import type { ReactNode } from "react";
import React from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { cn } from "@/lib/utils";
import type { DocumentDetailResponse, RagCitation } from "@/lib/api";

type CitationMap = Map<number, RagCitation>;

type Props = {
  answer: string;
  asking: boolean;
  citations: RagCitation[];
  citationDocuments: Record<string, DocumentDetailResponse>;
  onCitationClick: (citationIndex: number) => void;
  selectedCitationIndex: number | null;
};

const CITATION_PATTERN = /\[(\d+)\]/g;
const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL?.trim() || "http://localhost:8000";

export function ChatAnswerPanel({
  answer,
  asking,
  citations,
  citationDocuments,
  onCitationClick,
  selectedCitationIndex,
}: Props) {
  const citationMap: CitationMap = new Map(
    citations.map((citation) => [citation.citation_index, citation]),
  );

  return (
    <div className="space-y-5">
      <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
        <div className="mb-4 flex items-center justify-between gap-3">
          <div>
            <h2 className="text-sm font-semibold uppercase tracking-[0.18em] text-slate-500">
              Câu trả lời
            </h2>
            <p className="mt-1 text-sm text-slate-500">
              Nội dung được hiển thị kèm citation trong dòng.
            </p>
          </div>
          <span
            className={cn(
              "rounded-full px-3 py-1 text-xs font-semibold uppercase tracking-[0.18em]",
              asking
                ? "bg-cyan-50 text-cyan-700"
                : answer
                  ? "bg-emerald-50 text-emerald-700"
                  : "bg-slate-100 text-slate-500",
            )}
          >
            {asking ? "Đang trả lời" : answer ? "Sẵn sàng" : "Chưa hỏi"}
          </span>
        </div>

        <div className="min-h-40">
          {answer ? (
            <>
              <MarkdownAnswer
                citationMap={citationMap}
                markdown={normalizeMarkdown(answer)}
                onCitationClick={onCitationClick}
              />
              <AnswerSourceList
                citationDocuments={citationDocuments}
                citations={citations}
                onCitationClick={onCitationClick}
              />
            </>
          ) : (
            <p className="text-sm leading-7 text-slate-500">
              Câu trả lời sẽ hiển thị tại đây.
            </p>
          )}
          {asking ? (
            <span className="ml-1 inline-block h-5 w-2 animate-pulse rounded-full bg-cyan-600 align-middle" />
          ) : null}
        </div>
      </section>

      <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
        <div className="mb-4 flex items-center justify-between gap-3">
          <div>
            <h3 className="text-lg font-semibold text-slate-900">Tài liệu tham khảo</h3>
            <p className="text-sm text-slate-500">{citations.length} nguồn được trích dẫn.</p>
          </div>
        </div>

        {citations.length === 0 ? (
          <p className="rounded-xl border border-dashed border-slate-200 bg-slate-50 px-4 py-8 text-center text-sm text-slate-500">
            Chi tiết trích dẫn sẽ hiển thị sau khi có câu trả lời.
          </p>
        ) : (
          <div className="space-y-3">
            {citations.map((citation) => {
              const document = citationDocuments[citation.document_id];
              const metadata = citation.metadata ?? {};
              const sourceFlags = Array.isArray(metadata.source_flags)
                ? (metadata.source_flags as string[])
                : [];
              const articleNumber = stringValue(metadata.article_number);
              const articleTitle = stringValue(metadata.article_title);
              const chapterTitle = stringValue(metadata.chapter_title);
              const pageNumber = stringValue(metadata.page_number);

              return (
                <article
                  className={cn(
                    "rounded-2xl border p-4 transition-colors duration-200",
                    selectedCitationIndex === citation.citation_index
                      ? "border-cyan-300 bg-cyan-50/60 ring-2 ring-cyan-200"
                      : "border-slate-200 bg-slate-50/70",
                  )}
                  id={`citation-card-${citation.citation_index}`}
                  key={`${citation.document_id}-${citation.chunk_id}-${citation.citation_index}`}
                >
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div className="space-y-2">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="inline-flex h-7 min-w-7 items-center justify-center rounded-full bg-slate-900 px-2 text-xs font-semibold text-white">
                          {citation.citation_index}
                        </span>
                        <span className="text-sm font-semibold text-slate-900">
                          {document?.title ?? `Document ${citation.document_id.slice(0, 8)}`}
                        </span>
                        <span className="font-mono text-xs text-slate-500">
                          Đoạn {citation.chunk_index}
                        </span>
                      </div>

                      <div className="flex flex-wrap items-center gap-2 text-xs text-slate-600">
                        <span className="inline-flex items-center gap-1 rounded-full bg-white px-2.5 py-1">
                          <FileText className="h-3.5 w-3.5" />
                          {document?.files?.[0]?.filename ?? document?.filename ?? "Unknown file"}
                        </span>
                        {articleNumber ? (
                          <span className="rounded-full bg-white px-2.5 py-1">
                            Điều {articleNumber}
                          </span>
                        ) : null}
                        {pageNumber ? (
                          <span className="rounded-full bg-white px-2.5 py-1">
                            Trang {pageNumber}
                          </span>
                        ) : null}
                        {sourceFlags.map((flag) => (
                          <span
                            className="rounded-full bg-cyan-100 px-2.5 py-1 font-semibold uppercase tracking-[0.14em] text-cyan-800"
                            key={`${citation.citation_index}-${flag}`}
                          >
                            {flag}
                          </span>
                        ))}
                      </div>
                    </div>

                    <button
                      className="inline-flex cursor-pointer items-center gap-1 rounded-full border border-slate-200 bg-white px-3 py-1.5 text-xs font-semibold text-slate-700 transition-colors hover:border-cyan-300 hover:text-cyan-700"
                      onClick={() => onCitationClick(citation.citation_index)}
                      type="button"
                    >
                      <ExternalLink className="h-3.5 w-3.5" />
                      Xem nguồn
                    </button>
                  </div>

                  {articleTitle || chapterTitle ? (
                    <div className="mt-3 rounded-xl bg-white px-3 py-2 text-sm text-slate-700">
                      {articleTitle ? <p className="font-medium">{articleTitle}</p> : null}
                      {chapterTitle ? <p className="text-slate-500">{chapterTitle}</p> : null}
                    </div>
                  ) : null}

                  <div className="mt-3 rounded-xl bg-white px-4 py-3">
                    <div className="mb-2 flex items-center gap-2 text-xs font-semibold uppercase tracking-[0.16em] text-slate-500">
                      <Quote className="h-3.5 w-3.5" />
                      Trích đoạn
                    </div>
                    <p className="text-sm leading-7 text-slate-700">
                      {citation.quote ?? "Không có trích đoạn."}
                    </p>
                  </div>
                </article>
              );
            })}
          </div>
        )}
      </section>
    </div>
  );
}

function AnswerSourceList({
  citations,
  citationDocuments,
  onCitationClick,
}: {
  citations: RagCitation[];
  citationDocuments: Record<string, DocumentDetailResponse>;
  onCitationClick: (citationIndex: number) => void;
}) {
  if (citations.length === 0) {
    return null;
  }

  return (
    <div className="mt-5 border-t border-slate-200 pt-4">
      <div className="mb-3 flex items-center gap-2 text-sm font-semibold text-slate-900">
        <Quote className="h-4 w-4 text-cyan-700" />
        Trích dẫn từ:
      </div>
      <div className="space-y-2">
        {citations.map((citation) => {
          const document = citationDocuments[citation.document_id];
          const file = document?.files?.[0];
          const downloadUrl = file?.download_url
            ? absoluteDownloadUrl(file.download_url)
            : null;
          const title =
            document?.title ?? citation.document_title ?? `Tài liệu ${citation.document_id.slice(0, 8)}`;
          const fileName = file?.filename ?? citation.file_name ?? document?.filename ?? "Không rõ file";

          return (
            <div
              className="rounded-xl border border-slate-200 bg-slate-50 px-3 py-2 text-sm text-slate-700"
              key={`answer-source-${citation.document_id}-${citation.chunk_id}-${citation.citation_index}`}
            >
              <div className="flex flex-wrap items-center gap-2">
                <button
                  className="inline-flex h-6 min-w-6 cursor-pointer items-center justify-center rounded-full bg-slate-900 px-2 text-xs font-semibold text-white"
                  onClick={() => onCitationClick(citation.citation_index)}
                  type="button"
                >
                  {citation.citation_index}
                </button>
                <span className="font-medium text-slate-900">{title}</span>
                <span className="text-xs text-slate-500">Đoạn {citation.chunk_index}</span>
              </div>
              <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-slate-600">
                <span className="inline-flex items-center gap-1 rounded-full bg-white px-2.5 py-1">
                  <FileText className="h-3.5 w-3.5" />
                  {fileName}
                </span>
                {downloadUrl ? (
                  <a
                    className="inline-flex max-w-full items-center gap-1 rounded-full bg-white px-2.5 py-1 font-medium text-cyan-700 hover:text-cyan-900"
                    href={downloadUrl}
                    rel="noreferrer"
                    target="_blank"
                    title={downloadUrl}
                  >
                    <Download className="h-3.5 w-3.5 shrink-0" />
                    <span className="truncate">Tải file</span>
                  </a>
                ) : null}
              </div>
              {citation.quote ? (
                <p className="mt-2 line-clamp-2 text-xs leading-5 text-slate-600">
                  {citation.quote}
                </p>
              ) : null}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function MarkdownAnswer({
  markdown,
  citationMap,
  onCitationClick,
}: {
  markdown: string;
  citationMap: CitationMap;
  onCitationClick: (citationIndex: number) => void;
}) {
  return (
    <div className="prose prose-slate max-w-none text-[15px] leading-7 prose-p:my-3 prose-ul:my-3 prose-ol:my-3 prose-li:my-1 prose-strong:text-slate-900">
      <ReactMarkdown
        components={{
          p: ({ children }) => <p>{injectCitationBadges(children, citationMap, onCitationClick)}</p>,
          li: ({ children }) => <li>{injectCitationBadges(children, citationMap, onCitationClick)}</li>,
          strong: ({ children }) => (
            <strong>{injectCitationBadges(children, citationMap, onCitationClick)}</strong>
          ),
          em: ({ children }) => <em>{injectCitationBadges(children, citationMap, onCitationClick)}</em>,
          blockquote: ({ children }) => (
            <blockquote>{injectCitationBadges(children, citationMap, onCitationClick)}</blockquote>
          ),
          h1: ({ children }) => <h1>{injectCitationBadges(children, citationMap, onCitationClick)}</h1>,
          h2: ({ children }) => <h2>{injectCitationBadges(children, citationMap, onCitationClick)}</h2>,
          h3: ({ children }) => <h3>{injectCitationBadges(children, citationMap, onCitationClick)}</h3>,
          code: ({ children }) => (
            <code>{injectCitationBadges(children, citationMap, onCitationClick)}</code>
          ),
        }}
        remarkPlugins={[remarkGfm]}
      >
        {markdown}
      </ReactMarkdown>
    </div>
  );
}

function injectCitationBadges(
  children: ReactNode,
  citationMap: CitationMap,
  onCitationClick: (citationIndex: number) => void,
): ReactNode {
  return React.Children.map(children, (child) => {
    if (typeof child === "string") {
      return renderCitationText(child, citationMap, onCitationClick);
    }
    if (React.isValidElement<{ children?: ReactNode }>(child) && child.props.children) {
      return React.cloneElement(child, {
        children: injectCitationBadges(child.props.children, citationMap, onCitationClick),
      });
    }
    return child;
  });
}

function renderCitationText(
  text: string,
  citationMap: CitationMap,
  onCitationClick: (citationIndex: number) => void,
): ReactNode[] {
  const nodes: ReactNode[] = [];
  let lastIndex = 0;

  for (const match of text.matchAll(CITATION_PATTERN)) {
    const fullMatch = match[0];
    const indexText = match[1];
    const matchIndex = match.index ?? 0;
    const citationIndex = Number(indexText);

    if (matchIndex > lastIndex) {
      nodes.push(text.slice(lastIndex, matchIndex));
    }

    if (citationMap.has(citationIndex)) {
      nodes.push(
        <CitationBadge
          citationIndex={citationIndex}
          key={`${citationIndex}-${matchIndex}`}
          onClick={() => onCitationClick(citationIndex)}
        />,
      );
    } else {
      nodes.push(fullMatch);
    }

    lastIndex = matchIndex + fullMatch.length;
  }

  if (lastIndex < text.length) {
    nodes.push(text.slice(lastIndex));
  }

  return nodes;
}

function CitationBadge({
  citationIndex,
  onClick,
}: {
  citationIndex: number;
  onClick: () => void;
}) {
  return (
    <button
      className="mx-0.5 inline-flex h-5 min-w-5 translate-y-[-0.35em] cursor-pointer items-center justify-center rounded-full border border-cyan-200 bg-cyan-50 px-1.5 align-super text-[10px] font-semibold text-cyan-800 transition-colors hover:border-cyan-300 hover:bg-cyan-100"
      onClick={onClick}
      type="button"
    >
      {citationIndex}
    </button>
  );
}

function normalizeMarkdown(markdown: string): string {
  return markdown
    .replace(/\r\n/g, "\n")
    .replace(/([^\n])\n(?!\n|[-*]\s|\d+\.\s|>\s)/g, "$1  \n");
}

function absoluteDownloadUrl(downloadUrl: string): string {
  if (/^https?:\/\//i.test(downloadUrl)) {
    return downloadUrl;
  }
  return `${API_BASE_URL}${downloadUrl.startsWith("/") ? "" : "/"}${downloadUrl}`;
}

function stringValue(value: unknown): string | null {
  if (typeof value === "string" && value.trim()) {
    return value.trim();
  }
  if (typeof value === "number") {
    return String(value);
  }
  return null;
}
