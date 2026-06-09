"use client";

import {
  Activity,
  AlertCircle,
  Brain,
  CheckCircle2,
  ChevronRight,
  Clock3,
  Database,
  FileSearch,
  Gauge,
  GitBranch,
  Layers3,
  Loader2,
  MessageSquareText,
  Play,
  RefreshCw,
  RotateCcw,
  Rows3,
  Send,
  ServerCog,
  TerminalSquare,
  Trash2,
  Upload,
  Workflow,
} from "lucide-react";
import {
  type ChangeEvent,
  type FormEvent,
  useCallback,
  useEffect,
  useMemo,
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
  chunkDocument,
  createMemory,
  deleteDocument,
  deleteIngestionJob,
  deleteMemory,
  enqueueIngestionJob,
  getDocumentDetail,
  getCurrentUser,
  getErrorMessage,
  getGraphHealth,
  getMemorySettings,
  getProfiles,
  getRuntimeConfig,
  indexDocumentGraph,
  indexDocumentVector,
  listDocuments,
  listIngestionJobs,
  listMemories,
  parseDocument,
  type ChunkPreview,
  type DocumentDetailResponse,
  type DocumentListItem,
  type GraphHealthResponse,
  type IngestionJob,
  type IngestionLog,
  type IngestionStep,
  type MemoryItem,
  type MemorySettings,
  type MemoryType,
  type RagCitation,
  type RuntimeConfigResponse,
  type AuthUser,
  type AnswerMode,
  type AnswerStyle,
  type ChunkMode,
  type DocumentProfile,
  type ProfilesResponse,
  uploadDocument,
} from "@/lib/api";
import { streamRagChat } from "@/lib/streaming";
import { cn } from "@/lib/utils";

type ActiveView = "auto" | "chat" | "settings" | "memory";
type TypewriterSpeed = "slow" | "normal" | "fast";
type PipelineStepKey = "upload" | "parse" | "chunk" | "index" | "graph";
type RunState = "idle" | "running" | "succeeded" | "failed";
type LogSource = "auto" | "debug" | "chat" | "system";

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
  { key: "index", label: "Embed + Index", icon: Database },
  { key: "graph", label: "Graph Index", icon: GitBranch },
];

const SELECTED_DOCUMENT_STORAGE_KEY = "hbrag_selected_document_id";

const initialDebugSteps: DebugStep[] = pipelineDefinitions.map((step) => ({
  key: step.key,
  label: step.label,
  state: "idle",
  durationMs: null,
  output: {},
  error: null,
}));

const navItems: Array<{
  key: ActiveView;
  label: string;
  icon: typeof Workflow;
}> = [
  { key: "auto", label: "Auto Queue", icon: Workflow },
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
  const [runtimeExpanded, setRuntimeExpanded] = useState(false);
  const [systemError, setSystemError] = useState<string | null>(null);
  const [graphHealth, setGraphHealth] = useState<GraphHealthResponse | null>(null);
  const [graphHealthBusy, setGraphHealthBusy] = useState(false);
  const [documents, setDocuments] = useState<DocumentListItem[]>([]);
  const [selectedDocumentId, setSelectedDocumentId] = useState<string | null>(null);
  const [selectedDocument, setSelectedDocument] =
    useState<DocumentDetailResponse | null>(null);
  const [isLoadingDocuments, setIsLoadingDocuments] = useState(false);
  const [uploadFiles, setUploadFiles] = useState<File[]>([]);
  const [uploadingDocuments, setUploadingDocuments] = useState(false);
  const [deletingDocumentId, setDeletingDocumentId] = useState<string | null>(null);

  const [autoFile, setAutoFile] = useState<File | null>(null);
  const [autoJobs, setAutoJobs] = useState<IngestionJob[]>([]);
  const [selectedJobId, setSelectedJobId] = useState<string | null>(null);
  const [autoSubmitting, setAutoSubmitting] = useState(false);
  const [autoUploadMessage, setAutoUploadMessage] = useState<{
    type: "success" | "error";
    text: string;
  } | null>(null);

  const [debugDocumentId, setDebugDocumentId] = useState<string | null>(null);
  const [debugDocumentStatus, setDebugDocumentStatus] = useState<string>("none");
  const [debugSteps, setDebugSteps] = useState<DebugStep[]>(initialDebugSteps);
  const [debugParsedText, setDebugParsedText] = useState("");
  const [debugParsedCharacterCount, setDebugParsedCharacterCount] = useState(0);
  const [debugChunks, setDebugChunks] = useState<ChunkPreview[]>([]);
  const [debugPreviewTab, setDebugPreviewTab] = useState<"parse" | "chunks">(
    "parse",
  );
  const [runningDebugStep, setRunningDebugStep] =
    useState<PipelineStepKey | null>(null);
  const [highlightedLogKey, setHighlightedLogKey] = useState<string | null>(
    null,
  );

  const [question, setQuestion] = useState("");
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [answer, setAnswer] = useState("");
  const [citations, setCitations] = useState<RagCitation[]>([]);
  const [citationDocuments, setCitationDocuments] = useState<
    Record<string, DocumentDetailResponse>
  >({});
  const [selectedCitationIndex, setSelectedCitationIndex] = useState<number | null>(null);
  const [asking, setAsking] = useState(false);

  const [chunkSize, setChunkSize] = useState(1000);
  const [chunkOverlap, setChunkOverlap] = useState(150);
  const [chunkMode, setChunkMode] = useState<ChunkMode>("recursive");
  const [topK, setTopK] = useState(5);
  const [candidateK, setCandidateK] = useState(20);
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
  const [answerMode, setAnswerMode] = useState<AnswerMode>("hybrid");
  const [answerStyle, setAnswerStyle] = useState<AnswerStyle>("policy_explainer");
  const [maxContextChars, setMaxContextChars] = useState(6000);
  const [useGraph, setUseGraph] = useState(false);
  const [graphExpansionDepth, setGraphExpansionDepth] = useState(1);
  const [graphExpansionLimit, setGraphExpansionLimit] = useState(20);
  const [profile, setProfile] = useState<DocumentProfile>("auto");
  const [profilesConfig, setProfilesConfig] = useState<ProfilesResponse | null>(
    null,
  );
  const settingsInitialized = useRef(false);

  const pendingTextRef = useRef("");
  const isStreamDoneRef = useRef(false);
  const intervalRef = useRef<number | null>(null);

  const [logs, setLogs] = useState<UiLog[]>([]);

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
    const stored = window.localStorage.getItem("hbrag_answer_mode");
    if (stored === "generative" || stored === "extractive" || stored === "hybrid") {
      setAnswerMode(stored);
    }
    const storedStyle = window.localStorage.getItem("hbrag_answer_style");
    if (
      storedStyle === "concise" ||
      storedStyle === "detailed" ||
      storedStyle === "policy_explainer"
    ) {
      setAnswerStyle(storedStyle);
    }
    const storedProfile = window.localStorage.getItem("hbrag_profile");
    if (
      storedProfile === "auto" ||
      storedProfile === "legal_admin" ||
      storedProfile === "general" ||
      storedProfile === "technical" ||
      storedProfile === "faq" ||
      storedProfile === "spreadsheet"
    ) {
      setProfile(storedProfile);
    }
  }, []);

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

  const handleAnswerModeChange = useCallback((mode: AnswerMode) => {
    setAnswerMode(mode);
    window.localStorage.setItem("hbrag_answer_mode", mode);
  }, []);

  const handleAnswerStyleChange = useCallback((style: AnswerStyle) => {
    setAnswerStyle(style);
    window.localStorage.setItem("hbrag_answer_style", style);
  }, []);

  const refreshProfiles = useCallback(async () => {
    try {
      const config = await getProfiles();
      setProfilesConfig(config);
    } catch (error) {
      setSystemError(getErrorMessage(error));
    }
  }, []);

  const handleProfileChange = useCallback(
    (next: DocumentProfile) => {
      setProfile(next);
      window.localStorage.setItem("hbrag_profile", next);
      const config = profilesConfig?.configs?.[next];
      if (!config) {
        return;
      }
      // Populate controls from the profile so the user can still override.
      setChunkMode(config.chunk_mode);
      setChunkSize(config.chunk_size);
      setChunkOverlap(config.chunk_overlap);
      setTopK(config.top_k);
      setCandidateK(config.candidate_k);
      setAnswerMode(config.answer_mode);
      setAnswerStyle(config.answer_style);
      setMaxContextChars(config.max_context_chars);
    },
    [profilesConfig],
  );

  const selectedJob = useMemo(() => {
    if (selectedJobId) {
      return autoJobs.find((job) => job.job_id === selectedJobId) ?? null;
    }
    return autoJobs[0] ?? null;
  }, [autoJobs, selectedJobId]);

  const selectedJobSteps = useMemo(
    () => buildPipelineStepsFromJob(selectedJob),
    [selectedJob],
  );

  const selectedJobLogs = useMemo(
    () => mapIngestionLogs(selectedJob?.logs ?? []),
    [selectedJob?.logs],
  );

  const canDebugParse = Boolean(selectedDocumentId) && selectedDocument?.status === "uploaded";
  const canDebugChunk =
    Boolean(selectedDocumentId) && ["parsed", "chunked"].includes(selectedDocument?.status ?? "");
  const canDebugIndex =
    Boolean(selectedDocumentId) && ["chunked", "indexed"].includes(selectedDocument?.status ?? "");
  const canDebugGraph =
    Boolean(selectedDocumentId) &&
    selectedDocument?.status === "indexed" &&
    (runtimeConfig?.graph_enabled ?? false);

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
        setChunkSize(config.default_chunk_size);
        setChunkOverlap(config.default_chunk_overlap);
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

  const checkGraphHealth = useCallback(async () => {
    setGraphHealthBusy(true);
    try {
      const response = await getGraphHealth();
      setGraphHealth(response);
      appendLog(
        "system",
        "graph",
        response.healthy ? "success" : "error",
        response.message,
      );
    } catch (error) {
      appendLog("system", "graph", "error", getErrorMessage(error));
    } finally {
      setGraphHealthBusy(false);
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

  const syncDocumentWorkspace = useCallback(
    (detail: DocumentDetailResponse | null, options?: { keepChunks?: boolean }) => {
      setSelectedDocument(detail);
      setDebugDocumentId(detail?.document_id ?? null);
      setDebugDocumentStatus(detail?.status ?? "none");
      setDebugParsedText(detail?.preview_text ?? "");
      setDebugParsedCharacterCount(detail?.parsed_character_count ?? 0);
      setDebugSteps(
        buildDocumentDebugSteps(detail, runtimeConfig?.graph_enabled ?? false),
      );
      if (!options?.keepChunks) {
        setDebugChunks([]);
      }
      if (detail?.document_id) {
        window.localStorage.setItem(SELECTED_DOCUMENT_STORAGE_KEY, detail.document_id);
      } else {
        window.localStorage.removeItem(SELECTED_DOCUMENT_STORAGE_KEY);
      }
    },
    [runtimeConfig?.graph_enabled],
  );

  const refreshSelectedDocument = useCallback(
    async (documentId: string) => {
      const detail = await getDocumentDetail(documentId);
      syncDocumentWorkspace(detail);
      return detail;
    },
    [syncDocumentWorkspace],
  );

  const refreshDocuments = useCallback(
    async (preferredDocumentId?: string | null) => {
      setIsLoadingDocuments(true);
      try {
        const response = await listDocuments({
          limit: 200,
          offset: 0,
        });
        setDocuments(response.items);

        const storedDocumentId =
          preferredDocumentId ??
          selectedDocumentId ??
          window.localStorage.getItem(SELECTED_DOCUMENT_STORAGE_KEY);
        const nextSelectedId =
          response.items.find((item) => item.document_id === storedDocumentId)?.document_id ??
          response.items[0]?.document_id ??
          null;
        setSelectedDocumentId(nextSelectedId);
        if (!nextSelectedId) {
          syncDocumentWorkspace(null);
        }
      } catch (error) {
        appendLog("system", "documents", "error", getErrorMessage(error));
      } finally {
        setIsLoadingDocuments(false);
      }
    },
    [
      appendLog,
      selectedDocumentId,
      syncDocumentWorkspace,
    ],
  );

  const refreshJobs = useCallback(async () => {
    try {
      const jobs = await listIngestionJobs();
      setAutoJobs(jobs);
      setSelectedJobId((current) => current ?? jobs[0]?.job_id ?? null);
    } catch (error) {
      appendLog("auto", "queue", "error", getErrorMessage(error));
    }
  }, [appendLog]);

  useEffect(() => {
    if (!authChecked) {
      return;
    }
    void refreshDocuments();
    void refreshRuntimeConfig();
    void refreshJobs();
    void refreshMemorySettings();
    void refreshMemoryItems();
    void refreshProfiles();
  }, [
      authChecked,
    refreshDocuments,
    refreshJobs,
    refreshRuntimeConfig,
    refreshMemorySettings,
    refreshMemoryItems,
    refreshProfiles,
  ]);

  useEffect(() => {
    if (!authChecked) {
      return;
    }
    const timer = window.setTimeout(() => {
      void refreshDocuments();
    }, 200);
    return () => window.clearTimeout(timer);
  }, [authChecked, refreshDocuments]);

  useEffect(() => {
    if (!selectedDocumentId) {
      syncDocumentWorkspace(null);
      return;
    }

    let cancelled = false;
    void getDocumentDetail(selectedDocumentId)
      .then((detail) => {
        if (!cancelled) {
          syncDocumentWorkspace(detail);
        }
      })
      .catch((error) => {
        if (cancelled) {
          return;
        }
        appendLog("system", "documents", "error", getErrorMessage(error));
        void refreshDocuments(null);
      });

    return () => {
      cancelled = true;
    };
  }, [appendLog, refreshDocuments, selectedDocumentId, syncDocumentWorkspace]);

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

  useEffect(() => {
    if (!selectedJobId) {
      return;
    }

    const selectedStillExists = autoJobs.some(
      (job) => job.job_id === selectedJobId,
    );
    if (!selectedStillExists) {
      setSelectedJobId(autoJobs[0]?.job_id ?? null);
    }
  }, [autoJobs, selectedJobId]);

  const handleAutoFileChange = (event: ChangeEvent<HTMLInputElement>) => {
    setAutoFile(event.target.files?.[0] ?? null);
    setAutoUploadMessage(null);
  };

  const handleLibraryFilesChange = (event: ChangeEvent<HTMLInputElement>) => {
    const [file] = Array.from(event.target.files ?? []);
    setUploadFiles(file ? [file] : []);
  };

  const handleLibraryUpload = async (): Promise<number> => {
    if (uploadFiles.length === 0 || uploadingDocuments) {
      return 0;
    }

    setUploadingDocuments(true);
    const started = performance.now();
    try {
      const response = await uploadDocument(uploadFiles[0]);
      await refreshDocuments(response.document_id);
      const detail = await refreshSelectedDocument(response.document_id);
      setDebugSteps(buildDocumentDebugSteps(detail, runtimeConfig?.graph_enabled ?? false));
      appendLog(
        "system",
        "upload",
        "success",
        `Uploaded ${response.filename}.`,
        performance.now() - started,
      );
      return 1;
    } catch (error) {
      appendLog("system", "upload", "error", getErrorMessage(error));
      return 0;
    } finally {
      setUploadFiles([]);
      setUploadingDocuments(false);
    }
  };

  const handleAutoSubmit = async () => {
    if (!autoFile) {
      return;
    }

    setAutoSubmitting(true);
    setAutoUploadMessage(null);
    const started = performance.now();
    appendLog("auto", "queue", "info", `Queued ${autoFile.name}.`);

    try {
      const job = await enqueueIngestionJob(autoFile);
      const durationMs = performance.now() - started;
      setAutoJobs((current) => [job, ...current.filter((item) => item.job_id !== job.job_id)]);
      setSelectedJobId(job.job_id);
      appendLog(
        "auto",
        "queue",
        "success",
        `Created ingestion job ${compactId(job.job_id)}.`,
        durationMs,
      );
      setAutoUploadMessage({
        type: "success",
        text: `Queued ${autoFile.name} successfully.`,
      });
      await refreshDocuments(null);
    } catch (error) {
      const message = getErrorMessage(error);
      appendLog("auto", "queue", "error", message);
      setAutoUploadMessage({ type: "error", text: message });
    } finally {
      setAutoSubmitting(false);
    }
  };

  const handleDeleteDocument = useCallback(
    async (documentId: string) => {
      const target = documents.find((document) => document.document_id === documentId);
      const title = target?.title ?? documentId;
      const confirmed = window.confirm(
        `Delete "${title}" from MinIO, Qdrant, and the document database?`,
      );
      if (!confirmed) {
        return;
      }

      setDeletingDocumentId(documentId);
      const started = performance.now();
      try {
        const result = await deleteDocument(documentId);
        if (selectedDocumentId === documentId) {
          setSelectedDocumentId(null);
          syncDocumentWorkspace(null);
        }
        await refreshDocuments(null);
        appendLog(
          "system",
          "delete",
          "success",
          `Deleted ${title}: ${result.deleted_files} MinIO file(s), Qdrant vectors cleared.`,
          performance.now() - started,
        );
      } catch (error) {
        appendLog("system", "delete", "error", getErrorMessage(error));
      } finally {
        setDeletingDocumentId(null);
      }
    },
    [appendLog, documents, refreshDocuments, selectedDocumentId, syncDocumentWorkspace],
  );

  const handleDeleteQueuedDocument = useCallback(
    async (job: IngestionJob) => {
      if (!job.document_id) {
        appendLog("auto", "delete", "error", "This job has no document to delete.");
        return;
      }
      const confirmed = window.confirm(
        `Delete "${job.filename}" from MinIO, Qdrant, and the document database?`,
      );
      if (!confirmed) {
        return;
      }

      setDeletingDocumentId(job.document_id);
      const started = performance.now();
      try {
        const result = await deleteDocument(job.document_id);
        try {
          await deleteIngestionJob(job.job_id);
        } catch {
          // The document deletion is the important operation; the in-memory job may already be gone.
        }
        setAutoJobs((current) => current.filter((item) => item.job_id !== job.job_id));
        setSelectedJobId((current) => (current === job.job_id ? null : current));
        if (selectedDocumentId === job.document_id) {
          setSelectedDocumentId(null);
          syncDocumentWorkspace(null);
        }
        await refreshDocuments(null);
        appendLog(
          "auto",
          "delete",
          "success",
          `Deleted ${job.filename}: ${result.deleted_files} MinIO file(s), Qdrant vectors cleared.`,
          performance.now() - started,
        );
      } catch (error) {
        appendLog("auto", "delete", "error", getErrorMessage(error));
      } finally {
        setDeletingDocumentId(null);
      }
    },
    [appendLog, refreshDocuments, selectedDocumentId, syncDocumentWorkspace],
  );

  const runDebugStep = async (
    stepKey: PipelineStepKey,
    targetDocumentId?: string | null,
  ) => {
    if (runningDebugStep) {
      return;
    }

    const activeDocumentId = targetDocumentId ?? selectedDocumentId ?? debugDocumentId;

    if (stepKey === "upload" && uploadFiles.length === 0) {
      appendLog("debug", "upload", "error", "Select a file in Document Library.");
      return;
    }

    if (stepKey !== "upload" && !activeDocumentId) {
      appendLog("debug", stepKey, "error", "Select a document before running this step.");
      return;
    }

    setRunningDebugStep(stepKey);
    setDebugStep(stepKey, {
      state: "running",
      durationMs: null,
      error: null,
      output: {},
    });
    appendLog("debug", stepKey, "info", `Running ${stepKey}.`);

    const started = performance.now();

    try {
      if (stepKey === "upload") {
        const successCount = await handleLibraryUpload();
        const durationMs = performance.now() - started;
        setDebugStep("upload", {
          state: successCount > 0 ? "succeeded" : "failed",
          durationMs,
          output: {
            file_count: uploadFiles.length,
            selected_document_id: selectedDocumentId,
            success_count: successCount,
          },
          error: successCount > 0 ? null : "No documents were uploaded successfully.",
        });
        return;
      }

      if (stepKey === "parse") {
        const result = await parseDocument(activeDocumentId as string);
        const durationMs = performance.now() - started;
        setDebugParsedText(result.preview);
        setDebugParsedCharacterCount(result.character_count);
        setDebugChunks([]);
        setDebugPreviewTab("parse");
        setDebugDocumentStatus(result.status);
        await refreshDocuments(activeDocumentId);
        await refreshSelectedDocument(activeDocumentId as string);
        setDebugStep("parse", {
          state: "succeeded",
          durationMs,
          output: {
            character_count: result.character_count,
            status: result.status,
          },
          error: null,
        });
        resetDebugStepsAfter("parse");
        appendLog(
          "debug",
          "parse",
          "success",
          `Parsed ${result.character_count.toLocaleString()} characters.`,
          durationMs,
        );
        return;
      }

      if (stepKey === "chunk") {
        const result = await chunkDocument(activeDocumentId as string, {
          chunk_size: chunkSize,
          chunk_overlap: chunkOverlap,
          chunk_mode: chunkMode,
          profile,
        });
        const durationMs = performance.now() - started;
        setDebugChunks(result.preview);
        setDebugPreviewTab("chunks");
        setDebugDocumentStatus(result.status);
        await refreshDocuments(activeDocumentId);
        await refreshSelectedDocument(activeDocumentId as string);
        setDebugStep("chunk", {
          state: "succeeded",
          durationMs,
          output: {
            chunk_count: result.chunk_count,
            preview_count: result.preview.length,
            status: result.status,
          },
          error: null,
        });
        resetDebugStepsAfter("chunk");
        appendLog(
          "debug",
          "chunk",
          "success",
          `Created ${result.chunk_count.toLocaleString()} chunks.`,
          durationMs,
        );
        return;
      }

      if (stepKey === "graph") {
        const result = await indexDocumentGraph(activeDocumentId as string, {
          extractor_provider: "llm",
          max_entities_per_chunk: 30,
          max_relations_per_chunk: 40,
        });
        const durationMs = performance.now() - started;
        setDebugDocumentStatus(result.status);
        await refreshDocuments(activeDocumentId);
        await refreshSelectedDocument(activeDocumentId as string);
        setDebugStep("graph", {
          state: "succeeded",
          durationMs,
          output: {
            chunks_processed: result.chunks_processed,
            entities_extracted: result.entities_extracted,
            relations_extracted: result.relations_extracted,
            merged_entities: result.merged_entities,
            merged_relations: result.merged_relations,
            status: result.status,
          },
          error: null,
        });
        appendLog(
          "debug",
          "graph",
          "success",
          `Graph indexed ${result.chunks_processed.toLocaleString()} chunks.`,
          durationMs,
        );
        return;
      }

      const result = await indexDocumentVector(activeDocumentId as string);
      const durationMs = performance.now() - started;
      setDebugDocumentStatus(result.status);
      await refreshDocuments(activeDocumentId);
      await refreshSelectedDocument(activeDocumentId as string);
      setDebugStep("index", {
        state: "succeeded",
        durationMs,
        output: {
          indexed_chunk_count: result.indexed_chunk_count,
          status: result.status,
        },
        error: null,
      });
      appendLog(
        "debug",
        "index",
        "success",
        `Indexed ${result.indexed_chunk_count.toLocaleString()} chunks.`,
        durationMs,
      );
    } catch (error) {
      const durationMs = performance.now() - started;
      const message = getErrorMessage(error);
      setDebugStep(stepKey, {
        state: "failed",
        durationMs,
        output: {},
        error: message,
      });
      handleFailedDebugStep(stepKey);
      appendLog("debug", stepKey, "error", message, durationMs);
    } finally {
      setRunningDebugStep(null);
    }
  };

  const handleFailedDebugStep = (stepKey: PipelineStepKey) => {
    if (stepKey === "upload") {
      syncDocumentWorkspace(selectedDocument);
      return;
    }

    if (stepKey === "parse") {
      syncDocumentWorkspace(selectedDocument);
      setDebugPreviewTab("parse");
      return;
    }

    if (stepKey === "chunk") {
      syncDocumentWorkspace(selectedDocument);
      setDebugChunks([]);
      setDebugPreviewTab("chunks");
      return;
    }

    if (stepKey === "graph") {
      syncDocumentWorkspace(selectedDocument);
      return;
    }

    syncDocumentWorkspace(selectedDocument);
  };

  const setDebugStep = (
    stepKey: PipelineStepKey,
    patch: Partial<DebugStep>,
  ) => {
    setDebugSteps((current) =>
      current.map((step) =>
        step.key === stepKey
          ? {
              ...step,
              ...patch,
            }
          : step,
      ),
    );
  };

  const resetDebugStepsAfter = (stepKey: PipelineStepKey) => {
    const stepIndex = pipelineDefinitions.findIndex((step) => step.key === stepKey);
    setDebugSteps((current) =>
      current.map((step, index) =>
        index > stepIndex
          ? {
              ...step,
              state: "idle",
              durationMs: null,
              output: {},
              error: null,
            }
          : step,
      ),
    );
  };

  const resetDebugState = () => {
    setDebugChunks([]);
    setDebugPreviewTab("parse");
    setRunningDebugStep(null);
    setHighlightedLogKey(null);
    syncDocumentWorkspace(selectedDocument);
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
            top_k: topK,
            candidate_k: candidateK,
            use_memory: useMemory,
            use_mem0: useMem0,
            memory_top_k: memoryTopK,
            answer_mode: answerMode,
            answer_style: answerStyle,
            max_context_chars: maxContextChars,
            profile,
            use_graph: useGraph,
            graph_expansion_depth: graphExpansionDepth,
            graph_expansion_limit: graphExpansionLimit,
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
        top_k: topK,
        candidate_k: candidateK,
        use_memory: useMemory,
        use_mem0: useMem0,
        memory_top_k: memoryTopK,
        answer_mode: answerMode,
        answer_style: answerStyle,
        max_context_chars: maxContextChars,
        profile,
        use_graph: useGraph,
        graph_expansion_depth: graphExpansionDepth,
        graph_expansion_limit: graphExpansionLimit,
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

  const debugMetrics = [
    {
      label: "Document status",
      value: debugDocumentStatus,
    },
    {
      label: "Parsed characters",
      value: debugParsedCharacterCount.toLocaleString(),
    },
    {
      label: "Stored chunks",
      value: (selectedDocument?.chunk_count ?? debugChunks.length).toLocaleString(),
    },
  ];

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
          <AutoQueueView
            deletingDocumentId={deletingDocumentId}
            documents={documents}
            file={autoFile}
            isLoadingDocuments={isLoadingDocuments}
            jobs={autoJobs}
            loading={autoSubmitting}
            message={autoUploadMessage}
            onFileChange={handleAutoFileChange}
            onDeleteDocument={handleDeleteQueuedDocument}
            onDeletePublishedDocument={handleDeleteDocument}
            onRefreshDocuments={() => {
              void refreshDocuments(null);
            }}
            onSelectJob={setSelectedJobId}
            onSubmit={handleAutoSubmit}
            selectedJob={selectedJob}
            selectedJobSteps={selectedJobSteps}
            logs={[...selectedJobLogs, ...logs.filter((log) => log.source === "auto")]}
          />
        ) : null}

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
            answerMode={answerMode}
            answerStyle={answerStyle}
            candidateK={candidateK}
            chunkOverlap={chunkOverlap}
            chunkSize={chunkSize}
            chunkMode={chunkMode}
            memorySettings={memorySettings}
            maxContextChars={maxContextChars}
            runtimeConfig={runtimeConfig}
            setCandidateK={setCandidateK}
            setChunkOverlap={setChunkOverlap}
            setChunkSize={setChunkSize}
            setChunkMode={setChunkMode}
            setStreamingEnabled={setStreamingEnabled}
            setTopK={setTopK}
            setTypewriterEnabled={setTypewriterEnabled}
            setTypewriterSpeed={setTypewriterSpeed}
            setUseMem0={setUseMem0}
            setUseMemory={setUseMemory}
            setMemoryTopK={setMemoryTopK}
            setUseGraph={setUseGraph}
            setGraphExpansionDepth={setGraphExpansionDepth}
            setGraphExpansionLimit={setGraphExpansionLimit}
            setAnswerMode={handleAnswerModeChange}
            setAnswerStyle={handleAnswerStyleChange}
            profile={profile}
            setProfile={handleProfileChange}
            setMaxContextChars={setMaxContextChars}
            streamingEnabled={streamingEnabled}
            topK={topK}
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

function AutoQueueView({
  deletingDocumentId,
  documents,
  file,
  isLoadingDocuments,
  jobs,
  loading,
  logs,
  message,
  onFileChange,
  onDeleteDocument,
  onDeletePublishedDocument,
  onRefreshDocuments,
  onSelectJob,
  onSubmit,
  selectedJob,
  selectedJobSteps,
}: {
  deletingDocumentId: string | null;
  documents: DocumentListItem[];
  file: File | null;
  isLoadingDocuments: boolean;
  jobs: IngestionJob[];
  loading: boolean;
  logs: UiLog[];
  message: { type: "success" | "error"; text: string } | null;
  onFileChange: (event: ChangeEvent<HTMLInputElement>) => void;
  onDeleteDocument: (job: IngestionJob) => void;
  onDeletePublishedDocument: (documentId: string) => void;
  onRefreshDocuments: () => void;
  onSelectJob: (jobId: string) => void;
  onSubmit: () => void;
  selectedJob: IngestionJob | null;
  selectedJobSteps: DebugStep[];
}) {
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

      <section className="space-y-5">
        <Card className="bg-white shadow-sm">
          <CardHeader>
            <div className="flex items-start justify-between gap-4">
              <div>
                <CardTitle>Queue Monitor Pipeline</CardTitle>
                <CardDescription>
                  Selected job: {selectedJob ? selectedJob.filename : "No job selected"}
                </CardDescription>
              </div>
              {selectedJob ? <StatusBadge state={normalizeState(selectedJob.status)} /> : null}
            </div>
          </CardHeader>
          <CardContent>
            <PipelineStrip
              dark
              onStepFocus={() => undefined}
              runningStep={null}
              steps={selectedJobSteps}
            />
          </CardContent>
        </Card>

        <Card className="bg-white shadow-sm">
          <CardHeader>
            <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
              <div>
                <CardTitle>Published Documents</CardTitle>
                <CardDescription>
                  Persistent documents from the database, including uploads from previous backend runs.
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
                documents.map((document) => (
                  <article
                    className="rounded-xl border border-slate-100 bg-white px-4 py-3 transition-colors"
                    key={document.document_id}
                  >
                    <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
                      <div className="min-w-0">
                        <div className="flex flex-wrap items-center gap-2">
                          <p className="truncate text-sm font-semibold text-slate-800">
                            {document.title}
                          </p>
                          <StatusBadge state={normalizeState(document.status)} />
                        </div>
                        <p className="mt-1 truncate text-xs text-slate-500">
                          {document.filename ?? "No file name"} / {compactId(document.document_id)}
                        </p>
                      </div>
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
                  </article>
                ))
              )}
            </div>
          </CardContent>
        </Card>

        <Card className="bg-white shadow-sm">
          <CardHeader>
            <CardTitle>Recent Queue Jobs</CardTitle>
            <CardDescription>Runtime queue history for the current backend process.</CardDescription>
          </CardHeader>
          <CardContent>
            <div className="space-y-2">
              {jobs.length === 0 ? (
                <EmptyState message="No queue jobs in the current backend run." />
              ) : (
                jobs.map((job) => {
                  const stats = summarizeIngestionJob(job);
                  return (
                    <article
                      className={cn(
                        "rounded-xl border px-4 py-3 transition-colors",
                        selectedJob?.job_id === job.job_id
                          ? "border-cyan-200 bg-cyan-50"
                          : "border-slate-100 bg-white",
                      )}
                      key={job.job_id}
                    >
                      <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
                        <div className="min-w-0">
                          <p className="truncate text-sm font-semibold text-slate-800">
                            {job.filename}
                          </p>
                          <p className="mt-1 font-mono text-xs text-slate-500">
                            {compactId(job.job_id)}
                            {job.document_id ? ` / ${compactId(job.document_id)}` : ""}
                          </p>
                        </div>
                        <div className="flex flex-wrap items-center gap-2">
                          <StatusBadge state={normalizeState(job.status)} />
                          <Button
                            className="border-slate-200 bg-white text-slate-700 hover:bg-slate-50"
                            onClick={() => onSelectJob(job.job_id)}
                            type="button"
                            variant="outline"
                          >
                            Select
                          </Button>
                          <Button
                            className="border-rose-200 bg-white text-rose-700 hover:bg-rose-50"
                            disabled={!job.document_id || deletingDocumentId === job.document_id}
                            onClick={() => onDeleteDocument(job)}
                            title="Delete from MinIO, Qdrant, and database"
                            type="button"
                            variant="outline"
                          >
                            {deletingDocumentId === job.document_id ? (
                              <Loader2 className="h-4 w-4 animate-spin" />
                            ) : (
                              <Trash2 className="h-4 w-4" />
                            )}
                            Delete
                          </Button>
                        </div>
                      </div>
                      <dl className="mt-3 grid gap-2 text-xs text-slate-600 sm:grid-cols-3">
                        <QueueMetric label="Parsed chars" value={stats.parsedChars} />
                        <QueueMetric label="Chunks" value={stats.chunks} />
                        <QueueMetric label="Vector indexed" value={stats.indexed} />
                      </dl>
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

function SettingsPanel({
  answerMode,
  answerStyle,
  candidateK,
  chunkOverlap,
  chunkSize,
  chunkMode,
  memorySettings,
  memoryTopK,
  maxContextChars,
  runtimeConfig,
  setAnswerMode,
  setAnswerStyle,
  profile,
  setProfile,
  setMaxContextChars,
  setCandidateK,
  setChunkOverlap,
  setChunkSize,
  setChunkMode,
  setMemoryTopK,
  setStreamingEnabled,
  setTopK,
  setTypewriterEnabled,
  setTypewriterSpeed,
  setUseMem0,
  setUseGraph,
  setUseMemory,
  streamingEnabled,
  topK,
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
  answerMode: AnswerMode;
  answerStyle: AnswerStyle;
  candidateK: number;
  chunkOverlap: number;
  chunkSize: number;
  chunkMode: ChunkMode;
  memorySettings: MemorySettings | null;
  memoryTopK: number;
  maxContextChars: number;
  runtimeConfig: RuntimeConfigResponse | null;
  setAnswerMode: (value: AnswerMode) => void;
  setAnswerStyle: (value: AnswerStyle) => void;
  profile: DocumentProfile;
  setProfile: (value: DocumentProfile) => void;
  setMaxContextChars: (value: number) => void;
  setCandidateK: (value: number) => void;
  setChunkOverlap: (value: number) => void;
  setChunkSize: (value: number) => void;
  setChunkMode: (value: ChunkMode) => void;
  setMemoryTopK: (value: number) => void;
  setStreamingEnabled: (value: boolean) => void;
  setTopK: (value: number) => void;
  setTypewriterEnabled: (value: boolean) => void;
  setTypewriterSpeed: (value: TypewriterSpeed) => void;
  setUseMem0: (value: boolean) => void;
  setUseGraph: (value: boolean) => void;
  setUseMemory: (value: boolean) => void;
  streamingEnabled: boolean;
  topK: number;
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
  const chunkModes: { value: ChunkMode; label: string; hint: string }[] = [
    {
      value: "recursive",
      label: "Recursive",
      hint: "Generic character-window chunking.",
    },
    {
      value: "legal_article",
      label: "Legal / Administrative",
      hint: "Split by Điều, preserve chapter/article metadata.",
    },
  ];
  const answerStyles: { value: AnswerStyle; label: string; hint: string }[] = [
    { value: "concise", label: "Concise", hint: "Short 1–2 sentence reply." },
    {
      value: "detailed",
      label: "Detailed",
      hint: "Thorough explanation, exact wording.",
    },
    {
      value: "policy_explainer",
      label: "Policy explainer",
      hint: "Direct answer + related cases + notes (legal).",
    },
  ];
  const answerModes: { value: AnswerMode; label: string; hint: string }[] = [
    {
      value: "generative",
      label: "Generative",
      hint: "Natural answer, summarizes context.",
    },
    {
      value: "extractive",
      label: "Extractive",
      hint: "Exact text only, no paraphrasing.",
    },
    {
      value: "hybrid",
      label: "Hybrid",
      hint: "Concise answer plus supporting text.",
    },
  ];
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
          RAG Runtime Settings
        </CardTitle>
        <CardDescription>
          Tune retrieval and chunking. Model details are read-only.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-5">
        <div>
          <span className="text-xs font-semibold uppercase tracking-wider text-slate-500">
            Document profile
          </span>
          <div className="mt-2 flex flex-wrap gap-2">
            {(
              [
                "auto",
                "legal_admin",
                "general",
                "technical",
                "faq",
                "spreadsheet",
              ] as const
            ).map((value) => (
              <button
                className={cn(
                  "cursor-pointer rounded-lg border px-3 py-1.5 text-sm font-medium transition-colors",
                  profile === value
                    ? "border-cyan-300 bg-cyan-50 text-cyan-800"
                    : "border-slate-200 bg-white text-slate-600 hover:bg-slate-50",
                )}
                key={value}
                onClick={() => setProfile(value)}
                type="button"
              >
                {value === "auto" ? "Auto" : value.replace("_", " / ")}
              </button>
            ))}
          </div>
          <p className="mt-2 text-xs text-slate-500">
            Selecting a profile fills the settings below; you can still override any
            value. Auto detects the profile from the document.
          </p>
        </div>

        <div>
          <span className="text-xs font-semibold uppercase tracking-wider text-slate-500">
            Answer mode
          </span>
          <div className="mt-2 grid gap-2 sm:grid-cols-3">
            {answerModes.map((mode) => (
              <label
                className={cn(
                  "flex cursor-pointer flex-col gap-1 rounded-xl border px-3 py-2 transition-colors",
                  answerMode === mode.value
                    ? "border-cyan-300 bg-cyan-50"
                    : "border-slate-200 bg-white hover:bg-slate-50",
                )}
                key={mode.value}
              >
                <span className="flex items-center gap-2 text-sm font-semibold text-slate-800">
                  <input
                    checked={answerMode === mode.value}
                    className="h-4 w-4 cursor-pointer accent-cyan-600"
                    name="answer-mode"
                    onChange={() => setAnswerMode(mode.value)}
                    type="radio"
                    value={mode.value}
                  />
                  {mode.label}
                </span>
                <span className="text-xs text-slate-500">{mode.hint}</span>
              </label>
            ))}
          </div>
        </div>

        <div>
          <span className="text-xs font-semibold uppercase tracking-wider text-slate-500">
            Answer style
          </span>
          <div className="mt-2 grid gap-2 sm:grid-cols-3">
            {answerStyles.map((style) => (
              <label
                className={cn(
                  "flex cursor-pointer flex-col gap-1 rounded-xl border px-3 py-2 transition-colors",
                  answerStyle === style.value
                    ? "border-cyan-300 bg-cyan-50"
                    : "border-slate-200 bg-white hover:bg-slate-50",
                )}
                key={style.value}
              >
                <span className="flex items-center gap-2 text-sm font-semibold text-slate-800">
                  <input
                    checked={answerStyle === style.value}
                    className="h-4 w-4 cursor-pointer accent-cyan-600"
                    name="answer-style"
                    onChange={() => setAnswerStyle(style.value)}
                    type="radio"
                    value={style.value}
                  />
                  {style.label}
                </span>
                <span className="text-xs text-slate-500">{style.hint}</span>
              </label>
            ))}
          </div>
        </div>

        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <NumberField
            label="Chunk size"
            max={4000}
            min={300}
            onChange={setChunkSize}
            value={chunkSize}
          />
          <NumberField
            label="Chunk overlap"
            max={Math.floor(chunkSize / 2)}
            min={0}
            onChange={setChunkOverlap}
            value={chunkOverlap}
          />
          <NumberField
            label="top_k"
            max={50}
            min={1}
            onChange={setTopK}
            value={topK}
          />
          <NumberField
            label="candidate_k"
            max={200}
            min={1}
            onChange={setCandidateK}
            value={candidateK}
          />
          <NumberField
            label="max_context_chars"
            max={20000}
            min={500}
            onChange={setMaxContextChars}
            value={maxContextChars}
          />
        </div>

        <div>
          <span className="text-xs font-semibold uppercase tracking-wider text-slate-500">
            Chunk mode
          </span>
          <div className="mt-2 grid gap-2 sm:grid-cols-2">
            {chunkModes.map((mode) => (
              <label
                className={cn(
                  "flex cursor-pointer flex-col gap-1 rounded-xl border px-3 py-2 transition-colors",
                  chunkMode === mode.value
                    ? "border-cyan-300 bg-cyan-50"
                    : "border-slate-200 bg-white hover:bg-slate-50",
                )}
                key={mode.value}
              >
                <span className="flex items-center gap-2 text-sm font-semibold text-slate-800">
                  <input
                    checked={chunkMode === mode.value}
                    className="h-4 w-4 cursor-pointer accent-cyan-600"
                    name="chunk-mode"
                    onChange={() => setChunkMode(mode.value)}
                    type="radio"
                    value={mode.value}
                  />
                  {mode.label}
                </span>
                <span className="text-xs text-slate-500">{mode.hint}</span>
              </label>
            ))}
          </div>
        </div>

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
  dark,
  onStepFocus,
  runningStep,
  steps,
}: {
  dark?: boolean;
  onStepFocus: (step: PipelineStepKey) => void;
  runningStep: PipelineStepKey | null;
  steps: DebugStep[];
}) {
  return (
    <div
      className={cn(
        "grid gap-3 rounded-xl p-4 shadow-inner md:grid-cols-5",
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
                "flex min-h-32 w-full flex-col justify-between rounded-lg border p-3 text-left transition-colors",
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

function StepButton({
  disabled,
  icon: Icon,
  label,
  loading,
  onClick,
}: {
  disabled: boolean;
  icon: typeof Upload;
  label: string;
  loading: boolean;
  onClick: () => void;
}) {
  return (
    <Button
      className="justify-start bg-[#0d3b4c] text-white hover:bg-[#114e63]"
      disabled={disabled || loading}
      onClick={onClick}
      type="button"
    >
      {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Icon className="h-4 w-4" />}
      {label}
    </Button>
  );
}

function RuntimePanel({
  config,
  expanded,
  graphHealth,
  graphHealthBusy,
  onCheckGraphHealth,
  onToggle,
}: {
  config: RuntimeConfigResponse | null;
  expanded: boolean;
  graphHealth: GraphHealthResponse | null;
  graphHealthBusy: boolean;
  onCheckGraphHealth: () => void;
  onToggle: () => void;
}) {
  const rows = config
    ? [
        ["Embedding", config.embedding_provider],
        ["Embedding model", config.embedding_model ?? "Not set"],
        ["Dimension", String(config.embedding_dimension)],
        ["Reranker", config.reranker_provider],
        ["Reranker model", config.reranker_model ?? "Not set"],
        ["LLM", config.llm_provider],
        ["LLM model", config.llm_model ?? "Not set"],
        ["Collection", config.vector_collection_name],
        ["Auto recreate", config.auto_recreate_collection ? "true" : "false"],
        ["Graph", config.graph_provider],
        ["Graph enabled", config.graph_enabled ? "true" : "false"],
        ["Graph expansion", config.graph_expansion_enabled ? "true" : "false"],
        ["Graph depth", String(config.graph_expansion_depth)],
        ["Graph limit", String(config.graph_expansion_limit)],
      ]
    : [];

  return (
    <Card className="bg-white shadow-sm">
      <CardHeader className="pb-3">
        <button
          className="flex w-full cursor-pointer items-center justify-between gap-3 text-left"
          onClick={onToggle}
          type="button"
        >
          <span>
            <CardTitle className="flex items-center gap-2">
              <ServerCog className="h-5 w-5 text-cyan-700" />
              Runtime Config
            </CardTitle>
            <CardDescription>Safe provider details. API keys are hidden.</CardDescription>
          </span>
          <ChevronRight
            className={cn(
              "h-4 w-4 text-slate-400 transition-transform",
              expanded && "rotate-90",
            )}
          />
        </button>
      </CardHeader>
      {expanded ? (
        <CardContent>
          <div className="mb-4 flex items-center justify-between gap-3 rounded-xl border border-slate-200 bg-slate-50 p-3">
            <div>
              <p className="text-sm font-semibold text-slate-800">Graph health</p>
              <p className="text-xs text-slate-500">
                {graphHealth?.message ?? "Run a live Neo4j connectivity check."}
              </p>
            </div>
            <Button
              className="border-slate-200 bg-white text-slate-700 hover:bg-slate-50"
              disabled={graphHealthBusy}
              onClick={onCheckGraphHealth}
              type="button"
              variant="outline"
            >
              {graphHealthBusy ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <GitBranch className="h-4 w-4" />
              )}
              Check
            </Button>
          </div>
          {config ? (
            <dl className="space-y-2">
              {rows.map(([label, value]) => (
                <KeyValue key={label} label={label} value={value} />
              ))}
            </dl>
          ) : (
            <EmptyState message="Runtime config has not loaded." />
          )}
        </CardContent>
      ) : null}
    </Card>
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

function ParsePreview({ text }: { text: string }) {
  if (!text) {
    return <EmptyState message="Parsed text will appear after Parse succeeds." />;
  }

  return (
    <pre className="max-h-[560px] overflow-auto rounded-xl bg-slate-950 p-5 text-sm leading-6 text-slate-100">
      {text}
    </pre>
  );
}

function ChunkPreviewList({ chunks }: { chunks: ChunkPreview[] }) {
  if (chunks.length === 0) {
    return <EmptyState message="Chunk previews will appear after Chunk succeeds." />;
  }

  return (
    <div className="max-h-[560px] space-y-3 overflow-auto">
      {chunks.map((chunk) => (
        <article
          className={cn(
            "rounded-xl border border-slate-100 p-4",
            chunk.chunk_index % 2 === 0 ? "bg-slate-50" : "bg-white",
          )}
          key={`${chunk.chunk_index}-${chunk.start_char}`}
        >
          <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
            <span className="inline-flex items-center gap-2 text-sm font-semibold text-slate-800">
              <Rows3 className="h-4 w-4 text-cyan-700" />
              Chunk {chunk.chunk_index}
            </span>
            <span className="font-mono text-xs text-slate-500">
              {chunk.start_char}-{chunk.end_char} / {chunk.content.length} chars
            </span>
          </div>
          <p className="whitespace-pre-wrap border-l-2 border-cyan-600/40 pl-4 text-sm leading-6 text-slate-700">
            {chunk.content}
          </p>
        </article>
      ))}
    </div>
  );
}

function ErrorPreview({ message }: { message: string }) {
  return (
    <div
      className="rounded-xl border border-rose-200/70 bg-rose-50 p-5 text-sm text-rose-700"
      role="alert"
    >
      <div className="flex items-center gap-2 font-semibold">
        <AlertCircle className="h-4 w-4" />
        Step failed
      </div>
      <p className="mt-2 leading-6">{message}</p>
    </div>
  );
}

function TabButton({
  active,
  label,
  onClick,
}: {
  active: boolean;
  label: string;
  onClick: () => void;
}) {
  return (
    <button
      className={cn(
        "cursor-pointer rounded-md px-3 py-1.5 text-sm font-medium transition-colors",
        active
          ? "bg-white text-slate-900 shadow-sm"
          : "text-slate-500 hover:text-slate-800",
      )}
      onClick={onClick}
      type="button"
    >
      {label}
    </button>
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

function buildDocumentDebugSteps(
  detail: DocumentDetailResponse | null,
  graphEnabled: boolean,
): DebugStep[] {
  const latestByAction = new Map<string, { status: string; message: string | null }>();
  for (const log of detail?.pipeline_logs ?? []) {
    if (!latestByAction.has(log.action)) {
      latestByAction.set(log.action, { status: log.status, message: log.message });
    }
  }

  return pipelineDefinitions.map((definition) => {
    let state: RunState = "idle";
    if (definition.key === "upload") {
      state = detail ? "succeeded" : "idle";
    } else if (definition.key === "parse") {
      state =
        latestByAction.get("parse")?.status === "failed"
          ? "failed"
          : detail && ["parsed", "chunked", "indexed"].includes(detail.status)
            ? "succeeded"
            : "idle";
    } else if (definition.key === "chunk") {
      state =
        latestByAction.get("chunk")?.status === "failed"
          ? "failed"
          : detail && ["chunked", "indexed"].includes(detail.status)
            ? "succeeded"
            : "idle";
    } else if (definition.key === "index") {
      state =
        latestByAction.get("index_vector")?.status === "failed"
          ? "failed"
          : detail && detail.vector_indexed_count !== null
            ? "succeeded"
            : "idle";
    } else if (definition.key === "graph") {
      state =
        graphEnabled && detail?.graph_status?.graph_indexed ? "succeeded" : "idle";
    }

    return {
      key: definition.key,
      label: definition.label,
      state,
      durationMs: null,
      output: {},
      error:
        state === "failed"
          ? latestByAction.get(
              definition.key === "index" ? "index_vector" : definition.key,
            )?.message ?? null
          : null,
    };
  });
}

function buildPipelineStepsFromJob(job: IngestionJob | null): DebugStep[] {
  return pipelineDefinitions.map((definition) => {
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

function summarizeIngestionJob(job: IngestionJob): {
  parsedChars: string;
  chunks: string;
  indexed: string;
} {
  const parseOutput = findJobStep(job.steps, "parse")?.output ?? {};
  const chunkOutput = findJobStep(job.steps, "chunk")?.output ?? {};
  const indexOutput = findJobStep(job.steps, "index")?.output ?? {};

  return {
    parsedChars: formatOptionalCount(parseOutput.character_count),
    chunks: formatOptionalCount(chunkOutput.chunk_count),
    indexed: formatOptionalCount(indexOutput.indexed_chunk_count),
  };
}

function formatOptionalCount(value: unknown): string {
  if (typeof value === "number" && Number.isFinite(value)) {
    return value.toLocaleString();
  }
  if (typeof value === "string" && value.trim() && !Number.isNaN(Number(value))) {
    return Number(value).toLocaleString();
  }
  return "--";
}

function findJobStep(
  steps: IngestionStep[],
  key: PipelineStepKey,
): IngestionStep | undefined {
  const aliases: Record<PipelineStepKey, string[]> = {
    upload: ["upload"],
    parse: ["parse"],
    chunk: ["chunk"],
    index: ["index", "embed", "embed_index", "index-vector", "index_vector"],
    graph: ["graph", "index-graph", "graph_index"],
  };

  return steps.find((step) => aliases[key].includes(step.name));
}

function mapIngestionLogs(logs: IngestionLog[]): UiLog[] {
  return logs
    .slice()
    .reverse()
    .map((log, index) => ({
      id: `${log.timestamp}-${index}`,
      timestamp: log.timestamp,
      source: "auto",
      step: log.step,
      level: normalizeLogLevel(log.level),
      message: log.message,
      durationMs: log.duration_ms,
    }));
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

function normalizeLogLevel(level: string): UiLog["level"] {
  if (level === "success") {
    return "success";
  }
  if (level === "error") {
    return "error";
  }
  return "info";
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

function compactId(id: string): string {
  if (id.length <= 12) {
    return id;
  }
  return `${id.slice(0, 8)}...${id.slice(-4)}`;
}
