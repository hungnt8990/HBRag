"use client";

import {
  Activity,
  AlertCircle,
  Brain,
  CheckCircle2,
  ChevronRight,
  Clock3,
  Database,
  Download,
  Eye,
  FileSearch,
  GitBranch,
  Layers3,
  Loader2,
  MessageSquareText,
  Play,
  RefreshCw,
  Rows3,
  Send,
  ServerCog,
  ShieldCheck,
  TerminalSquare,
  Trash2,
  Upload,
  Workflow,
  X,
} from "lucide-react";
import {
  type ChangeEvent,
  type FormEvent,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import { useRouter } from "next/navigation";

import { ChatAnswerPanel } from "@/components/chat-answer-panel";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import {
  askRagChat,
  clearAccessToken,
  createMemory,
  deleteDocument,
  deleteMemory,
  downloadDocumentFile,
  enqueueDofficeIngestionJob,
  getDocumentAccess,
  getDocumentChunkQdrantPayloads,
  getDocumentDetail,
  getProfiles,
  getCurrentUser,
  getErrorMessage,
  getMemorySettings,
  getRuntimeConfig,
  updateDocumentAccess,
  listDocuments,
  listIngestionJobs,
  listMemories,
  testHeadingRules,
  updateProfileConfig,
  type DocumentDetailResponse,
  type DocumentAccessPolicy,
  type DofficeIngestResponse,
  type DocumentListItem,
  type DocumentListResponse,
  type DocumentQdrantPayloadsResponse,
  type HeadingRuleTestMatch,
  type IngestionJob,
  type IngestionStep,
  type MemoryItem,
  type MemorySettings,
  type MemoryType,
  type RagCitation,
  type ProfileConfig,
  type RuntimeConfigResponse,
  type AuthUser,
  type AccessCatalogResponse,
  type UploadAccessOptions,
} from "@/lib/api";
import { streamRagChat } from "@/lib/streaming";
import { cn } from "@/lib/utils";

type ActiveView = "auto" | "documents" | "chat" | "settings" | "memory";
type TypewriterSpeed = "slow" | "normal" | "fast";
type PipelineStepKey = "upload" | "parse" | "chunk" | "enrich" | "index" | "graph";
type RunState = "idle" | "running" | "succeeded" | "failed";
type LogSource = "auto" | "debug" | "chat" | "system";

const DOFFICE_SOURCE_TYPE = "doffice_elasticsearch";

type UploadAccessForm = Required<
  Pick<
    UploadAccessOptions,
    | "organization_id"
    | "access_scope"
    | "classification"
    | "allowed_org_ids"
    | "allowed_role_names"
    | "allowed_group_codes"
    | "denied_org_ids"
    | "denied_role_names"
    | "denied_group_codes"
  >
>;

const DEFAULT_DOCUMENT_ACCESS: DocumentAccessPolicy = {
  scope: "unit_only",
  classification: "internal",
  owner_org_id: null,
  owner_org_path: null,
  business_domains: [],
  project_codes: [],
  allowed_org_ids: [],
  allowed_org_paths: [],
  allowed_role_names: [],
  allowed_group_codes: [],
  allowed_user_ids: [],
  denied_org_ids: [],
  denied_org_paths: [],
  denied_role_names: [],
  denied_group_codes: [],
  denied_user_ids: [],
  inherit_permission: true,
  access_policy_id: null,
};

type DocumentAccessListField =
  | "business_domains"
  | "project_codes"
  | "allowed_org_ids"
  | "allowed_org_paths"
  | "allowed_role_names"
  | "allowed_group_codes"
  | "allowed_user_ids"
  | "denied_org_ids"
  | "denied_org_paths"
  | "denied_role_names"
  | "denied_group_codes"
  | "denied_user_ids";

type AccessSelectOption = {
  value: string;
  label: string;
};

type DebugStep = {
  key: PipelineStepKey;
  label: string;
  state: RunState;
  durationMs: number | null;
  output: Record<string, unknown>;
  error: string | null;
};

type UiLog = {
  id: string;
  timestamp: string;
  source: LogSource;
  step: string;
  level: "info" | "success" | "error";
  message: string;
  durationMs?: number | null;
};

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL?.trim() || "http://localhost:8000";

const typewriterSpeedConfig: Record<
  TypewriterSpeed,
  { intervalMs: number; charsPerTick: number }
> = {
  slow: { intervalMs: 30, charsPerTick: 1 },
  normal: { intervalMs: 18, charsPerTick: 2 },
  fast: { intervalMs: 12, charsPerTick: 4 },
};

const pipelineDefinitions: Array<{
  key: PipelineStepKey;
  label: string;
  icon: typeof Upload;
}> = [
  { key: "upload", label: "Upload", icon: Upload },
  { key: "parse", label: "Parse", icon: FileSearch },
  { key: "chunk", label: "Chunk", icon: Layers3 },
  { key: "enrich", label: "Enrich", icon: Brain },
  { key: "index", label: "Embed + Index", icon: Database },
  { key: "graph", label: "Graph Index", icon: GitBranch },
];

const dofficePipelineDefinitions = pipelineDefinitions
  .filter((definition) => definition.key !== "upload")
  .map((definition) =>
    definition.key === "parse"
      ? { ...definition, label: "Clean MD -> Text" }
      : definition,
  );

const chunkModeOptions: ProfileConfig["chunk_mode"][] = [
  "recursive",
  "legal_article",
  "table_aware",
  "hybrid_structured",
  "docling_router",
  "slide_page",
  "heading_aware",
  "docling_v6",
];

const answerModeOptions: ProfileConfig["answer_mode"][] = [
  "generative",
  "extractive",
  "hybrid",
];

const answerStyleOptions: ProfileConfig["answer_style"][] = [
  "concise",
  "detailed",
  "policy_explainer",
  "table_qa",
];

const navItems: Array<{
  key: ActiveView;
  label: string;
  icon: typeof Workflow;
}> = [
  { key: "auto", label: "Auto Queue", icon: Workflow },
  { key: "documents", label: "Tra cứu văn bản", icon: FileSearch },
  { key: "chat", label: "RAG Chat", icon: MessageSquareText },
  { key: "settings", label: "RAG Config", icon: ServerCog },
  { key: "memory", label: "Memory", icon: Brain },
];

export default function Home() {
  const router = useRouter();
  const [activeView, setActiveView] = useState<ActiveView>("auto");
  const [currentUser, setCurrentUser] = useState<AuthUser | null>(null);
  const [authChecked, setAuthChecked] = useState(false);
  const [runtimeConfig, setRuntimeConfig] =
    useState<RuntimeConfigResponse | null>(null);
  const [systemError, setSystemError] = useState<string | null>(null);
  const [autoJobs, setAutoJobs] = useState<IngestionJob[]>([]);
  const [dofficeIdVb, setDofficeIdVb] = useState("");
  const [dofficeForceRefresh, setDofficeForceRefresh] = useState(false);
  const [dofficeEnableEnrichment, setDofficeEnableEnrichment] = useState(true);
  const [dofficeSubmitting, setDofficeSubmitting] = useState(false);
  const [dofficeMessage, setDofficeMessage] = useState<{
    type: "success" | "error";
    text: string;
  } | null>(null);

  const [question, setQuestion] = useState("");
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [answer, setAnswer] = useState("");
  const [citations, setCitations] = useState<RagCitation[]>([]);
  const [citationDocuments, setCitationDocuments] = useState<
    Record<string, DocumentDetailResponse>
  >({});
  const [selectedCitationIndex, setSelectedCitationIndex] = useState<number | null>(null);
  const [asking, setAsking] = useState(false);

  const [streamingEnabled, setStreamingEnabled] = useState(true);
  const [typewriterEnabled, setTypewriterEnabled] = useState(true);
  const [typewriterSpeed, setTypewriterSpeed] =
    useState<TypewriterSpeed>("normal");
  const [memorySettings, setMemorySettings] = useState<MemorySettings | null>(
    null,
  );
  const [useMemory, setUseMemory] = useState(true);
  const [useMem0, setUseMem0] = useState(false);
  const [memoryTopK, setMemoryTopK] = useState(5);
  const [useGraph, setUseGraph] = useState(false);
  const [adminViewAll, setAdminViewAll] = useState(true);
  const [graphExpansionDepth, setGraphExpansionDepth] = useState(1);
  const [graphExpansionLimit, setGraphExpansionLimit] = useState(20);
  const settingsInitialized = useRef(false);

  const pendingTextRef = useRef("");
  const isStreamDoneRef = useRef(false);
  const intervalRef = useRef<number | null>(null);

  const [, setLogs] = useState<UiLog[]>([]);

  const [memoryItems, setMemoryItems] = useState<MemoryItem[]>([]);
  const [memoryDraft, setMemoryDraft] = useState("");
  const [memoryDraftType, setMemoryDraftType] = useState<MemoryType>("preference");
  const [memoryBusy, setMemoryBusy] = useState(false);

  const clearTypewriter = useCallback(() => {
    if (intervalRef.current !== null) {
      window.clearInterval(intervalRef.current);
      intervalRef.current = null;
    }
    pendingTextRef.current = "";
    isStreamDoneRef.current = false;
  }, []);

  useEffect(() => clearTypewriter, [clearTypewriter]);

  useEffect(() => {
    if (citations.length === 0) {
      setCitationDocuments({});
      setSelectedCitationIndex(null);
      return;
    }

    const uniqueDocumentIds = [...new Set(citations.map((citation) => citation.document_id))];
    let cancelled = false;

    void Promise.all(
      uniqueDocumentIds.map(async (documentId) => {
        try {
          const detail = await getDocumentDetail(documentId);
          return [documentId, detail] as const;
        } catch {
          return null;
        }
      }),
    ).then((entries) => {
      if (cancelled) {
        return;
      }
      const next: Record<string, DocumentDetailResponse> = {};
      for (const entry of entries) {
        if (entry) {
          next[entry[0]] = entry[1];
        }
      }
      setCitationDocuments(next);
    });

    return () => {
      cancelled = true;
    };
  }, [citations]);

  const appendLog = useCallback(
    (
      source: LogSource,
      step: string,
      level: UiLog["level"],
      message: string,
      durationMs?: number | null,
    ) => {
      setLogs((current) => [
        {
          id: `${Date.now()}-${Math.random().toString(16).slice(2)}`,
          timestamp: new Date().toISOString(),
          source,
          step,
          level,
          message,
          durationMs,
        },
        ...current,
      ]);
    },
    [],
  );

  useEffect(() => {
    let mounted = true;
    getCurrentUser()
      .then((user) => {
        if (!mounted) {
          return;
        }
        setCurrentUser(user);
        setAuthChecked(true);
      })
      .catch(() => {
        clearAccessToken();
        router.replace("/login");
      });
    return () => {
      mounted = false;
    };
  }, [router]);

  const refreshRuntimeConfig = useCallback(async () => {
    try {
      const config = await getRuntimeConfig();
      setRuntimeConfig(config);
      setSystemError(null);
      if (!settingsInitialized.current) {
        setUseGraph(config.graph_enabled);
        setGraphExpansionDepth(config.graph_expansion_depth);
        setGraphExpansionLimit(config.graph_expansion_limit);
        settingsInitialized.current = true;
      }
    } catch (error) {
      const message = getErrorMessage(error);
      setSystemError(message);
      appendLog("system", "runtime", "error", message);
    }
  }, [appendLog]);

  const refreshMemorySettings = useCallback(async () => {
    try {
      const config = await getMemorySettings();
      setMemorySettings(config);
      if (!settingsInitialized.current) {
        // Settings init is owned by runtime config; only memory defaults here.
      }
      setMemoryTopK((current) => (current === 5 ? config.memory_top_k : current));
      setUseMemory((current) => (config.memory_enabled ? current : false));
      setUseMem0((current) => (config.mem0_enabled ? current : false));
    } catch (error) {
      appendLog("system", "memory", "error", getErrorMessage(error));
    }
  }, [appendLog]);

  const refreshMemoryItems = useCallback(async () => {
    try {
      const items = await listMemories();
      setMemoryItems(items);
    } catch (error) {
      appendLog("system", "memory", "error", getErrorMessage(error));
    }
  }, [appendLog]);

  const refreshJobs = useCallback(async () => {
    try {
      const jobs = await listIngestionJobs();
      setAutoJobs(jobs);
    } catch (error) {
      appendLog("auto", "queue", "error", getErrorMessage(error));
    }
  }, [appendLog]);

  useEffect(() => {
    if (!authChecked) {
      return;
    }
    void refreshRuntimeConfig();
    void refreshJobs();
    void refreshMemorySettings();
    void refreshMemoryItems();
  }, [
    authChecked,
    refreshJobs,
    refreshRuntimeConfig,
    refreshMemorySettings,
    refreshMemoryItems,
  ]);

  useEffect(() => {
    const hasActiveJob = autoJobs.some((job) =>
      ["queued", "running"].includes(job.status),
    );
    if (!hasActiveJob) {
      return;
    }

    const timer = window.setInterval(() => {
      void refreshJobs();
    }, 1500);

    return () => window.clearInterval(timer);
  }, [autoJobs, refreshJobs]);

  const handleDofficeSubmit = async () => {
    const idVb = dofficeIdVb.trim();
    if (!idVb || dofficeSubmitting) {
      return;
    }

    setDofficeSubmitting(true);
    setDofficeMessage(null);
    const started = performance.now();
    appendLog("auto", "doffice", "info", `Đưa văn bản DOffice ${idVb} vào hàng đợi.`);

    try {
      const job = await enqueueDofficeIngestionJob({
        id_vb: idVb,
        force_refresh: dofficeForceRefresh,
        enable_enrichment: dofficeEnableEnrichment,
      });
      const durationMs = performance.now() - started;
      setAutoJobs((current) => [job, ...current.filter((item) => item.job_id !== job.job_id)]);
      setDofficeMessage({
        type: "success",
        text: `Đã tạo job xử lý văn bản ${idVb}.`,
      });
      appendLog(
        "auto",
        "doffice",
        "success",
        `Created DOffice ingestion job ${compactId(job.job_id)} for id_vb=${idVb}.`,
        durationMs,
      );
      await refreshJobs();
    } catch (error) {
      const message = getErrorMessage(error);
      appendLog("auto", "doffice", "error", message);
      setDofficeMessage({ type: "error", text: message });
    } finally {
      setDofficeSubmitting(false);
    }
  };

  const handleAsk = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const query = question.trim();
    if (!query || asking) {
      return;
    }

    setAsking(true);
    setAnswer("");
    setCitations([]);
    setSelectedCitationIndex(null);
    clearTypewriter();
    const started = performance.now();

    const useStreaming = streamingEnabled && (runtimeConfig?.streaming_supported ?? true);
    if (useStreaming) {
      appendLog("chat", "rag", "info", "Streaming grounded answer.");
      const useTypewriter = typewriterEnabled;
      let streamErrored = false;

      let typewriterDone: Promise<void> = Promise.resolve();
      if (useTypewriter) {
        const { intervalMs, charsPerTick } =
          typewriterSpeedConfig[typewriterSpeed];
        typewriterDone = new Promise<void>((resolve) => {
          intervalRef.current = window.setInterval(() => {
            if (pendingTextRef.current.length > 0) {
              const chunk = pendingTextRef.current.slice(0, charsPerTick);
              pendingTextRef.current = pendingTextRef.current.slice(charsPerTick);
              setAnswer((current) => current + chunk);
              return;
            }
            if (isStreamDoneRef.current) {
              if (intervalRef.current !== null) {
                window.clearInterval(intervalRef.current);
                intervalRef.current = null;
              }
              resolve();
            }
          }, intervalMs);
        });
      }

      try {
        await streamRagChat(
          {
            query,
            session_id: sessionId ?? undefined,
            use_memory: useMemory,
            use_mem0: useMem0,
            memory_top_k: memoryTopK,
            use_graph: useGraph,
            graph_expansion_depth: graphExpansionDepth,
            graph_expansion_limit: graphExpansionLimit,
            admin_view_all: adminViewAll,
          },
          {
            onMetadata: (data) => setSessionId(data.session_id),
            onToken: (delta) => {
              if (useTypewriter) {
                pendingTextRef.current += delta;
              } else {
                setAnswer((current) => current + delta);
              }
            },
            onCitations: (incoming) => setCitations(incoming),
            onError: (message) => {
              streamErrored = true;
              appendLog("chat", "rag", "error", message);
            },
            onDone: () => {
              const durationMs = performance.now() - started;
              appendLog(
                "chat",
                "rag",
                "success",
                "Streamed answer completed.",
                durationMs,
              );
            },
          },
        );
      } catch (error) {
        if (!streamErrored) {
          appendLog("chat", "rag", "error", getErrorMessage(error));
        }
      } finally {
        isStreamDoneRef.current = true;
        await typewriterDone;
        clearTypewriter();
        setAsking(false);
      }
      return;
    }

    appendLog("chat", "rag", "info", "Generating grounded answer.");
    try {
      const response = await askRagChat({
        query,
        session_id: sessionId ?? undefined,
        use_memory: useMemory,
        use_mem0: useMem0,
        memory_top_k: memoryTopK,
        use_graph: useGraph,
        graph_expansion_depth: graphExpansionDepth,
        graph_expansion_limit: graphExpansionLimit,
        admin_view_all: adminViewAll,
      });
      const durationMs = performance.now() - started;
      setSessionId(response.session_id);
      setAnswer(response.answer);
      setCitations(response.citations);
      appendLog(
        "chat",
        "rag",
        "success",
        `Generated answer with ${response.citations.length} citations.`,
        durationMs,
      );
    } catch (error) {
      appendLog("chat", "rag", "error", getErrorMessage(error));
    } finally {
      setAsking(false);
    }
  };

  const handleAddMemory = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const content = memoryDraft.trim();
    if (!content || memoryBusy) {
      return;
    }
    setMemoryBusy(true);
    try {
      const created = await createMemory(content, memoryDraftType);
      setMemoryItems((current) => [created, ...current]);
      setMemoryDraft("");
      appendLog("system", "memory", "success", "Saved a memory item.");
    } catch (error) {
      appendLog("system", "memory", "error", getErrorMessage(error));
    } finally {
      setMemoryBusy(false);
    }
  };

  const handleDeleteMemory = async (memoryId: string) => {
    try {
      await deleteMemory(memoryId);
      setMemoryItems((current) => current.filter((item) => item.id !== memoryId));
      appendLog("system", "memory", "success", "Removed a memory item.");
    } catch (error) {
      appendLog("system", "memory", "error", getErrorMessage(error));
    }
  };

  if (!authChecked) {
    return (
      <main className="flex min-h-screen items-center justify-center bg-slate-50 text-slate-700">
        <div className="flex items-center gap-3 rounded-xl bg-white px-5 py-4 shadow-sm">
          <Loader2 className="h-4 w-4 animate-spin text-cyan-700" />
          Checking session
        </div>
      </main>
    );
  }

  return (
    <main className="min-h-screen bg-slate-50 text-slate-950">
      <header className="border-b border-slate-800 bg-slate-900 text-white shadow-sm">
        <div className="mx-auto flex max-w-[1600px] flex-col gap-4 px-6 py-5 lg:flex-row lg:items-center lg:justify-between">
          <div>
            <div className="flex flex-wrap items-center gap-2">
              <span className="inline-flex items-center gap-2 rounded-full border border-cyan-400/20 bg-cyan-400/10 px-3 py-1 text-xs font-medium text-cyan-100">
                <Workflow className="h-3.5 w-3.5" />
                HBRag Admin Console
              </span>
              <RuntimeBadges config={runtimeConfig} />
            </div>
            <h1 className="mt-3 text-2xl font-semibold tracking-normal">
              Document Ingestion Operations
            </h1>
            <p className="mt-1 text-sm text-slate-300">
              Queue, inspect, index, and validate Hybrid RAG documents.
            </p>
          </div>
          <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
            {currentUser ? (
              <span className="rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-xs text-slate-300">
                {currentUser.full_name || currentUser.username}
              </span>
            ) : null}
            <span className="rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 font-mono text-xs text-slate-300">
              API {API_BASE_URL}
            </span>
            <Button
              className="bg-cyan-500 text-slate-950 hover:bg-cyan-400"
              onClick={() => {
                void refreshRuntimeConfig();
                void refreshJobs();
              }}
              type="button"
            >
              <RefreshCw className="h-4 w-4" />
              Refresh
            </Button>
            <Button
              className="border-slate-700 bg-slate-800 text-slate-100 hover:bg-slate-700"
              onClick={() => {
                clearAccessToken();
                router.replace("/login");
              }}
              type="button"
              variant="outline"
            >
              Logout
            </Button>
          </div>
        </div>
      </header>

      <div className="mx-auto max-w-[1600px] px-6 py-6">
        {systemError ? (
          <div
            className="mb-4 flex items-center gap-2 rounded-xl border border-rose-200/70 bg-rose-50 px-4 py-3 text-sm text-rose-700"
            role="alert"
          >
            <AlertCircle className="h-4 w-4" />
            {systemError}
          </div>
        ) : null}

        <nav className="mb-5 inline-flex rounded-xl bg-slate-900 p-1 shadow-sm">
          {navItems.map((item) => {
            const Icon = item.icon;
            const active = activeView === item.key;
            return (
              <button
                className={cn(
                  "inline-flex cursor-pointer items-center gap-2 rounded-lg px-4 py-2 text-sm font-medium transition-colors",
                  active
                    ? "bg-cyan-400 text-slate-950"
                    : "text-slate-300 hover:bg-slate-800 hover:text-white",
                )}
                key={item.key}
                onClick={() => setActiveView(item.key)}
                type="button"
              >
                <Icon className="h-4 w-4" />
                {item.label}
              </button>
            );
          })}
        </nav>

        {activeView === "auto" ? (
          <AutoJobRunnerView
            dofficeEnableEnrichment={dofficeEnableEnrichment}
            dofficeForceRefresh={dofficeForceRefresh}
            dofficeIdVb={dofficeIdVb}
            dofficeLoading={dofficeSubmitting}
            dofficeMessage={dofficeMessage}
            jobs={autoJobs}
            onDofficeEnableEnrichmentChange={setDofficeEnableEnrichment}
            onDofficeForceRefreshChange={setDofficeForceRefresh}
            onDofficeIdVbChange={(value) => {
              setDofficeIdVb(value);
              setDofficeMessage(null);
            }}
            onDofficeSubmit={handleDofficeSubmit}
            onRefreshJobs={() => {
              void refreshJobs();
            }}
          />
        ) : null}

        {activeView === "documents" ? <DocumentSearchView /> : null}

        {activeView === "chat" ? (
          <ChatView
            answer={answer}
            asking={asking}
            citations={citations}
            citationDocuments={citationDocuments}
            onAsk={handleAsk}
            onCitationClick={(citationIndex) => {
              setSelectedCitationIndex(citationIndex);
              const element = document.getElementById(`citation-card-${citationIndex}`);
              if (element) {
                element.scrollIntoView({ behavior: "smooth", block: "center" });
              }
            }}
            onQuestionChange={setQuestion}
            question={question}
            selectedCitationIndex={selectedCitationIndex}
            sessionId={sessionId}
          />
        ) : null}

        {activeView === "settings" ? (
          <SettingsPanel
            memorySettings={memorySettings}
            runtimeConfig={runtimeConfig}
            setStreamingEnabled={setStreamingEnabled}
            setTypewriterEnabled={setTypewriterEnabled}
            setTypewriterSpeed={setTypewriterSpeed}
            setUseMem0={setUseMem0}
            setUseMemory={setUseMemory}
            setMemoryTopK={setMemoryTopK}
            setUseGraph={setUseGraph}
            adminViewAll={adminViewAll}
            setAdminViewAll={setAdminViewAll}
            setGraphExpansionDepth={setGraphExpansionDepth}
            setGraphExpansionLimit={setGraphExpansionLimit}
            streamingEnabled={streamingEnabled}
            typewriterEnabled={typewriterEnabled}
            typewriterSpeed={typewriterSpeed}
            useMem0={useMem0}
            useMemory={useMemory}
            memoryTopK={memoryTopK}
            useGraph={useGraph}
            graphExpansionDepth={graphExpansionDepth}
            graphExpansionLimit={graphExpansionLimit}
          />
        ) : null}

        {activeView === "memory" ? (
          <MemoryView
            busy={memoryBusy}
            draft={memoryDraft}
            draftType={memoryDraftType}
            items={memoryItems}
            memorySettings={memorySettings}
            onAdd={handleAddMemory}
            onDelete={handleDeleteMemory}
            onDraftChange={setMemoryDraft}
            onDraftTypeChange={setMemoryDraftType}
            onRefresh={() => {
              void refreshMemoryItems();
              void refreshMemorySettings();
            }}
          />
        ) : null}
      </div>
    </main>
  );
}

function DocumentSearchView() {
  const PAGE_SIZE = 20;
  const [queryInput, setQueryInput] = useState("");
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(0);
  const [data, setData] = useState<DocumentListResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedDoc, setSelectedDoc] = useState<DocumentListItem | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  // Lọc "chỉ văn bản đã có point trên Qdrant (đã embed)" -> để soi chất lượng point sau khi chạy job.
  const [embeddedOnly, setEmbeddedOnly] = useState(false);

  const load = useCallback(
    async (term: string, pageIndex: number) => {
      setLoading(true);
      setError(null);
      try {
        const res = await listDocuments({
          search: term || undefined,
          qdrantIndexed: embeddedOnly ? true : undefined,
          limit: PAGE_SIZE,
          offset: pageIndex * PAGE_SIZE,
        });
        setData(res);
      } catch (err) {
        setError(getErrorMessage(err));
        setData(null);
      } finally {
        setLoading(false);
      }
    },
    [embeddedOnly],
  );

  useEffect(() => {
    void load(search, page);
  }, [search, page, load]);

  const handleDelete = useCallback(
    async (doc: DocumentListItem) => {
      if (
        !window.confirm(
          `Xóa văn bản "${doc.title}"?\nSẽ xóa khỏi PostgreSQL + Qdrant + Elasticsearch (KHÔNG hồi phục).`,
        )
      ) {
        return;
      }
      setDeletingId(doc.document_id);
      setError(null);
      try {
        await deleteDocument(doc.document_id);
        await load(search, page);
      } catch (err) {
        setError(getErrorMessage(err));
      } finally {
        setDeletingId(null);
      }
    },
    [load, search, page],
  );

  const total = data?.total ?? 0;
  const items = data?.items ?? [];
  const maxPage = Math.max(0, Math.ceil(total / PAGE_SIZE) - 1);

  return (
    <div className="space-y-5">
      <Card className="bg-white shadow-sm">
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <FileSearch className="h-5 w-5 text-emerald-700" />
            Tra cứu văn bản
          </CardTitle>
          <CardDescription>
            Tìm trong toàn bộ văn bản đã đồng bộ (PostgreSQL) — theo tiêu đề/ký hiệu/tên file, có phân trang.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <form
            className="flex gap-2"
            onSubmit={(event) => {
              event.preventDefault();
              setPage(0);
              setSearch(queryInput.trim());
            }}
          >
            <Input
              className="flex-1"
              onChange={(event) => setQueryInput(event.target.value)}
              placeholder="Tìm theo tiêu đề / ký hiệu / tên file..."
              value={queryInput}
            />
            <Button className="bg-emerald-600 text-white hover:bg-emerald-700" type="submit">
              {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <FileSearch className="h-4 w-4" />}
              Tìm
            </Button>
            <Button
              className="border-slate-200 bg-white text-slate-700 hover:bg-slate-50"
              onClick={() => {
                void load(search, page);
              }}
              type="button"
              variant="outline"
            >
              <RefreshCw className={cn("h-4 w-4", loading && "animate-spin")} />
            </Button>
          </form>

          <label className="flex w-fit cursor-pointer items-center gap-2 text-xs text-slate-600">
            <input
              checked={embeddedOnly}
              className="h-4 w-4 cursor-pointer accent-emerald-600"
              onChange={(event) => {
                setPage(0);
                setEmbeddedOnly(event.target.checked);
              }}
              type="checkbox"
            />
            Chỉ văn bản đã có point trên Qdrant (đã embed)
          </label>

          {error ? (
            <div className="rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700">
              {error}
            </div>
          ) : null}

          <div className="flex items-center justify-between text-xs text-slate-500">
            <span>
              {total.toLocaleString()} văn bản{search ? ` khớp "${search}"` : ""}
            </span>
            <span>
              Trang {page + 1} / {Math.max(1, maxPage + 1)}
            </span>
          </div>

          <div className="space-y-2">
            {loading && items.length === 0 ? (
              <div className="rounded-xl border border-slate-100 bg-slate-50 px-4 py-6 text-center text-sm text-slate-500">
                Đang tải...
              </div>
            ) : items.length === 0 ? (
              <div className="rounded-xl border border-slate-100 bg-slate-50 px-4 py-6 text-center text-sm text-slate-500">
                Không có văn bản nào.
              </div>
            ) : (
              items.map((doc) => (
                <div
                  className="flex items-start gap-2 rounded-xl border border-slate-100 bg-white px-3 py-3 transition-colors hover:border-cyan-200 hover:bg-cyan-50/30"
                  key={doc.document_id}
                >
                  <button
                    className="min-w-0 flex-1 cursor-pointer text-left"
                    onClick={() => setSelectedDoc(doc)}
                    type="button"
                  >
                    <div className="flex flex-wrap items-center gap-2">
                      <p className="truncate text-sm font-semibold text-slate-800">{doc.title}</p>
                      <span className="rounded-full border border-slate-200 bg-slate-50 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-slate-600">
                        {doc.status}
                      </span>
                      {doc.source_type === "doffice_elasticsearch" ? (
                        <span className="rounded-full border border-sky-200 bg-sky-50 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-sky-700">
                          AI DO
                        </span>
                      ) : null}
                    </div>
                    <div className="mt-2 grid gap-1 text-xs text-slate-500 sm:grid-cols-2">
                      <span className="truncate" title={doc.ky_hieu ?? undefined}>
                        Ký hiệu: <span className="font-medium text-slate-700">{doc.ky_hieu ?? "--"}</span>
                      </span>
                      <span className="truncate">
                        id_vb: <span className="font-medium text-slate-700">{doc.id_vb ?? "--"}</span>
                      </span>
                      <span className="truncate">
                        Chunk (PG): {doc.chunk_count} · Qdrant:{" "}
                        <span className="font-medium text-cyan-700">{doc.qdrant_point_count ?? "—"}</span> point
                      </span>
                      <span className="truncate">
                        Cập nhật: {new Date(doc.updated_at).toLocaleString()}
                      </span>
                      <span className="truncate sm:col-span-2" title={doc.document_id}>
                        doc_id: {doc.document_id}
                      </span>
                    </div>
                  </button>
                  <button
                    className="shrink-0 rounded-lg border border-rose-200 bg-white p-2 text-rose-600 hover:bg-rose-50 disabled:opacity-50"
                    disabled={deletingId === doc.document_id}
                    onClick={() => handleDelete(doc)}
                    title="Xóa văn bản (PostgreSQL + Qdrant + Elasticsearch)"
                    type="button"
                  >
                    {deletingId === doc.document_id ? (
                      <Loader2 className="h-4 w-4 animate-spin" />
                    ) : (
                      <Trash2 className="h-4 w-4" />
                    )}
                  </button>
                </div>
              ))
            )}
          </div>

          <div className="flex items-center justify-between">
            <Button
              className="border-slate-200 bg-white text-slate-700 hover:bg-slate-50"
              disabled={page <= 0 || loading}
              onClick={() => setPage((prev) => Math.max(0, prev - 1))}
              type="button"
              variant="outline"
            >
              ← Trước
            </Button>
            <Button
              className="border-slate-200 bg-white text-slate-700 hover:bg-slate-50"
              disabled={page >= maxPage || loading}
              onClick={() => setPage((prev) => prev + 1)}
              type="button"
              variant="outline"
            >
              Sau →
            </Button>
          </div>
        </CardContent>
      </Card>

      {selectedDoc ? (
        <DocumentChunksModal doc={selectedDoc} onClose={() => setSelectedDoc(null)} />
      ) : null}
    </div>
  );
}

// Format JSON gọn: mảng toàn giá trị nguyên thủy (acl_subjects, don_vi_list...) gom
// về 1 DÒNG thay vì mỗi phần tử 1 dòng; object/mảng lồng vẫn xuống dòng cho dễ đọc.
function formatCompactJson(value: unknown, indent = 0): string {
  const pad = "  ".repeat(indent);
  const padIn = "  ".repeat(indent + 1);
  if (Array.isArray(value)) {
    if (value.length === 0) return "[]";
    if (value.every((item) => item === null || typeof item !== "object")) {
      return `[${value.map((item) => JSON.stringify(item)).join(", ")}]`;
    }
    const items = value.map((item) => padIn + formatCompactJson(item, indent + 1));
    return `[\n${items.join(",\n")}\n${pad}]`;
  }
  if (value && typeof value === "object") {
    const entries = Object.entries(value as Record<string, unknown>);
    if (entries.length === 0) return "{}";
    const items = entries.map(
      ([key, val]) => `${padIn}${JSON.stringify(key)}: ${formatCompactJson(val, indent + 1)}`,
    );
    return `{\n${items.join(",\n")}\n${pad}}`;
  }
  return JSON.stringify(value);
}

function DocumentChunksModal({
  doc,
  onClose,
}: {
  doc: DocumentListItem;
  onClose: () => void;
}) {
  const [detail, setDetail] = useState<DocumentDetailResponse | null>(null);
  const [qdrant, setQdrant] = useState<DocumentQdrantPayloadsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [openChunks, setOpenChunks] = useState<Set<string>>(new Set());

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError(null);
    void (async () => {
      try {
        const [detailRes, qdrantRes] = await Promise.all([
          getDocumentDetail(doc.document_id),
          getDocumentChunkQdrantPayloads(doc.document_id).catch(() => null),
        ]);
        if (!active) return;
        setDetail(detailRes);
        setQdrant(qdrantRes);
      } catch (err) {
        if (active) setError(getErrorMessage(err));
      } finally {
        if (active) setLoading(false);
      }
    })();
    return () => {
      active = false;
    };
  }, [doc.document_id]);

  const qdrantByIndex = new Map<number, Record<string, unknown>>();
  for (const point of qdrant?.points ?? []) {
    const idx = Number(point["chunk_index"]);
    if (!Number.isNaN(idx)) {
      qdrantByIndex.set(idx, point);
    }
  }

  const toggle = (id: string) => {
    setOpenChunks((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  };

  const chunks = detail?.chunks ?? [];

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-slate-900/40 p-4 sm:p-8"
      onClick={onClose}
    >
      <div
        className="w-full max-w-3xl rounded-2xl bg-white shadow-xl"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="flex items-start justify-between border-b border-slate-100 px-5 py-4">
          <div className="min-w-0">
            <p className="truncate text-sm font-semibold text-slate-800">{doc.title}</p>
            <p className="mt-0.5 truncate text-xs text-slate-500">
              Ký hiệu: {doc.ky_hieu ?? "--"} · id_vb: {doc.id_vb ?? "--"} · {chunks.length} chunk (PG) ·
              Qdrant: {qdrant?.count ?? 0} point
            </p>
          </div>
          <button
            className="rounded-lg p-1 text-slate-400 hover:bg-slate-100"
            onClick={onClose}
            type="button"
          >
            <X className="h-5 w-5" />
          </button>
        </div>
        <div className="max-h-[70vh] space-y-2 overflow-y-auto px-5 py-4">
          {loading ? (
            <div className="py-8 text-center text-sm text-slate-500">Đang tải chunk...</div>
          ) : error ? (
            <div className="rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700">
              {error}
            </div>
          ) : chunks.length === 0 ? (
            <div className="py-8 text-center text-sm text-slate-500">
              Văn bản chưa có chunk trong PostgreSQL.
            </div>
          ) : (
            chunks.map((chunk) => {
              const payload = qdrantByIndex.get(chunk.chunk_index);
              const open = openChunks.has(chunk.id);
              return (
                <div className="rounded-xl border border-slate-100" key={chunk.id}>
                  <div className="flex items-start gap-2 px-3 py-2">
                    <span className="mt-0.5 shrink-0 rounded-md bg-slate-100 px-2 py-0.5 text-[11px] font-semibold text-slate-600">
                      #{chunk.chunk_index}
                    </span>
                    <p className="min-w-0 flex-1 whitespace-pre-wrap break-words text-xs text-slate-700">
                      {chunk.content}
                    </p>
                    <button
                      className={cn(
                        "shrink-0 rounded-lg border p-1.5",
                        payload
                          ? "border-cyan-200 bg-white text-cyan-700 hover:bg-cyan-50"
                          : "border-slate-200 bg-slate-50 text-slate-300",
                      )}
                      disabled={!payload}
                      onClick={() => toggle(chunk.id)}
                      title={payload ? "Xem metadata Qdrant" : "Chunk này không có point trên Qdrant"}
                      type="button"
                    >
                      <Eye className="h-4 w-4" />
                    </button>
                  </div>
                  {open && payload ? (
                    <pre className="overflow-x-auto border-t border-slate-100 bg-slate-50 px-3 py-2 text-[11px] leading-relaxed text-slate-700">
                      {formatCompactJson(payload)}
                    </pre>
                  ) : null}
                </div>
              );
            })
          )}
        </div>
      </div>
    </div>
  );
}

function AutoJobRunnerView({
  dofficeEnableEnrichment,
  dofficeForceRefresh,
  dofficeIdVb,
  dofficeLoading,
  dofficeMessage,
  jobs,
  onDofficeEnableEnrichmentChange,
  onDofficeForceRefreshChange,
  onDofficeIdVbChange,
  onDofficeSubmit,
  onRefreshJobs,
}: {
  dofficeEnableEnrichment: boolean;
  dofficeForceRefresh: boolean;
  dofficeIdVb: string;
  dofficeLoading: boolean;
  dofficeMessage: { type: "success" | "error"; text: string } | null;
  jobs: IngestionJob[];
  onDofficeEnableEnrichmentChange: (value: boolean) => void;
  onDofficeForceRefreshChange: (value: boolean) => void;
  onDofficeIdVbChange: (value: string) => void;
  onDofficeSubmit: () => void;
  onRefreshJobs: () => void;
}) {
  const jobMetrics = [
    { label: "Queued", value: jobs.filter((job) => job.status === "queued").length.toLocaleString() },
    { label: "Running", value: jobs.filter((job) => job.status === "running").length.toLocaleString() },
    { label: "Done", value: jobs.filter((job) => job.status === "succeeded").length.toLocaleString() },
    { label: "Failed", value: jobs.filter((job) => job.status === "failed").length.toLocaleString() },
  ];

  return (
    <div className="space-y-5">
      <section className="space-y-4">
        <Card className="bg-white shadow-sm">
          <CardHeader>
            <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
              <div>
                <CardTitle>Job Queue</CardTitle>
                <CardDescription>Trạng thái các job backend gần đây.</CardDescription>
              </div>
              <Button
                className="border-slate-200 bg-white text-slate-700 hover:bg-slate-50"
                onClick={onRefreshJobs}
                type="button"
                variant="outline"
              >
                <RefreshCw className="h-4 w-4" />
                Refresh
              </Button>
            </div>
          </CardHeader>
          <CardContent className="space-y-4">
            <MetricStrip metrics={jobMetrics} />
            {jobs.length === 0 ? (
              <EmptyState message="No jobs found." />
            ) : (
              <div className="space-y-3">
                {jobs.slice(0, 20).map((job) => {
                  const steps = buildPipelineStepsFromJob(job, dofficePipelineDefinitions);
                  return (
                    <article
                      className="rounded-xl border border-slate-100 bg-white px-4 py-3"
                      key={job.job_id}
                    >
                      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
                        <div className="flex items-center gap-2">
                          <StatusBadge state={normalizeState(job.status)} />
                          <span className="font-mono text-xs text-slate-500">
                            {compactId(job.job_id)}
                          </span>
                        </div>
                        <span className="text-xs text-slate-500">
                          {formatDateTime(job.updated_at)}
                        </span>
                      </div>
                      <div className="mb-3 grid gap-2 text-xs text-slate-600 sm:grid-cols-3">
                        <QueueMetric label="Document" value={job.document_id ? compactId(job.document_id) : "--"} />
                        <QueueMetric label="Source" value={job.filename || "--"} />
                        <QueueMetric
                          label="Profile"
                          value={job.resolved_ingestion_profile ?? job.ingestion_profile ?? "auto"}
                        />
                      </div>
                      <PipelineStrip
                        compact
                        dark={false}
                        onStepFocus={() => undefined}
                        runningStep={null}
                        steps={steps}
                      />
                      {job.error ? (
                        <p className="mt-3 text-sm text-rose-700">{job.error}</p>
                      ) : null}
                    </article>
                  );
                })}
              </div>
            )}
          </CardContent>
        </Card>
      </section>
    </div>
  );
}

// eslint-disable-next-line @typescript-eslint/no-unused-vars
function AutoQueueView({
  accessCatalog,
  deletingDocumentId,
  detailDocument,
  detailLoading,
  detailOpen,
  documents,
  dofficeEnableEnrichment,
  dofficeForceRefresh,
  dofficeIdVb,
  dofficeLoading,
  dofficeMessage,
  dofficeResult,
  file,
  isLoadingDocuments,
  jobs,
  loading,
  logs,
  message,
  uploadAccess,
  onCloseDetail,
  onFileChange,
  onUploadAccessChange,
  onDeletePublishedDocument,
  onDofficeEnableEnrichmentChange,
  onDofficeForceRefreshChange,
  onDofficeIdVbChange,
  onDofficeSubmit,
  onOpenDocument,
  onRefreshDocuments,
  onReingestDocument,
  onSubmit,
  rerunningDocumentId,
}: {
  accessCatalog: AccessCatalogResponse | null;
  deletingDocumentId: string | null;
  detailDocument: DocumentDetailResponse | null;
  detailLoading: boolean;
  detailOpen: boolean;
  documents: DocumentListItem[];
  dofficeEnableEnrichment: boolean;
  dofficeForceRefresh: boolean;
  dofficeIdVb: string;
  dofficeLoading: boolean;
  dofficeMessage: { type: "success" | "error"; text: string } | null;
  dofficeResult: DofficeIngestResponse | null;
  file: File | null;
  isLoadingDocuments: boolean;
  jobs: IngestionJob[];
  loading: boolean;
  logs: UiLog[];
  message: { type: "success" | "error"; text: string } | null;
  uploadAccess: UploadAccessForm;
  onCloseDetail: () => void;
  onFileChange: (event: ChangeEvent<HTMLInputElement>) => void;
  onUploadAccessChange: (value: UploadAccessForm) => void;
  onDeletePublishedDocument: (documentId: string) => void;
  onDofficeEnableEnrichmentChange: (value: boolean) => void;
  onDofficeForceRefreshChange: (value: boolean) => void;
  onDofficeIdVbChange: (value: string) => void;
  onDofficeSubmit: () => void;
  onOpenDocument: (documentId: string) => void;
  onRefreshDocuments: () => void;
  onReingestDocument: (documentId: string) => void;
  onSubmit: () => void;
  rerunningDocumentId: string | null;
}) {
  const activeJobsByDocumentId = new Map(
    jobs
      .filter((job) => job.document_id && job.status !== "succeeded")
      .map((job) => [job.document_id as string, job]),
  );
  const metrics = [
    { label: "Documents", value: documents.length.toLocaleString() },
    {
      label: "Indexed",
      value: documents
        .filter((document) => document.status === "indexed")
        .length.toLocaleString(),
    },
    {
      label: "Chunks",
      value: documents
        .reduce((total, document) => total + document.chunk_count, 0)
      .toLocaleString(),
    },
  ];
  const setAccessField = (key: keyof UploadAccessForm, value: string) => {
    onUploadAccessChange({ ...uploadAccess, [key]: value });
  };
  const organizationOptions = buildOrganizationOptions(accessCatalog);
  const roleOptions = buildRoleOptions(accessCatalog);
  const groupOptions = buildGroupOptions(accessCatalog);
  const selectedAllowedOrgIds = splitListInput(uploadAccess.allowed_org_ids);
  const selectedAllowedRoleNames = splitListInput(uploadAccess.allowed_role_names);
  const selectedAllowedGroupCodes = splitListInput(uploadAccess.allowed_group_codes);

  return (
    <div className="grid gap-5 xl:grid-cols-[360px_minmax(0,1fr)_360px]">
      <Card className="bg-white shadow-sm">
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Upload className="h-5 w-5 text-cyan-700" />
            Queue Upload
          </CardTitle>
          <CardDescription>
            The backend queue will parse, chunk, embed, and index automatically.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <label className="block">
            <span className="text-xs font-semibold uppercase tracking-wider text-slate-500">
              Source document
            </span>
            <input
              className="mt-2 block w-full cursor-pointer rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700 file:mr-3 file:rounded-lg file:border-0 file:bg-slate-100 file:px-3 file:py-1.5 file:text-sm file:font-medium file:text-slate-700 hover:border-slate-300"
              onChange={onFileChange}
              type="file"
            />
          </label>
          <div className="rounded-xl border border-cyan-100 bg-cyan-50 px-3 py-2 text-sm text-cyan-900">
            <div className="text-xs font-semibold uppercase tracking-wider text-cyan-700">
              Ingestion profile
            </div>
            <div className="mt-1 font-medium">Auto detect</div>
            <p className="mt-1 text-xs text-cyan-800">
              The backend parses a preview, scores the saved ingestion profiles, and stores
              the resolved profile on the document/job metadata.
            </p>
          </div>
          <div className="space-y-3 rounded-xl border border-slate-200 bg-white px-3 py-3">
            <div className="grid gap-3 sm:grid-cols-2">
              <label className="block">
                <span className="text-xs font-semibold uppercase tracking-wider text-slate-500">
                  Scope
                </span>
                <select
                  className="mt-2 h-10 w-full rounded-lg border border-slate-200 bg-white px-3 text-sm text-slate-700"
                  onChange={(event) => setAccessField("access_scope", event.target.value)}
                  value={uploadAccess.access_scope}
                >
                  <option value="unit_only">Unit only</option>
                  <option value="subtree">Subtree</option>
                  <option value="corp_wide">Corp wide</option>
                  <option value="explicit_acl">Explicit ACL</option>
                </select>
              </label>
              <label className="block">
                <span className="text-xs font-semibold uppercase tracking-wider text-slate-500">
                  Classification
                </span>
                <select
                  className="mt-2 h-10 w-full rounded-lg border border-slate-200 bg-white px-3 text-sm text-slate-700"
                  onChange={(event) => setAccessField("classification", event.target.value)}
                  value={uploadAccess.classification}
                >
                  <option value="internal">Internal</option>
                  <option value="restricted">Restricted</option>
                  <option value="confidential">Confidential</option>
                  <option value="secret">Secret</option>
                </select>
              </label>
            </div>
            <label className="block">
              <span className="text-xs font-semibold uppercase tracking-wider text-slate-500">
                Owner company ID
              </span>
              <AccessSelectField
                className="mt-2"
                onChange={(value) => setAccessField("organization_id", value)}
                options={mergeOptionsWithSelected(
                  organizationOptions,
                  uploadAccess.organization_id ? [uploadAccess.organization_id] : [],
                )}
                placeholder="Default company"
                value={uploadAccess.organization_id}
              />
            </label>
            <label className="block">
              <span className="text-xs font-semibold uppercase tracking-wider text-slate-500">
                Allowed company IDs
              </span>
              <AccessMultiSelectField
                className="mt-2"
                onChange={(values) => setAccessField("allowed_org_ids", values.join(", "))}
                options={mergeOptionsWithSelected(organizationOptions, selectedAllowedOrgIds)}
                value={selectedAllowedOrgIds}
              />
            </label>
            <div className="grid gap-3 sm:grid-cols-2">
              <label className="block">
                <span className="text-xs font-semibold uppercase tracking-wider text-slate-500">
                  Allowed roles
                </span>
                <AccessMultiSelectField
                  className="mt-2"
                  onChange={(values) => setAccessField("allowed_role_names", values.join(", "))}
                  options={mergeOptionsWithSelected(roleOptions, selectedAllowedRoleNames)}
                  value={selectedAllowedRoleNames}
                />
              </label>
              <label className="block">
                <span className="text-xs font-semibold uppercase tracking-wider text-slate-500">
                  Allowed groups
                </span>
                {groupOptions.length > 0 ? (
                  <AccessMultiSelectField
                    className="mt-2"
                    onChange={(values) => setAccessField("allowed_group_codes", values.join(", "))}
                    options={mergeOptionsWithSelected(groupOptions, selectedAllowedGroupCodes)}
                    value={selectedAllowedGroupCodes}
                  />
                ) : (
                  <Input
                    className="mt-2"
                    onChange={(event) => setAccessField("allowed_group_codes", event.target.value)}
                    placeholder="ai-team, legal-team"
                    value={uploadAccess.allowed_group_codes}
                  />
                )}
              </label>
            </div>
          </div>
          <Button
            className="w-full bg-[#0d3b4c] text-white hover:bg-[#114e63]"
            disabled={!file || loading}
            onClick={onSubmit}
            type="button"
          >
            {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
            Enqueue ingestion
          </Button>
          {message ? (
            <div
              className={cn(
                "rounded-xl border px-3 py-2 text-sm",
                message.type === "error"
                  ? "border-rose-200 bg-rose-50 text-rose-700"
                  : "border-emerald-200 bg-emerald-50 text-emerald-700",
              )}
            >
              {message.text}
            </div>
          ) : null}
          <MetricStrip metrics={metrics} />
        </CardContent>
      </Card>

      <section>
        <Card className="bg-white shadow-sm">
          <CardHeader>
            <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
              <div>
                <CardTitle>Published Documents</CardTitle>
                <CardDescription>
                  Persistent documents from uploads and AI DO sources.
                </CardDescription>
              </div>
              <Button
                className="border-slate-200 bg-white text-slate-700 hover:bg-slate-50"
                onClick={onRefreshDocuments}
                type="button"
                variant="outline"
              >
                <RefreshCw className={cn("h-4 w-4", isLoadingDocuments && "animate-spin")} />
                Refresh
              </Button>
            </div>
          </CardHeader>
          <CardContent>
            <div className="space-y-2">
              {isLoadingDocuments ? (
                <EmptyState message="Loading published documents..." />
              ) : documents.length === 0 ? (
                <EmptyState message="No published documents found." />
              ) : (
                documents.map((document) => {
                  const activeJob = activeJobsByDocumentId.get(document.document_id);
                  const isDofficeDocument = isDofficeSource(document);
                  const shouldShowActiveJob = Boolean(activeJob && !isDofficeDocument);
                  const activeJobSteps = buildPipelineStepsFromJob(
                    activeJob ?? null,
                    isDofficeDocument ? dofficePipelineDefinitions : pipelineDefinitions,
                  );
                  const sourceLabel = formatDocumentSource(document);
                  const secondaryLabel = formatDocumentSecondaryLabel(document);

                  return (
                  <article
                    className="rounded-xl border border-slate-100 bg-white px-4 py-3 transition-colors hover:border-cyan-200 hover:bg-cyan-50/30"
                    key={document.document_id}
                  >
                    <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
                      <button
                        className="min-w-0 flex-1 cursor-pointer text-left"
                        onClick={() => onOpenDocument(document.document_id)}
                        type="button"
                      >
                        <div className="flex flex-wrap items-center gap-2">
                          <p className="truncate text-sm font-semibold text-slate-800">
                            {document.title}
                          </p>
                          <StatusBadge state={normalizeState(document.status)} />
                          {isDofficeDocument ? (
                            <span className="rounded-full border border-sky-200 bg-sky-50 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-sky-700">
                              AI DO
                            </span>
                          ) : null}
                          {shouldShowActiveJob && activeJob ? (
                            <StatusBadge state={normalizeState(activeJob.status)} compact />
                          ) : null}
                        </div>
                        <p className="mt-1 truncate text-xs text-slate-500">
                          {secondaryLabel} / {compactId(document.document_id)}
                        </p>
                        <div className="mt-2 grid gap-1 text-xs text-slate-500 sm:grid-cols-2">
                          <span className="truncate" title={sourceLabel}>
                            Source: {sourceLabel}
                          </span>
                          <span className="truncate" title={formatKnowledgeBase(document)}>
                            Knowledge base: {formatKnowledgeBase(document)}
                          </span>
                          <span className="truncate" title={formatDocumentScope(document)}>
                            Scope: {formatDocumentScope(document)}
                          </span>
                          <span className="truncate" title={formatDocumentProfile(document)}>
                            Profile: {formatDocumentProfile(document)}
                          </span>
                          <span className="truncate" title={formatPerson(document.uploaded_by)}>
                            Uploader: {formatPerson(document.uploaded_by)}
                          </span>
                          <span
                            className="truncate"
                            title={formatPerson(document.knowledge_base?.owner ?? null)}
                          >
                            Owner: {formatPerson(document.knowledge_base?.owner ?? null)}
                          </span>
                          <span
                            className="truncate sm:col-span-2"
                            title={document.organization?.ten_dviqly ?? "No organization"}
                          >
                            Organization: {document.organization?.ten_dviqly ?? "No organization"}
                          </span>
                        </div>
                      </button>
                      <div className="flex flex-wrap gap-2 lg:justify-end">
                        <Button
                          className="border-cyan-200 bg-white text-cyan-800 hover:bg-cyan-50"
                          disabled={isDofficeDocument || rerunningDocumentId === document.document_id}
                          onClick={() => onReingestDocument(document.document_id)}
                          aria-label={
                            isDofficeDocument
                              ? "DOffice documents are refreshed from the DOffice form"
                              : "Run parse, chunk, and vector indexing again for this document"
                          }
                          title={
                            isDofficeDocument
                              ? "DOffice documents are refreshed from the DOffice form"
                              : "Run parse, chunk, and vector indexing again for this document"
                          }
                          type="button"
                          variant="outline"
                        >
                          {rerunningDocumentId === document.document_id ? (
                            <Loader2 className="h-4 w-4 animate-spin" />
                          ) : (
                            <RefreshCw className="h-4 w-4" />
                          )}
                        </Button>
                        <Button
                          className="border-rose-200 bg-white text-rose-700 hover:bg-rose-50"
                          disabled={deletingDocumentId === document.document_id}
                          onClick={() => onDeletePublishedDocument(document.document_id)}
                          title="Delete from MinIO, Qdrant, and database"
                          type="button"
                          variant="outline"
                        >
                          {deletingDocumentId === document.document_id ? (
                            <Loader2 className="h-4 w-4 animate-spin" />
                          ) : (
                            <Trash2 className="h-4 w-4" />
                          )}
                          Delete
                        </Button>
                      </div>
                    </div>
                    <dl className="mt-3 grid gap-2 text-xs text-slate-600 sm:grid-cols-3">
                      <QueueMetric
                        label="Parsed chars"
                        value={document.parsed_character_count.toLocaleString()}
                      />
                      <QueueMetric label="Chunks" value={document.chunk_count.toLocaleString()} />
                      <QueueMetric
                        label="Vector indexed"
                        value={
                          document.vector_indexed_count === null
                            ? "--"
                            : document.vector_indexed_count.toLocaleString()
                        }
                      />
                    </dl>
                    {shouldShowActiveJob && activeJob ? (
                      <div className="mt-4 rounded-xl border border-cyan-100 bg-cyan-50/60 p-3">
                        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
                          <span className="text-xs font-semibold uppercase tracking-wider text-cyan-800">
                            Queue monitor pipeline
                          </span>
                          <span className="font-mono text-xs text-cyan-900">
                            {compactId(activeJob.job_id)}
                          </span>
                          <span className="text-xs text-cyan-800">
                            Profile: {activeJob.resolved_ingestion_profile ?? activeJob.ingestion_profile ?? "auto"}
                          </span>
                        </div>
                        <PipelineStrip
                          compact
                          dark={false}
                          onStepFocus={() => undefined}
                          runningStep={null}
                          steps={activeJobSteps}
                        />
                        {activeJob.error ? (
                          <p className="mt-3 text-sm text-rose-700">{activeJob.error}</p>
                        ) : null}
                      </div>
                    ) : null}
                  </article>
                  );
                })
              )}
            </div>
          </CardContent>
        </Card>
      </section>

      <LogPanel
        highlightedLogKey={null}
        logs={logs}
        title="Operation Logs"
      />

      <DocumentDetailModal
        accessCatalog={accessCatalog}
        document={detailDocument}
        loading={detailLoading}
        onClose={onCloseDetail}
        open={detailOpen}
      />
    </div>
  );
}

function DocumentDetailModal({
  accessCatalog,
  document,
  loading,
  onClose,
  open,
}: {
  accessCatalog: AccessCatalogResponse | null;
  document: DocumentDetailResponse | null;
  loading: boolean;
  onClose: () => void;
  open: boolean;
}) {
  const [activeTab, setActiveTab] = useState<"parse" | "chunk" | "embed" | "access">("parse");
  const [downloadingFileId, setDownloadingFileId] = useState<string | null>(null);
  const [downloadError, setDownloadError] = useState<string | null>(null);
  const [expandedMetadataChunkIds, setExpandedMetadataChunkIds] = useState<Set<string>>(
    () => new Set(),
  );
  const [accessPolicy, setAccessPolicy] = useState<DocumentAccessPolicy>(
    DEFAULT_DOCUMENT_ACCESS,
  );
  const [accessLoading, setAccessLoading] = useState(false);
  const [accessSaving, setAccessSaving] = useState(false);
  const [accessMessage, setAccessMessage] = useState<{
    type: "success" | "error";
    text: string;
  } | null>(null);

  useEffect(() => {
    if (open) {
      setActiveTab("parse");
      setDownloadError(null);
      setAccessMessage(null);
      setExpandedMetadataChunkIds(new Set());
    }
  }, [open, document?.document_id]);

  useEffect(() => {
    if (!open || !document?.document_id) {
      setAccessPolicy(DEFAULT_DOCUMENT_ACCESS);
      return;
    }

    let cancelled = false;
    setAccessLoading(true);
    setAccessMessage(null);
    void getDocumentAccess(document.document_id)
      .then((response) => {
        if (!cancelled) {
          setAccessPolicy({ ...DEFAULT_DOCUMENT_ACCESS, ...response.access });
        }
      })
      .catch((error) => {
        if (!cancelled) {
          setAccessMessage({ type: "error", text: getErrorMessage(error) });
        }
      })
      .finally(() => {
        if (!cancelled) {
          setAccessLoading(false);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [document?.document_id, open]);

  useEffect(() => {
    if (!open) {
      return;
    }

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        onClose();
      }
    };

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [onClose, open]);

  const handleDownloadFile = async (file: DocumentDetailResponse["files"][number]) => {
    setDownloadingFileId(file.id);
    setDownloadError(null);
    try {
      await downloadDocumentFile(file);
    } catch (error) {
      setDownloadError(getErrorMessage(error));
    } finally {
      setDownloadingFileId(null);
    }
  };

  const handleAccessScalarChange = (
    key: "scope" | "classification" | "owner_org_id" | "owner_org_path",
    value: string,
  ) => {
    setAccessPolicy((current) => ({
      ...current,
      [key]: value.trim() === "" ? null : value,
    }));
  };

  const handleAccessListChange = (key: DocumentAccessListField, value: string) => {
    setAccessPolicy((current) => ({ ...current, [key]: splitListInput(value) }));
  };

  const handleAccessListSelect = (
    key: DocumentAccessListField,
    value: string[],
  ) => {
    setAccessPolicy((current) => ({ ...current, [key]: value }));
  };

  const organizationOptions = buildOrganizationOptions(accessCatalog);
  const roleOptions = buildRoleOptions(accessCatalog);
  const groupOptions = buildGroupOptions(accessCatalog);

  const handleSaveAccess = async () => {
    if (!document?.document_id) {
      return;
    }
    setAccessSaving(true);
    setAccessMessage(null);
    try {
      const response = await updateDocumentAccess(document.document_id, accessPolicy);
      setAccessPolicy({ ...DEFAULT_DOCUMENT_ACCESS, ...response.access });
      setAccessMessage({ type: "success", text: "Document access was updated." });
    } catch (error) {
      setAccessMessage({ type: "error", text: getErrorMessage(error) });
    } finally {
      setAccessSaving(false);
    }
  };

  if (!open) {
    return null;
  }

  const vectorLogs = document?.pipeline_logs.filter(
    (log) => log.action === "index_vector",
  ) ?? [];
  const isDofficeDocument = document ? isDofficeSource(document) : false;
  const tabs = [
    { key: "parse", label: isDofficeDocument ? "Clean text" : "Parse", icon: FileSearch },
    { key: "chunk", label: "Chunk", icon: Rows3 },
    { key: "embed", label: "Embed", icon: Database },
    { key: "access", label: "Access", icon: ShieldCheck },
  ] as const;

  return (
    <div
      aria-modal="true"
      className="fixed inset-0 z-[100] flex items-center justify-center bg-slate-950/55 px-4 py-6"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) {
          onClose();
        }
      }}
      role="dialog"
    >
      <div className="relative z-[101] flex h-[90vh] w-full max-w-5xl flex-col rounded-xl bg-white shadow-xl">
        <div className="shrink-0 flex items-start justify-between gap-4 border-b border-slate-200 px-5 py-4">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <h2 className="truncate text-lg font-semibold text-slate-900">
                {document?.title ?? "Document detail"}
              </h2>
              {document ? <StatusBadge state={normalizeState(document.status)} /> : null}
              {isDofficeDocument ? (
                <span className="rounded-full border border-sky-200 bg-sky-50 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-sky-700">
                  AI DO
                </span>
              ) : null}
            </div>
            <p className="mt-1 truncate text-sm text-slate-500">
              {document ? formatDocumentSecondaryLabel(document) : "Loading document data..."}
            </p>
          </div>
          <Button
            className="h-9 w-9 shrink-0 border-slate-200 bg-white p-0 text-slate-600 hover:bg-slate-50"
            onClick={onClose}
            title="Close detail"
            type="button"
            variant="outline"
          >
            <X className="h-4 w-4" />
          </Button>
        </div>

        {loading ? (
          <div className="flex min-h-80 items-center justify-center gap-3 text-sm text-slate-500">
            <Loader2 className="h-4 w-4 animate-spin text-cyan-700" />
            Loading document detail...
          </div>
        ) : !document ? (
          <div className="p-5">
            <EmptyState message="Document detail is unavailable." />
          </div>
        ) : (
          <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
            <dl className="mb-4 grid gap-3 text-xs text-slate-600 sm:grid-cols-2 lg:grid-cols-4">
              <QueueMetric label="Document id" value={compactId(document.document_id)} />
              <QueueMetric label="Source" value={formatDocumentSource(document)} />
              <QueueMetric label="Parsed chars" value={document.parsed_character_count.toLocaleString()} />
              <QueueMetric label="Chunks" value={document.chunk_count.toLocaleString()} />
              <QueueMetric
                label="Vector indexed"
                value={
                  document.vector_indexed_count === null
                    ? "--"
                    : document.vector_indexed_count.toLocaleString()
                }
              />
              <QueueMetric label="Knowledge base" value={formatKnowledgeBase(document)} />
              <QueueMetric label="Scope" value={formatDocumentScope(document)} />
              <QueueMetric label="Uploader" value={formatPerson(document.uploaded_by)} />
              <QueueMetric label="Updated" value={formatDateTime(document.updated_at)} />
            </dl>

            <div className="mb-4 rounded-xl border border-slate-200 bg-white p-4">
              <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
                <p className="text-xs font-semibold uppercase tracking-wider text-slate-500">
                  Document files
                </p>
                <span className="text-xs text-slate-500">
                  {document.files.length.toLocaleString()} file{document.files.length === 1 ? "" : "s"}
                </span>
              </div>
              {document.files.length === 0 ? (
                <EmptyState
                  message={
                    isDofficeDocument
                      ? "AI DO document: no uploaded file is attached. Content was fetched from DOffice and cleaned before chunking."
                      : "No files are attached to this document."
                  }
                />
              ) : (
                <div className="space-y-3">
                  {document.files.map((file) => {
                    const downloadUrl = `${API_BASE_URL}${file.download_url}`;
                    const isDownloading = downloadingFileId === file.id;
                    return (
                      <article
                        className="rounded-lg border border-slate-200 bg-slate-50 p-3"
                        key={file.id}
                      >
                        <div className="flex flex-wrap items-start justify-between gap-3">
                          <div className="min-w-0 flex-1">
                            <p className="truncate text-sm font-semibold text-slate-800">
                              {file.filename}
                            </p>
                            <p className="mt-1 text-xs text-slate-500">
                              {file.mime_type || "application/octet-stream"} / {formatFileSize(file.file_size)}
                            </p>
                          </div>
                          <Button
                            className="h-9 shrink-0 border-slate-200 bg-white text-slate-700 hover:bg-slate-100"
                            disabled={isDownloading}
                            onClick={() => void handleDownloadFile(file)}
                            title="Download file"
                            type="button"
                            variant="outline"
                          >
                            {isDownloading ? (
                              <Loader2 className="h-4 w-4 animate-spin" />
                            ) : (
                              <Download className="h-4 w-4" />
                            )}
                            Download
                          </Button>
                        </div>
                        <p className="mt-3 break-all rounded-md bg-white px-3 py-2 font-mono text-xs text-slate-600">
                          {downloadUrl}
                        </p>
                      </article>
                    );
                  })}
                </div>
              )}
              {downloadError ? (
                <p className="mt-3 text-sm text-rose-700">{downloadError}</p>
              ) : null}
            </div>

            <div className="mb-4 inline-flex rounded-lg bg-slate-100 p-1">
              {tabs.map((tab) => {
                const Icon = tab.icon;
                return (
                  <button
                    className={cn(
                      "inline-flex cursor-pointer items-center gap-2 rounded-md px-3 py-1.5 text-sm font-medium transition-colors",
                      activeTab === tab.key
                        ? "bg-white text-slate-900 shadow-sm"
                        : "text-slate-500 hover:text-slate-800",
                    )}
                    key={tab.key}
                    onClick={() => setActiveTab(tab.key)}
                    type="button"
                  >
                    <Icon className="h-4 w-4" />
                    {tab.label}
                  </button>
                );
              })}
            </div>

            {activeTab === "parse" ? (
              <div className="space-y-3">
                <div className="rounded-xl border border-slate-200 bg-white p-4">
                  <p className="mb-2 text-xs font-semibold uppercase tracking-wider text-slate-500">
                    {isDofficeDocument ? "Cleaned text" : "Parsed text"}
                  </p>
                  <pre className="max-h-[68vh] overflow-auto whitespace-pre-wrap break-words rounded-lg border border-slate-200 bg-white p-4 text-sm leading-6 text-slate-800 shadow-inner">
                    {document.preview_text || "No parsed text available."}
                  </pre>
                </div>
                <PipelineLogList logs={document.pipeline_logs.filter((log) => log.action === "parse")} />
              </div>
            ) : null}

            {activeTab === "chunk" ? (
              <div className="space-y-3">
                {document.chunks.length === 0 ? (
                  <EmptyState message="No chunk data available." />
                ) : (
                  document.chunks.map((chunk) => {
                    const hasMetadata = Object.keys(chunk.metadata).length > 0;
                    const metadataExpanded = expandedMetadataChunkIds.has(chunk.id);
                    return (
                      <article className="rounded-xl border border-slate-200 bg-white p-4" key={chunk.id}>
                        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
                          <span className="text-sm font-semibold text-slate-800">
                            Chunk #{chunk.chunk_index}
                          </span>
                          <div className="flex items-center gap-2">
                            <span className="font-mono text-xs text-slate-500">
                              {chunk.token_count === null ? "tokens --" : `${chunk.token_count} tokens`}
                            </span>
                            {hasMetadata ? (
                              <button
                                aria-expanded={metadataExpanded}
                                aria-label={metadataExpanded ? "Hide metadata" : "Show metadata"}
                                className={cn(
                                  "inline-flex h-8 w-8 items-center justify-center rounded-md border transition-colors",
                                  metadataExpanded
                                    ? "border-slate-300 bg-slate-100 text-slate-900"
                                    : "border-slate-200 bg-white text-slate-500 hover:border-slate-300 hover:text-slate-800",
                                )}
                                onClick={() => {
                                  setExpandedMetadataChunkIds((current) => {
                                    const next = new Set(current);
                                    if (next.has(chunk.id)) {
                                      next.delete(chunk.id);
                                    } else {
                                      next.add(chunk.id);
                                    }
                                    return next;
                                  });
                                }}
                                title={metadataExpanded ? "Hide metadata" : "Show metadata"}
                                type="button"
                              >
                                <Database className="h-4 w-4" />
                              </button>
                            ) : null}
                          </div>
                        </div>
                        <p className="whitespace-pre-wrap text-sm leading-6 text-slate-700">
                          {chunk.content}
                        </p>
                        {hasMetadata && metadataExpanded ? (
                          <pre className="mt-3 overflow-auto rounded-lg bg-slate-50 p-3 text-xs text-slate-600">
                            {JSON.stringify(chunk.metadata, null, 2)}
                          </pre>
                        ) : null}
                      </article>
                    );
                  })
                )}
              </div>
            ) : null}

            {activeTab === "embed" ? (
              <div className="space-y-3">
                <div className="rounded-xl border border-slate-200 bg-slate-50 p-4">
                  <dl className="grid gap-3 text-xs text-slate-600 sm:grid-cols-3">
                    <QueueMetric label="Status" value={document.status} />
                    <QueueMetric label="Stored chunks" value={document.chunk_count.toLocaleString()} />
                    <QueueMetric
                      label="Indexed chunks"
                      value={
                        document.vector_indexed_count === null
                          ? "--"
                          : document.vector_indexed_count.toLocaleString()
                      }
                    />
                  </dl>
                </div>
                <PipelineLogList logs={vectorLogs} />
              </div>
            ) : null}

            {activeTab === "access" ? (
              <div className="space-y-4">
                <div className="rounded-xl border border-slate-200 bg-white p-4">
                  <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
                    <div>
                      <p className="text-xs font-semibold uppercase tracking-wider text-slate-500">
                        Document access
                      </p>
                      <p className="mt-1 text-sm text-slate-600">
                        Update company, role, group, and user access for this file.
                      </p>
                    </div>
                    <Button
                      className="h-9 bg-[#0d3b4c] text-white hover:bg-[#114e63]"
                      disabled={accessLoading || accessSaving}
                      onClick={() => void handleSaveAccess()}
                      type="button"
                    >
                      {accessSaving ? (
                        <Loader2 className="h-4 w-4 animate-spin" />
                      ) : (
                        <ShieldCheck className="h-4 w-4" />
                      )}
                      Save access
                    </Button>
                  </div>

                  {accessLoading ? (
                    <div className="flex min-h-32 items-center justify-center gap-3 text-sm text-slate-500">
                      <Loader2 className="h-4 w-4 animate-spin text-cyan-700" />
                      Loading access policy...
                    </div>
                  ) : (
                    <div className="space-y-4">
                      <div className="grid gap-3 md:grid-cols-2">
                        <label className="block">
                          <span className="text-xs font-semibold uppercase tracking-wider text-slate-500">
                            Scope
                          </span>
                          <select
                            className="mt-2 h-10 w-full rounded-lg border border-slate-200 bg-white px-3 text-sm text-slate-700"
                            onChange={(event) => handleAccessScalarChange("scope", event.target.value)}
                            value={accessPolicy.scope ?? "unit_only"}
                          >
                            <option value="unit_only">Unit only</option>
                            <option value="subtree">Subtree</option>
                            <option value="corp_wide">Corp wide</option>
                            <option value="public_internal">Public internal</option>
                            <option value="explicit_acl">Explicit ACL</option>
                            <option value="functional_vertical">Functional vertical</option>
                            <option value="project_only">Project only</option>
                            <option value="leadership_only">Leadership only</option>
                          </select>
                        </label>
                        <label className="block">
                          <span className="text-xs font-semibold uppercase tracking-wider text-slate-500">
                            Classification
                          </span>
                          <select
                            className="mt-2 h-10 w-full rounded-lg border border-slate-200 bg-white px-3 text-sm text-slate-700"
                            onChange={(event) => handleAccessScalarChange("classification", event.target.value)}
                            value={accessPolicy.classification ?? "internal"}
                          >
                            <option value="public_internal">Public internal</option>
                            <option value="internal">Internal</option>
                            <option value="restricted">Restricted</option>
                            <option value="personal_data">Personal data</option>
                            <option value="confidential">Confidential</option>
                            <option value="secret">Secret</option>
                          </select>
                        </label>
                      </div>

                      <div className="grid gap-3 md:grid-cols-2">
                        <label className="block">
                          <span className="text-xs font-semibold uppercase tracking-wider text-slate-500">
                            Owner company ID
                          </span>
                          <AccessSelectField
                            className="mt-2"
                            onChange={(value) => handleAccessScalarChange("owner_org_id", value)}
                            options={mergeOptionsWithSelected(
                              organizationOptions,
                              accessPolicy.owner_org_id ? [accessPolicy.owner_org_id] : [],
                            )}
                            placeholder="No owner company"
                            value={accessPolicy.owner_org_id ?? ""}
                          />
                        </label>
                        <AccessTextField
                          label="Owner org path"
                          onChange={(value) => handleAccessScalarChange("owner_org_path", value)}
                          placeholder="EVNCPC/PC_DANANG"
                          value={accessPolicy.owner_org_path ?? ""}
                        />
                      </div>

                      <div className="grid gap-3 md:grid-cols-2">
                        <AccessMultiSelectBlock
                          label="Allowed company IDs"
                          onChange={(value) => handleAccessListSelect("allowed_org_ids", value)}
                          options={mergeOptionsWithSelected(
                            organizationOptions,
                            accessPolicy.allowed_org_ids,
                          )}
                          value={accessPolicy.allowed_org_ids}
                        />
                        <AccessMultiSelectBlock
                          label="Denied company IDs"
                          onChange={(value) => handleAccessListSelect("denied_org_ids", value)}
                          options={mergeOptionsWithSelected(
                            organizationOptions,
                            accessPolicy.denied_org_ids,
                          )}
                          value={accessPolicy.denied_org_ids}
                        />
                        <AccessMultiSelectBlock
                          label="Allowed roles"
                          onChange={(value) => handleAccessListSelect("allowed_role_names", value)}
                          options={mergeOptionsWithSelected(
                            roleOptions,
                            accessPolicy.allowed_role_names,
                          )}
                          value={accessPolicy.allowed_role_names}
                        />
                        <AccessMultiSelectBlock
                          label="Denied roles"
                          onChange={(value) => handleAccessListSelect("denied_role_names", value)}
                          options={mergeOptionsWithSelected(
                            roleOptions,
                            accessPolicy.denied_role_names,
                          )}
                          value={accessPolicy.denied_role_names}
                        />
                        {groupOptions.length > 0 ? (
                          <AccessMultiSelectBlock
                            label="Allowed groups"
                            onChange={(value) => handleAccessListSelect("allowed_group_codes", value)}
                            options={mergeOptionsWithSelected(
                              groupOptions,
                              accessPolicy.allowed_group_codes,
                            )}
                            value={accessPolicy.allowed_group_codes}
                          />
                        ) : (
                          <AccessTextField
                            label="Allowed groups"
                            onChange={(value) => handleAccessListChange("allowed_group_codes", value)}
                            placeholder="ai-team, legal-team"
                            value={joinListInput(accessPolicy.allowed_group_codes)}
                          />
                        )}
                        {groupOptions.length > 0 ? (
                          <AccessMultiSelectBlock
                            label="Denied groups"
                            onChange={(value) => handleAccessListSelect("denied_group_codes", value)}
                            options={mergeOptionsWithSelected(
                              groupOptions,
                              accessPolicy.denied_group_codes,
                            )}
                            value={accessPolicy.denied_group_codes}
                          />
                        ) : (
                          <AccessTextField
                            label="Denied groups"
                            onChange={(value) => handleAccessListChange("denied_group_codes", value)}
                            placeholder="external"
                            value={joinListInput(accessPolicy.denied_group_codes)}
                          />
                        )}
                        <AccessTextField
                          label="Allowed users"
                          onChange={(value) => handleAccessListChange("allowed_user_ids", value)}
                          placeholder="user UUID1, user UUID2"
                          value={joinListInput(accessPolicy.allowed_user_ids)}
                        />
                        <AccessTextField
                          label="Denied users"
                          onChange={(value) => handleAccessListChange("denied_user_ids", value)}
                          placeholder="user UUID1, user UUID2"
                          value={joinListInput(accessPolicy.denied_user_ids)}
                        />
                        <AccessTextField
                          label="Business domains"
                          onChange={(value) => handleAccessListChange("business_domains", value)}
                          placeholder="kinh_doanh, ky_thuat"
                          value={joinListInput(accessPolicy.business_domains)}
                        />
                        <AccessTextField
                          label="Project codes"
                          onChange={(value) => handleAccessListChange("project_codes", value)}
                          placeholder="project-a, project-b"
                          value={joinListInput(accessPolicy.project_codes)}
                        />
                      </div>

                      <label className="inline-flex cursor-pointer items-center gap-2 text-sm text-slate-700">
                        <input
                          checked={accessPolicy.inherit_permission}
                          className="h-4 w-4 rounded border-slate-300 text-cyan-700"
                          onChange={(event) =>
                            setAccessPolicy((current) => ({
                              ...current,
                              inherit_permission: event.target.checked,
                            }))
                          }
                          type="checkbox"
                        />
                        Inherit document-level permissions to chunks
                      </label>
                    </div>
                  )}

                  {accessMessage ? (
                    <p
                      className={cn(
                        "mt-3 rounded-lg border px-3 py-2 text-sm",
                        accessMessage.type === "error"
                          ? "border-rose-200 bg-rose-50 text-rose-700"
                          : "border-emerald-200 bg-emerald-50 text-emerald-700",
                      )}
                    >
                      {accessMessage.text}
                    </p>
                  ) : null}
                </div>
              </div>
            ) : null}
          </div>
        )}
      </div>
    </div>
  );
}

function AccessTextField({
  label,
  onChange,
  placeholder,
  value,
}: {
  label: string;
  onChange: (value: string) => void;
  placeholder: string;
  value: string;
}) {
  return (
    <label className="block">
      <span className="text-xs font-semibold uppercase tracking-wider text-slate-500">
        {label}
      </span>
      <Input
        className="mt-2"
        onChange={(event) => onChange(event.target.value)}
        placeholder={placeholder}
        value={value}
      />
    </label>
  );
}

function AccessSelectField({
  className,
  onChange,
  options,
  placeholder,
  value,
}: {
  className?: string;
  onChange: (value: string) => void;
  options: AccessSelectOption[];
  placeholder: string;
  value: string;
}) {
  return (
    <select
      className={cn(
        "h-10 w-full rounded-lg border border-slate-200 bg-white px-3 text-sm text-slate-700",
        className,
      )}
      onChange={(event) => onChange(event.target.value)}
      value={value}
    >
      <option value="">{placeholder}</option>
      {options.map((option) => (
        <option key={option.value} value={option.value}>
          {option.label}
        </option>
      ))}
    </select>
  );
}

function AccessMultiSelectBlock({
  label,
  onChange,
  options,
  value,
}: {
  label: string;
  onChange: (value: string[]) => void;
  options: AccessSelectOption[];
  value: string[];
}) {
  return (
    <label className="block">
      <span className="text-xs font-semibold uppercase tracking-wider text-slate-500">
        {label}
      </span>
      <AccessMultiSelectField
        className="mt-2"
        onChange={onChange}
        options={options}
        value={value}
      />
    </label>
  );
}

function AccessMultiSelectField({
  className,
  onChange,
  options,
  value,
}: {
  className?: string;
  onChange: (value: string[]) => void;
  options: AccessSelectOption[];
  value: string[];
}) {
  return (
    <select
      className={cn(
        "min-h-24 w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700",
        className,
      )}
      multiple
      onChange={(event) =>
        onChange(
          Array.from(event.currentTarget.selectedOptions, (option) => option.value),
        )
      }
      size={Math.min(Math.max(options.length, 3), 6)}
      value={value}
    >
      {options.map((option) => (
        <option key={option.value} value={option.value}>
          {option.label}
        </option>
      ))}
    </select>
  );
}

function joinListInput(value: string[] | undefined): string {
  return (value ?? []).join(", ");
}

function buildOrganizationOptions(
  catalog: AccessCatalogResponse | null,
): AccessSelectOption[] {
  return (catalog?.organizations ?? []).map((organization) => ({
    value: organization.id,
    label: `${organization.ma_dviqly} - ${organization.ten_dviqly}`,
  }));
}

function buildRoleOptions(catalog: AccessCatalogResponse | null): AccessSelectOption[] {
  return (catalog?.roles ?? []).map((role) => ({
    value: role.name,
    label: role.description ? `${role.name} - ${role.description}` : role.name,
  }));
}

function buildGroupOptions(catalog: AccessCatalogResponse | null): AccessSelectOption[] {
  return (catalog?.groups ?? []).map((group) => ({ value: group, label: group }));
}

function mergeOptionsWithSelected(
  options: AccessSelectOption[],
  selected: string[] | undefined,
): AccessSelectOption[] {
  const seen = new Set(options.map((option) => option.value));
  const missingOptions = (selected ?? [])
    .filter((value) => value && !seen.has(value))
    .map((value) => ({ value, label: value }));
  return [...options, ...missingOptions];
}

function splitListInput(value: string): string[] {
  return value
    .replace(/;/g, ",")
    .replace(/\|/g, ",")
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}

function PipelineLogList({ logs }: { logs: DocumentDetailResponse["pipeline_logs"] }) {
  if (logs.length === 0) {
    return <EmptyState message="No pipeline logs for this tab." />;
  }

  return (
    <div className="space-y-2">
      {logs.map((log) => (
        <article
          className="rounded-xl border border-slate-200 bg-white p-3"
          key={`${log.action}-${log.created_at}`}
        >
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="flex items-center gap-2">
              <span className="text-sm font-semibold text-slate-800">{log.action}</span>
              <StatusBadge state={normalizeState(log.status)} compact />
            </div>
            <span className="text-xs text-slate-500">{formatDateTime(log.created_at)}</span>
          </div>
          {log.message ? <p className="mt-2 text-sm text-slate-600">{log.message}</p> : null}
          {log.metadata ? (
            <pre className="mt-3 overflow-auto rounded-lg bg-slate-50 p-3 text-xs text-slate-600">
              {JSON.stringify(log.metadata, null, 2)}
            </pre>
          ) : null}
        </article>
      ))}
    </div>
  );
}

function ChatView({
  answer,
  asking,
  citations,
  citationDocuments,
  onAsk,
  onCitationClick,
  onQuestionChange,
  question,
  selectedCitationIndex,
  sessionId,
}: {
  answer: string;
  asking: boolean;
  citations: RagCitation[];
  citationDocuments: Record<string, DocumentDetailResponse>;
  onAsk: (event: FormEvent<HTMLFormElement>) => void;
  onCitationClick: (citationIndex: number) => void;
  onQuestionChange: (question: string) => void;
  question: string;
  selectedCitationIndex: number | null;
  sessionId: string | null;
}) {
  return (
    <div className="space-y-5">
        <Card className="bg-white shadow-sm">
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <MessageSquareText className="h-5 w-5 text-cyan-700" />
              Grounded Chat
            </CardTitle>
            <CardDescription>
              Ask against indexed context and verify citations.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <form className="space-y-4" onSubmit={onAsk}>
              <label className="block">
                <span className="text-xs font-semibold uppercase tracking-wider text-slate-500">
                  Question
                </span>
                <Textarea
                  className="mt-2 min-h-32 border-slate-200 bg-white text-sm shadow-none focus-visible:ring-cyan-700"
                  onChange={(event) => onQuestionChange(event.target.value)}
                  placeholder="Ask about indexed documents..."
                  value={question}
                />
              </label>
              <div className="flex items-center justify-between gap-3">
                <span className="font-mono text-xs text-slate-500">
                  Session {sessionId ? compactId(sessionId) : "new"}
                </span>
                <Button
                  className="bg-[#0d3b4c] text-white hover:bg-[#114e63]"
                  disabled={!question.trim() || asking}
                  type="submit"
                >
                  {asking ? (
                    <Loader2 className="h-4 w-4 animate-spin" />
                  ) : (
                    <Send className="h-4 w-4" />
                  )}
                  {asking ? "Generating..." : "Ask"}
                </Button>
              </div>
            </form>
          </CardContent>
        </Card>

        <ChatAnswerPanel
          answer={answer}
          asking={asking}
          citationDocuments={citationDocuments}
          citations={citations}
          onCitationClick={onCitationClick}
          selectedCitationIndex={selectedCitationIndex}
        />
    </div>
  );
}

function MemoryView({
  busy,
  draft,
  draftType,
  items,
  memorySettings,
  onAdd,
  onDelete,
  onDraftChange,
  onDraftTypeChange,
  onRefresh,
}: {
  busy: boolean;
  draft: string;
  draftType: MemoryType;
  items: MemoryItem[];
  memorySettings: MemorySettings | null;
  onAdd: (event: FormEvent<HTMLFormElement>) => void;
  onDelete: (memoryId: string) => void;
  onDraftChange: (value: string) => void;
  onDraftTypeChange: (value: MemoryType) => void;
  onRefresh: () => void;
}) {
  const memoryTypes: MemoryType[] = [
    "preference",
    "task",
    "entity",
    "instruction",
    "fact",
  ];

  return (
    <div className="grid gap-5 xl:grid-cols-[420px_minmax(0,1fr)]">
      <Card className="bg-white shadow-sm">
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Brain className="h-5 w-5 text-cyan-700" />
            Add Memory
          </CardTitle>
          <CardDescription>
            Stored memories are private to your account and used as chat context only.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="rounded-xl bg-slate-50 p-3 text-xs text-slate-500">
            Provider{" "}
            <span className="font-mono text-slate-700">
              {memorySettings?.memory_provider ?? "unknown"}
            </span>{" "}
            · Mem0 {memorySettings?.mem0_enabled ? "enabled" : "disabled"}
          </div>
          <form className="space-y-4" onSubmit={onAdd}>
            <label className="block">
              <span className="text-xs font-semibold uppercase tracking-wider text-slate-500">
                Memory type
              </span>
              <div className="mt-2 flex flex-wrap gap-2">
                {memoryTypes.map((type) => (
                  <button
                    className={cn(
                      "cursor-pointer rounded-lg border px-3 py-1.5 text-sm font-medium capitalize transition-colors",
                      draftType === type
                        ? "border-cyan-300 bg-cyan-50 text-cyan-800"
                        : "border-slate-200 bg-white text-slate-600 hover:bg-slate-50",
                    )}
                    key={type}
                    onClick={() => onDraftTypeChange(type)}
                    type="button"
                  >
                    {type}
                  </button>
                ))}
              </div>
            </label>
            <label className="block">
              <span className="text-xs font-semibold uppercase tracking-wider text-slate-500">
                Content
              </span>
              <Textarea
                className="mt-2 min-h-28 border-slate-200 bg-white text-sm shadow-none focus-visible:ring-cyan-700"
                onChange={(event) => onDraftChange(event.target.value)}
                placeholder="e.g. Always answer in Vietnamese."
                value={draft}
              />
            </label>
            <Button
              className="w-full bg-[#0d3b4c] text-white hover:bg-[#114e63]"
              disabled={!draft.trim() || busy}
              type="submit"
            >
              {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Brain className="h-4 w-4" />}
              Save memory
            </Button>
          </form>
        </CardContent>
      </Card>

      <Card className="bg-white shadow-sm">
        <CardHeader>
          <div className="flex items-start justify-between gap-4">
            <div>
              <CardTitle>Your Memories</CardTitle>
              <CardDescription>{items.length} stored items.</CardDescription>
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
        <CardContent>
          <div className="space-y-3">
            {items.length === 0 ? (
              <EmptyState message="No memories stored yet." />
            ) : (
              items.map((item) => (
                <div
                  className="rounded-xl border border-slate-100 bg-slate-50 p-4"
                  key={item.id ?? item.content}
                >
                  <div className="flex items-center justify-between gap-3">
                    <span className="inline-flex items-center gap-2 text-sm font-semibold text-slate-800">
                      <span className="rounded bg-cyan-50 px-2 py-0.5 text-xs font-medium capitalize text-cyan-800">
                        {item.memory_type}
                      </span>
                      <span className="text-xs font-normal text-slate-400">
                        {item.source}
                      </span>
                    </span>
                    {item.id ? (
                      <button
                        className="cursor-pointer text-xs font-medium text-rose-600 hover:text-rose-700"
                        onClick={() => onDelete(item.id as string)}
                        type="button"
                      >
                        Remove
                      </button>
                    ) : null}
                  </div>
                  <p className="mt-3 text-sm leading-6 text-slate-700">
                    {item.content}
                  </p>
                </div>
              ))
            )}
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

function stringifyProfileConfig(config: Record<string, unknown> | null | undefined) {
  return JSON.stringify(config ?? {}, null, 2);
}

function parseProfileDraft(draft: string): ProfileConfig {
  const parsed = JSON.parse(draft) as unknown;
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error("Profile config must be a JSON object.");
  }
  return parsed as ProfileConfig;
}

function parseProfileDraftSafe(draft: string): ProfileConfig | null {
  try {
    return parseProfileDraft(draft);
  } catch {
    return null;
  }
}

function profileRecord(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return {};
  }
  return value as Record<string, unknown>;
}

function stringifyQueryIntentRules(config: ProfileConfig | null | undefined) {
  return stringifyProfileConfig(profileRecord(config?.query_intent_rules));
}

function ProfileConfigPanel() {
  const [profiles, setProfiles] = useState<string[]>([]);
  const [configs, setConfigs] = useState<Record<string, ProfileConfig>>({});
  const [selectedProfile, setSelectedProfile] = useState("");
  const [profileDraft, setProfileDraft] = useState("");
  const [queryIntentRulesDraft, setQueryIntentRulesDraft] = useState("");
  const [queryIntentRulesError, setQueryIntentRulesError] = useState<string | null>(null);
  const [profileLoading, setProfileLoading] = useState(true);
  const [profileSaving, setProfileSaving] = useState(false);
  const [profileTesting, setProfileTesting] = useState(false);
  const [profileMessage, setProfileMessage] = useState<{
    type: "success" | "error";
    text: string;
  } | null>(null);
  const [headingSample, setHeadingSample] = useState("");
  const [headingMatches, setHeadingMatches] = useState<HeadingRuleTestMatch[]>([]);

  const loadProfiles = useCallback(async () => {
    setProfileLoading(true);
    setProfileMessage(null);
    try {
      const response = await getProfiles();
      const concreteProfiles = response.profiles.filter((profile) => profile !== "auto");
      const nextProfile = concreteProfiles.includes(selectedProfile)
        ? selectedProfile
        : concreteProfiles[0] ?? "";
      setProfiles(concreteProfiles);
      setConfigs(response.configs);
      setSelectedProfile(nextProfile);
      setProfileDraft(stringifyProfileConfig(response.configs[nextProfile]));
      setQueryIntentRulesDraft(stringifyQueryIntentRules(response.configs[nextProfile]));
      setQueryIntentRulesError(null);
    } catch (error) {
      setProfileMessage({ type: "error", text: getErrorMessage(error) });
    } finally {
      setProfileLoading(false);
    }
  }, [selectedProfile]);

  useEffect(() => {
    void loadProfiles();
  }, [loadProfiles]);

  const draftConfig = parseProfileDraftSafe(profileDraft) ?? configs[selectedProfile];

  const handleProfileSelect = (profile: string) => {
    setSelectedProfile(profile);
    setProfileDraft(stringifyProfileConfig(configs[profile]));
    setQueryIntentRulesDraft(stringifyQueryIntentRules(configs[profile]));
    setQueryIntentRulesError(null);
    setHeadingMatches([]);
    setProfileMessage(null);
  };

  const updateDraftField = (key: keyof ProfileConfig, value: unknown) => {
    const current = parseProfileDraftSafe(profileDraft) ?? configs[selectedProfile] ?? {};
    const nextConfig = { ...current, [key]: value };
    setProfileDraft(stringifyProfileConfig(nextConfig));
    if (key === "query_intent_rules") {
      setQueryIntentRulesDraft(stringifyProfileConfig(profileRecord(value)));
      setQueryIntentRulesError(null);
    }
    setProfileMessage(null);
  };

  const handleProfileDraftChange = (value: string) => {
    setProfileDraft(value);
    setProfileMessage(null);
    const parsed = parseProfileDraftSafe(value);
    if (parsed) {
      setQueryIntentRulesDraft(stringifyQueryIntentRules(parsed));
      setQueryIntentRulesError(null);
    }
  };

  const handleQueryIntentRulesDraftChange = (value: string) => {
    setQueryIntentRulesDraft(value);
    setProfileMessage(null);
    try {
      const parsed = JSON.parse(value) as unknown;
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
        throw new Error("query_intent_rules must be a JSON object.");
      }
      const current = parseProfileDraftSafe(profileDraft) ?? configs[selectedProfile] ?? {};
      setProfileDraft(stringifyProfileConfig({ ...current, query_intent_rules: parsed }));
      setQueryIntentRulesError(null);
    } catch {
      setQueryIntentRulesError("query_intent_rules must be a JSON object.");
    }
  };

  const handleSaveProfile = async () => {
    if (!selectedProfile) {
      return;
    }
    if (queryIntentRulesError) {
      setProfileMessage({ type: "error", text: queryIntentRulesError });
      return;
    }
    setProfileSaving(true);
    setProfileMessage(null);
    try {
      const parsed = parseProfileDraft(profileDraft);
      const response = await updateProfileConfig(selectedProfile, parsed);
      setConfigs(response.configs);
      setProfileDraft(stringifyProfileConfig(response.configs[selectedProfile]));
      setQueryIntentRulesDraft(stringifyQueryIntentRules(response.configs[selectedProfile]));
      setQueryIntentRulesError(null);
      setProfileMessage({
        type: "success",
        text: `Saved ${selectedProfile} to Postgres.`,
      });
    } catch (error) {
      setProfileMessage({ type: "error", text: getErrorMessage(error) });
    } finally {
      setProfileSaving(false);
    }
  };

  const handleTestHeadingRules = async () => {
    if (!selectedProfile) {
      return;
    }
    setProfileTesting(true);
    setProfileMessage(null);
    try {
      const parsed = parseProfileDraft(profileDraft);
      const response = await testHeadingRules({
        profile: selectedProfile,
        sample_text: headingSample,
        config: parsed,
      });
      setHeadingMatches(response.matches);
    } catch (error) {
      setProfileMessage({ type: "error", text: getErrorMessage(error) });
    } finally {
      setProfileTesting(false);
    }
  };

  const numberFields: Array<{ key: keyof ProfileConfig; label: string }> = [
    { key: "chunk_size", label: "chunk_size" },
    { key: "chunk_overlap", label: "chunk_overlap" },
    { key: "top_k", label: "top_k" },
    { key: "candidate_k", label: "candidate_k" },
    { key: "max_context_chars", label: "max_context_chars" },
  ];

  return (
    <div className="space-y-4 rounded-xl border border-slate-200 p-4">
      <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
        <div>
          <div className="flex items-center gap-2">
            <p className="text-xs font-semibold uppercase tracking-wider text-slate-500">
              Ingestion profiles
            </p>
            <span className="rounded bg-emerald-50 px-2 py-0.5 text-xs font-medium text-emerald-700">
              Postgres
            </span>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <select
            className="h-10 cursor-pointer rounded-md border border-slate-200 bg-white px-3 text-sm font-medium text-slate-700 focus:outline-none focus:ring-2 focus:ring-cyan-500"
            disabled={profileLoading || profiles.length === 0}
            onChange={(event) => handleProfileSelect(event.target.value)}
            value={selectedProfile}
          >
            {profiles.map((profile) => (
              <option key={profile} value={profile}>
                {profile}
              </option>
            ))}
          </select>
          <Button
            className="cursor-pointer border-slate-200 bg-white text-slate-700 hover:bg-slate-50"
            disabled={profileLoading}
            onClick={() => void loadProfiles()}
            type="button"
            variant="outline"
          >
            <RefreshCw className={cn("h-4 w-4", profileLoading && "animate-spin")} />
            Reload
          </Button>
          <Button
            className="cursor-pointer bg-cyan-700 text-white hover:bg-cyan-800"
            disabled={!selectedProfile || profileSaving || Boolean(queryIntentRulesError)}
            onClick={() => void handleSaveProfile()}
            type="button"
          >
            {profileSaving ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <CheckCircle2 className="h-4 w-4" />
            )}
            Save
          </Button>
        </div>
      </div>

      {profileMessage ? (
        <div
          className={cn(
            "flex items-center gap-2 rounded-lg px-3 py-2 text-sm",
            profileMessage.type === "success"
              ? "bg-emerald-50 text-emerald-700"
              : "bg-rose-50 text-rose-700",
          )}
        >
          {profileMessage.type === "success" ? (
            <CheckCircle2 className="h-4 w-4" />
          ) : (
            <AlertCircle className="h-4 w-4" />
          )}
          {profileMessage.text}
        </div>
      ) : null}

      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
        <label className="space-y-1.5 text-sm font-medium text-slate-700">
          <span>chunk_mode</span>
          <select
            className="h-10 w-full cursor-pointer rounded-md border border-slate-200 bg-white px-3 text-sm text-slate-700 focus:outline-none focus:ring-2 focus:ring-cyan-500"
            onChange={(event) =>
              updateDraftField("chunk_mode", event.target.value as ProfileConfig["chunk_mode"])
            }
            value={draftConfig?.chunk_mode ?? "recursive"}
          >
            {chunkModeOptions.map((mode) => (
              <option key={mode} value={mode}>
                {mode}
              </option>
            ))}
          </select>
        </label>

        <label className="space-y-1.5 text-sm font-medium text-slate-700">
          <span>answer_mode</span>
          <select
            className="h-10 w-full cursor-pointer rounded-md border border-slate-200 bg-white px-3 text-sm text-slate-700 focus:outline-none focus:ring-2 focus:ring-cyan-500"
            onChange={(event) =>
              updateDraftField("answer_mode", event.target.value as ProfileConfig["answer_mode"])
            }
            value={draftConfig?.answer_mode ?? "hybrid"}
          >
            {answerModeOptions.map((mode) => (
              <option key={mode} value={mode}>
                {mode}
              </option>
            ))}
          </select>
        </label>

        <label className="space-y-1.5 text-sm font-medium text-slate-700">
          <span>answer_style</span>
          <select
            className="h-10 w-full cursor-pointer rounded-md border border-slate-200 bg-white px-3 text-sm text-slate-700 focus:outline-none focus:ring-2 focus:ring-cyan-500"
            onChange={(event) =>
              updateDraftField("answer_style", event.target.value as ProfileConfig["answer_style"])
            }
            value={draftConfig?.answer_style ?? "detailed"}
          >
            {answerStyleOptions.map((style) => (
              <option key={style} value={style}>
                {style}
              </option>
            ))}
          </select>
        </label>

        {numberFields.map((field) => (
          <label
            className="space-y-1.5 text-sm font-medium text-slate-700"
            key={field.key}
          >
            <span>{field.label}</span>
            <Input
              min={0}
              onChange={(event) =>
                updateDraftField(field.key, Number(event.target.value || 0))
              }
              type="number"
              value={Number(draftConfig?.[field.key] ?? 0)}
            />
          </label>
        ))}
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <label className="block space-y-2 text-sm font-medium text-slate-700">
          <span className="inline-flex items-center gap-2">
            <TerminalSquare className="h-4 w-4 text-slate-500" />
            query_intent_rules
          </span>
          <Textarea
            className="min-h-[220px] font-mono text-xs leading-5"
            onChange={(event) => handleQueryIntentRulesDraftChange(event.target.value)}
            spellCheck={false}
            value={queryIntentRulesDraft}
          />
          {queryIntentRulesError ? (
            <p className="text-xs font-medium text-rose-600">{queryIntentRulesError}</p>
          ) : null}
        </label>

        <label className="block space-y-2 text-sm font-medium text-slate-700">
          <span className="inline-flex items-center gap-2">
            <TerminalSquare className="h-4 w-4 text-slate-500" />
            Profile JSON
          </span>
          <Textarea
            className="min-h-[220px] font-mono text-xs leading-5"
            onChange={(event) => handleProfileDraftChange(event.target.value)}
            spellCheck={false}
            value={profileDraft}
          />
        </label>
      </div>

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(280px,0.7fr)]">
        <label className="block space-y-2 text-sm font-medium text-slate-700">
          <span>Heading rule sample</span>
          <Textarea
            className="min-h-[130px]"
            onChange={(event) => setHeadingSample(event.target.value)}
            value={headingSample}
          />
        </label>
        <div className="space-y-3 rounded-lg bg-slate-50 p-3">
          <Button
            className="w-full cursor-pointer border-slate-200 bg-white text-slate-700 hover:bg-slate-100"
            disabled={!selectedProfile || profileTesting}
            onClick={() => void handleTestHeadingRules()}
            type="button"
            variant="outline"
          >
            {profileTesting ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Play className="h-4 w-4" />
            )}
            Test rules
          </Button>
          {headingMatches.length === 0 ? (
            <EmptyState message="No heading matches yet." />
          ) : (
            <div className="max-h-44 space-y-2 overflow-auto pr-1">
              {headingMatches.map((match, index) => (
                <div
                  className="rounded-md border border-slate-200 bg-white px-3 py-2 text-xs text-slate-600"
                  key={`${match.start}-${match.end}-${index}`}
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-semibold text-slate-800">{match.name}</span>
                    <span className="rounded bg-cyan-50 px-2 py-0.5 text-cyan-700">
                      level {match.level}
                    </span>
                  </div>
                  <p className="mt-1 truncate">{match.display_text}</p>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function SettingsPanel({
  memorySettings,
  memoryTopK,
  runtimeConfig,
  setMemoryTopK,
  setStreamingEnabled,
  setTypewriterEnabled,
  setTypewriterSpeed,
  setUseMem0,
  setUseGraph,
  adminViewAll,
  setAdminViewAll,
  setUseMemory,
  streamingEnabled,
  typewriterEnabled,
  typewriterSpeed,
  useGraph,
  useMem0,
  useMemory,
  setGraphExpansionDepth,
  setGraphExpansionLimit,
  graphExpansionDepth,
  graphExpansionLimit,
}: {
  memorySettings: MemorySettings | null;
  memoryTopK: number;
  runtimeConfig: RuntimeConfigResponse | null;
  setMemoryTopK: (value: number) => void;
  setStreamingEnabled: (value: boolean) => void;
  setTypewriterEnabled: (value: boolean) => void;
  setTypewriterSpeed: (value: TypewriterSpeed) => void;
  setUseMem0: (value: boolean) => void;
  setUseGraph: (value: boolean) => void;
  adminViewAll: boolean;
  setAdminViewAll: (value: boolean) => void;
  setUseMemory: (value: boolean) => void;
  streamingEnabled: boolean;
  typewriterEnabled: boolean;
  typewriterSpeed: TypewriterSpeed;
  useGraph: boolean;
  useMem0: boolean;
  useMemory: boolean;
  setGraphExpansionDepth: (value: number) => void;
  setGraphExpansionLimit: (value: number) => void;
  graphExpansionDepth: number;
  graphExpansionLimit: number;
}) {
  const memoryEnabled = memorySettings?.memory_enabled ?? false;
  const mem0Available = memorySettings?.mem0_enabled ?? false;
  const streamingSupported = runtimeConfig?.streaming_supported ?? true;
  const modelRows = runtimeConfig
    ? [
        ["Embedding model", runtimeConfig.embedding_model ?? "Not set"],
        ["Reranker model", runtimeConfig.reranker_model ?? "Not set"],
        ["LLM model", runtimeConfig.llm_model ?? "Not set"],
        ["Embedding dimension", String(runtimeConfig.embedding_dimension)],
      ]
    : [];

  return (
    <Card className="bg-white shadow-sm">
      <CardHeader className="pb-3">
        <CardTitle className="flex items-center gap-2">
          <ServerCog className="h-5 w-5 text-cyan-700" />
          RAG Settings
        </CardTitle>
        <CardDescription>
          Retrieval, answer style, context limits, and ingestion profiles are resolved automatically by the backend.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-5">
        <div className="rounded-xl border border-cyan-100 bg-cyan-50 px-4 py-3 text-sm text-cyan-900">
          Backend auto-detects a concrete ingestion profile during parsing, stores
          the resolved profile with detection evidence, then reuses it for
          retrieval and answer formatting.
        </div>

        <ProfileConfigPanel />

        <label
          className={cn(
            "flex items-center justify-between gap-3 rounded-xl border border-slate-200 bg-slate-50 px-4 py-3",
            !streamingSupported && "opacity-60",
          )}
        >
          <span>
            <span className="block text-sm font-semibold text-slate-800">
              Streaming answers
            </span>
            <span className="block text-xs text-slate-500">
              {streamingSupported
                ? "Stream tokens as they are generated."
                : "Streaming is not supported by the backend."}
            </span>
          </span>
          <input
            checked={streamingEnabled && streamingSupported}
            className="h-5 w-5 cursor-pointer accent-cyan-600"
            disabled={!streamingSupported}
            onChange={(event) => setStreamingEnabled(event.target.checked)}
            type="checkbox"
          />
        </label>

        <label
          className={cn(
            "flex items-center justify-between gap-3 rounded-xl border border-slate-200 bg-slate-50 px-4 py-3",
            !(streamingEnabled && streamingSupported) && "opacity-60",
          )}
        >
          <span>
            <span className="block text-sm font-semibold text-slate-800">
              Typewriter rendering
            </span>
            <span className="block text-xs text-slate-500">
              Reveal streamed text gradually instead of in blocks.
            </span>
          </span>
          <input
            checked={typewriterEnabled}
            className="h-5 w-5 cursor-pointer accent-cyan-600"
            disabled={!(streamingEnabled && streamingSupported)}
            onChange={(event) => setTypewriterEnabled(event.target.checked)}
            type="checkbox"
          />
        </label>

        <div>
          <span className="text-xs font-semibold uppercase tracking-wider text-slate-500">
            Typewriter speed
          </span>
          <div className="mt-2 inline-flex rounded-lg bg-slate-100 p-1">
            {(["slow", "normal", "fast"] as const).map((speed) => (
              <button
                className={cn(
                  "cursor-pointer rounded-md px-3 py-1.5 text-sm font-medium capitalize transition-colors disabled:cursor-not-allowed disabled:opacity-50",
                  typewriterSpeed === speed
                    ? "bg-white text-slate-900 shadow-sm"
                    : "text-slate-500 hover:text-slate-800",
                )}
                disabled={!typewriterEnabled}
                key={speed}
                onClick={() => setTypewriterSpeed(speed)}
                type="button"
              >
                {speed}
              </button>
            ))}
          </div>
        </div>

        <div className="rounded-xl bg-slate-50 p-4">
          <p className="mb-3 text-xs font-semibold uppercase tracking-wider text-slate-500">
            Model configuration
          </p>
          {runtimeConfig ? (
            <dl className="space-y-2">
              {modelRows.map(([label, value]) => (
                <KeyValue key={label} label={label} value={value} />
              ))}
            </dl>
          ) : (
            <EmptyState message="Runtime config has not loaded." />
          )}
        </div>

        <div className="space-y-4 rounded-xl border border-slate-200 p-4">
          <div className="flex items-center justify-between">
            <p className="text-xs font-semibold uppercase tracking-wider text-slate-500">
              GraphRAG
            </p>
            <span className="inline-flex items-center gap-2 text-xs text-slate-500">
              <span className="rounded bg-slate-100 px-2 py-0.5 font-mono text-slate-700">
                {runtimeConfig?.graph_provider ?? "unknown"}
              </span>
              <span
                className={cn(
                  "rounded px-2 py-0.5 font-medium",
                  runtimeConfig?.graph_enabled
                    ? "bg-emerald-50 text-emerald-700"
                    : "bg-slate-100 text-slate-500",
                )}
              >
                {runtimeConfig?.graph_enabled ? "enabled" : "disabled"}
              </span>
            </span>
          </div>

          <label
            className={cn(
              "flex items-center justify-between gap-3 rounded-xl border border-slate-200 bg-slate-50 px-4 py-3",
              !runtimeConfig?.graph_enabled && "opacity-60",
            )}
          >
            <span>
              <span className="block text-sm font-semibold text-slate-800">
                Use GraphRAG
              </span>
              <span className="block text-xs text-slate-500">
                Expand recall through Neo4j entities and relationships before reranking.
              </span>
            </span>
            <input
              checked={useGraph && Boolean(runtimeConfig?.graph_enabled)}
              className="h-5 w-5 cursor-pointer accent-cyan-600"
              disabled={!runtimeConfig?.graph_enabled}
              onChange={(event) => setUseGraph(event.target.checked)}
              type="checkbox"
            />
          </label>

          <label className="flex items-center justify-between gap-3 rounded-xl border border-slate-100 bg-white px-4 py-3">
            <span>
              <span className="block text-sm font-semibold text-slate-800">
                Xem tất cả tài liệu (admin)
              </span>
              <span className="block text-xs text-slate-500">
                Nếu bạn là admin: bỏ lọc quyền, tìm trong toàn bộ tài liệu. Người dùng thường không bị ảnh hưởng.
              </span>
            </span>
            <input
              checked={adminViewAll}
              className="h-5 w-5 cursor-pointer accent-cyan-600"
              onChange={(event) => setAdminViewAll(event.target.checked)}
              type="checkbox"
            />
          </label>

          <div className="grid gap-4 sm:grid-cols-2">
            <NumberField
              label="graph_depth"
              max={5}
              min={0}
              onChange={setGraphExpansionDepth}
              value={graphExpansionDepth}
            />
            <NumberField
              label="graph_limit"
              max={100}
              min={1}
              onChange={setGraphExpansionLimit}
              value={graphExpansionLimit}
            />
          </div>
        </div>

        <div className="space-y-4 rounded-xl border border-slate-200 p-4">
          <div className="flex items-center justify-between">
            <p className="text-xs font-semibold uppercase tracking-wider text-slate-500">
              Memory
            </p>
            <span className="inline-flex items-center gap-2 text-xs text-slate-500">
              <span className="rounded bg-slate-100 px-2 py-0.5 font-mono text-slate-700">
                {memorySettings?.memory_provider ?? "unknown"}
              </span>
              <span
                className={cn(
                  "rounded px-2 py-0.5 font-medium",
                  mem0Available
                    ? "bg-emerald-50 text-emerald-700"
                    : "bg-slate-100 text-slate-500",
                )}
              >
                Mem0 {mem0Available ? "enabled" : "disabled"}
              </span>
            </span>
          </div>

          <label
            className={cn(
              "flex items-center justify-between gap-3 rounded-xl border border-slate-200 bg-slate-50 px-4 py-3",
              !memoryEnabled && "opacity-60",
            )}
          >
            <span>
              <span className="block text-sm font-semibold text-slate-800">
                Use memory
              </span>
              <span className="block text-xs text-slate-500">
                Inject your saved memories into the prompt as context.
              </span>
            </span>
            <input
              checked={useMemory && memoryEnabled}
              className="h-5 w-5 cursor-pointer accent-cyan-600"
              disabled={!memoryEnabled}
              onChange={(event) => setUseMemory(event.target.checked)}
              type="checkbox"
            />
          </label>

          <label
            className={cn(
              "flex items-center justify-between gap-3 rounded-xl border border-slate-200 bg-slate-50 px-4 py-3",
              !(memoryEnabled && mem0Available) && "opacity-60",
            )}
          >
            <span>
              <span className="block text-sm font-semibold text-slate-800">
                Use Mem0
              </span>
              <span className="block text-xs text-slate-500">
                Also retrieve from the external Mem0 provider when available.
              </span>
            </span>
            <input
              checked={useMem0 && mem0Available}
              className="h-5 w-5 cursor-pointer accent-cyan-600"
              disabled={!(memoryEnabled && mem0Available)}
              onChange={(event) => setUseMem0(event.target.checked)}
              type="checkbox"
            />
          </label>

          <div className="sm:max-w-[200px]">
            <NumberField
              label="memory_top_k"
              max={50}
              min={1}
              onChange={setMemoryTopK}
              value={memoryTopK}
            />
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

function NumberField({
  label,
  max,
  min,
  onChange,
  value,
}: {
  label: string;
  max: number;
  min: number;
  onChange: (value: number) => void;
  value: number;
}) {
  return (
    <label className="block">
      <span className="text-xs font-semibold uppercase tracking-wider text-slate-500">
        {label}
      </span>
      <Input
        className="mt-2 border-slate-200 bg-white"
        max={max}
        min={min}
        onChange={(event) => {
          const parsed = Number(event.target.value);
          if (!Number.isNaN(parsed)) {
            onChange(parsed);
          }
        }}
        type="number"
        value={value}
      />
    </label>
  );
}

function PipelineStrip({
  compact,
  dark,
  onStepFocus,
  runningStep,
  steps,
}: {
  compact?: boolean;
  dark?: boolean;
  onStepFocus: (step: PipelineStepKey) => void;
  runningStep: PipelineStepKey | null;
  steps: DebugStep[];
}) {
  return (
    <div
      className={cn(
        "grid gap-3 rounded-xl shadow-inner md:grid-cols-5",
        compact ? "p-3" : "p-4",
        dark ? "bg-[#0b3342]" : "bg-slate-100",
      )}
    >
      {steps.map((step, index) => {
        const definition = pipelineDefinitions.find((item) => item.key === step.key);
        const Icon = definition?.icon ?? Activity;
        const clickable = step.state === "failed";

        return (
          <div className="relative" key={step.key}>
            {index > 0 ? (
              <div className="absolute -left-3 top-1/2 hidden w-3 border-t border-dashed border-slate-600 md:block" />
            ) : null}
            <button
              className={cn(
                "flex w-full flex-col justify-between rounded-lg border p-3 text-left transition-colors",
                compact ? "min-h-24" : "min-h-32",
                dark
                  ? "border-cyan-800/50 bg-[#114659] text-white hover:bg-[#15546b]"
                  : "border-slate-200 bg-white text-slate-900 hover:bg-slate-50",
                clickable ? "cursor-pointer" : "cursor-default",
              )}
              onClick={() => {
                if (clickable) {
                  onStepFocus(step.key);
                }
              }}
              type="button"
            >
              <div className="flex items-center justify-between gap-2">
                <span
                  className={cn(
                    "inline-flex items-center gap-1.5 text-xs font-medium uppercase tracking-wider",
                    dark ? "text-cyan-200" : "text-slate-500",
                  )}
                >
                  {runningStep === step.key || step.state === "running" ? (
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  ) : (
                    <Icon className="h-3.5 w-3.5" />
                  )}
                  {step.label}
                </span>
                <StatusBadge state={step.state} compact dark={dark} />
              </div>
              <div className="mt-5 flex items-end justify-between gap-3">
                <span
                  className={cn(
                    "text-xs",
                    dark ? "text-cyan-100/60" : "text-slate-500",
                  )}
                >
                  Duration
                </span>
                <span
                  className={cn(
                    "font-mono text-sm font-semibold",
                    step.state === "failed"
                      ? dark
                        ? "text-rose-300"
                        : "text-rose-700"
                      : dark
                        ? "text-emerald-300"
                        : "text-slate-800",
                  )}
                >
                  {formatDuration(step.durationMs)}
                </span>
              </div>
            </button>
            {index < steps.length - 1 ? (
              <ChevronRight className="absolute -right-2 top-1/2 hidden h-4 w-4 -translate-y-1/2 text-cyan-300/50 md:block" />
            ) : null}
          </div>
        );
      })}
    </div>
  );
}

function StatusBadge({
  compact,
  dark,
  state,
}: {
  compact?: boolean;
  dark?: boolean;
  state: RunState | string;
}) {
  const normalized = normalizeState(state);
  const Icon =
    normalized === "succeeded"
      ? CheckCircle2
      : normalized === "failed"
        ? AlertCircle
        : normalized === "running"
          ? Loader2
          : Clock3;

  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs font-medium",
        compact ? "text-[10px]" : "text-xs",
        normalized === "succeeded" &&
          (dark
            ? "border-emerald-500/30 bg-emerald-500/20 text-emerald-300"
            : "border-emerald-200/60 bg-emerald-50 text-emerald-700"),
        normalized === "failed" &&
          (dark
            ? "border-rose-500/30 bg-rose-500/20 text-rose-300"
            : "border-rose-200/60 bg-rose-50 text-rose-700"),
        normalized === "running" &&
          (dark
            ? "border-cyan-500/30 bg-cyan-500/20 text-cyan-200"
            : "border-cyan-200/60 bg-cyan-50 text-cyan-700"),
        normalized === "idle" &&
          (dark
            ? "border-slate-500/30 bg-slate-500/20 text-slate-300"
            : "border-slate-200/60 bg-slate-50 text-slate-600"),
      )}
    >
      <Icon
        className={cn("h-3 w-3", normalized === "running" && "animate-spin")}
      />
      {normalized.toUpperCase()}
    </span>
  );
}

function MetricStrip({
  metrics,
}: {
  metrics: Array<{ label: string; value: string }>;
}) {
  return (
    <div className="grid divide-y divide-slate-100 rounded-xl bg-slate-50 p-4 sm:grid-cols-3 sm:divide-x sm:divide-y-0">
      {metrics.map((metric) => (
        <div className="px-3 py-2 first:pl-0 last:pr-0" key={metric.label}>
          <p className="text-xs font-semibold uppercase tracking-wider text-slate-500">
            {metric.label}
          </p>
          <p className="mt-1 font-mono text-2xl font-bold text-slate-800">
            {metric.value}
          </p>
        </div>
      ))}
    </div>
  );
}

function QueueMetric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg bg-white/70 px-3 py-2 ring-1 ring-slate-100">
      <dt className="font-semibold uppercase tracking-wider text-slate-400">{label}</dt>
      <dd className="mt-1 font-mono text-sm font-semibold text-slate-800">{value}</dd>
    </div>
  );
}

function LogPanel({
  highlightedLogKey,
  logs,
  title,
}: {
  highlightedLogKey: string | null;
  logs: UiLog[];
  title: string;
}) {
  return (
    <Card className="bg-white shadow-sm">
      <CardHeader className="pb-3">
        <CardTitle className="flex items-center gap-2">
          <TerminalSquare className="h-5 w-5 text-cyan-700" />
          {title}
        </CardTitle>
        <CardDescription>Timestamped execution events for validation.</CardDescription>
      </CardHeader>
      <CardContent>
        <div className="max-h-[620px] space-y-1 overflow-y-auto rounded-xl bg-slate-950 p-3">
          {logs.length === 0 ? (
            <p className="px-2 py-8 text-center text-sm text-slate-400">
              No operation logs yet.
            </p>
          ) : (
            logs.map((log) => {
              const logKey = `${log.source}:${log.step}`;
              return (
                <div
                  className={cn(
                    "rounded-lg px-2 py-1.5 text-xs transition-colors",
                    highlightedLogKey === logKey
                      ? "bg-amber-400/15 ring-1 ring-amber-300/40"
                      : "bg-transparent",
                  )}
                  key={log.id}
                >
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-mono text-slate-400">
                      {formatTime(log.timestamp)}
                    </span>
                    <TinyTag>{log.source}</TinyTag>
                    <TinyTag>{log.step}</TinyTag>
                    <span
                      className={cn(
                        "font-medium",
                        log.level === "success" && "text-emerald-300",
                        log.level === "error" && "text-rose-300",
                        log.level === "info" && "text-cyan-200",
                      )}
                    >
                      {log.level.toUpperCase()}
                    </span>
                    {log.durationMs !== undefined ? (
                      <span className="font-mono text-slate-500">
                        {formatDuration(log.durationMs)}
                      </span>
                    ) : null}
                  </div>
                  <p className="mt-1 text-slate-300">{log.message}</p>
                </div>
              );
            })
          )}
        </div>
      </CardContent>
    </Card>
  );
}

function RuntimeBadges({ config }: { config: RuntimeConfigResponse | null }) {
  if (!config) {
    return null;
  }

  return (
    <>
      <HeaderBadge label="Embedding" value={config.embedding_provider} />
      <HeaderBadge label="LLM" value={config.llm_provider} />
      <HeaderBadge
        label="Vector"
        value={`${config.vector_collection_name} / ${config.embedding_dimension}`}
      />
    </>
  );
}

function HeaderBadge({ label, value }: { label: string; value: string }) {
  return (
    <span className="inline-flex items-center gap-1 rounded-full border border-slate-700 bg-slate-800 px-2.5 py-1 text-[11px] font-medium text-slate-300">
      <span className="text-slate-500">{label}</span>
      <span className="font-mono text-cyan-200">{value}</span>
    </span>
  );
}

function KeyValue({ label, value }: { label: string; value: string }) {
  return (
    <div className="grid grid-cols-[120px_minmax(0,1fr)] gap-3 text-sm">
      <dt className="text-slate-400">{label}</dt>
      <dd className="truncate font-medium text-slate-700" title={value}>
        {value}
      </dd>
    </div>
  );
}

function TinyTag({ children }: { children: string }) {
  return (
    <span className="rounded bg-slate-800 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-slate-300">
      {children}
    </span>
  );
}

function EmptyState({ message }: { message: string }) {
  return (
    <div className="rounded-xl border border-dashed border-slate-200 bg-slate-50 px-4 py-8 text-center text-sm text-slate-500">
      {message}
    </div>
  );
}

function buildPipelineStepsFromJob(
  job: IngestionJob | null,
  definitions = pipelineDefinitions,
): DebugStep[] {
  return definitions.map((definition) => {
    const jobStep = findJobStep(job?.steps ?? [], definition.key);
    return {
      key: definition.key,
      label: definition.label,
      state: normalizeState(jobStep?.state ?? "idle"),
      durationMs: jobStep?.duration_ms ?? null,
      output: jobStep?.output ?? {},
      error: jobStep?.error ?? null,
    };
  });
}

function findJobStep(
  steps: IngestionStep[],
  key: PipelineStepKey,
): IngestionStep | undefined {
  const aliases: Record<PipelineStepKey, string[]> = {
    upload: ["upload"],
    parse: ["parse"],
    chunk: ["chunk"],
    enrich: ["enrich", "chunk-enrich", "chunk_enrich", "enrichment"],
    index: ["index", "embed", "embed_index", "index-vector", "index_vector"],
    graph: ["graph", "index-graph", "graph_index"],
  };

  return steps.find((step) => aliases[key].includes(step.name));
}

function normalizeState(state: string): RunState {
  if (["succeeded", "success", "indexed", "completed"].includes(state)) {
    return "succeeded";
  }
  if (["failed", "error"].includes(state)) {
    return "failed";
  }
  if (["running", "queued"].includes(state)) {
    return "running";
  }
  return "idle";
}

function formatDuration(durationMs?: number | null): string {
  if (durationMs === null || durationMs === undefined) {
    return "--";
  }
  if (durationMs < 1000) {
    return `${Math.round(durationMs)}ms`;
  }
  return `${(durationMs / 1000).toFixed(2)}s`;
}

function formatTime(timestamp: string): string {
  const date = new Date(timestamp);
  if (Number.isNaN(date.getTime())) {
    return timestamp;
  }
  return date.toLocaleTimeString("en-GB", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
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

function formatFileSize(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) {
    return "--";
  }
  if (bytes < 1024) {
    return `${bytes} B`;
  }
  const units = ["KB", "MB", "GB", "TB"];
  let size = bytes / 1024;
  let unitIndex = 0;
  while (size >= 1024 && unitIndex < units.length - 1) {
    size /= 1024;
    unitIndex += 1;
  }
  return `${size.toFixed(size >= 10 ? 1 : 2)} ${units[unitIndex]}`;
}

function compactId(id: string): string {
  if (id.length <= 12) {
    return id;
  }
  return `${id.slice(0, 8)}...${id.slice(-4)}`;
}

function formatPerson(person: DocumentListItem["uploaded_by"]): string {
  return person?.full_name ?? person?.username ?? "Unknown";
}

function isDofficeSource(document: Pick<DocumentListItem, "source_type">): boolean {
  return document.source_type === DOFFICE_SOURCE_TYPE;
}

function formatDocumentSource(document: DocumentListItem): string {
  if (isDofficeSource(document)) {
    return "AI DO";
  }
  return document.source_type ? document.source_type.toUpperCase() : "Upload";
}

function formatDocumentSecondaryLabel(document: DocumentListItem): string {
  if (isDofficeSource(document)) {
    return `AI DO${document.filename ? ` / ${document.filename}` : ""}`;
  }
  return document.filename ?? "No file name";
}

function formatDocumentProfile(document: DocumentListItem): string {
  if (isDofficeSource(document)) {
    return "AI DO: clean markdown/html -> text";
  }
  return document.document_profile ?? "unknown";
}

function formatKnowledgeBase(document: DocumentListItem): string {
  return document.knowledge_base?.name ?? "No knowledge base";
}

function formatDocumentScope(document: DocumentListItem): string {
  if (document.knowledge_base?.visibility === "private") {
    return "Personal";
  }
  if (document.visibility === "private") {
    return "Personal document";
  }
  if (document.knowledge_base?.visibility === "global") {
    return "Shared global";
  }
  if (document.knowledge_base?.visibility === "subtree") {
    return "Shared subtree";
  }
  return "Shared organization";
}
