import {
  ApiError,
  getStoredAccessToken,
  type AnswerMode,
  type AnswerStyle,
  type DocumentProfile,
  type RagCitation,
} from "@/lib/api";
const configuredApiBaseUrl = process.env.NEXT_PUBLIC_API_BASE_URL?.trim();
const API_BASE_URL = configuredApiBaseUrl
  ? configuredApiBaseUrl.replace(/\/$/, "")
  : "http://localhost:8000";

export type RagStreamScope = {
  document_id?: string;
  organization_id?: string;
  include_descendants?: boolean;
};

export type RagStreamRequest = {
  query: string;
  session_id?: string;
  top_k?: number;
  candidate_k?: number;
  scope?: RagStreamScope;
  use_memory?: boolean;
  use_mem0?: boolean;
  memory_top_k?: number;
  answer_mode?: AnswerMode;
  answer_style?: AnswerStyle;
  max_context_chars?: number;
  profile?: DocumentProfile;
  use_graph?: boolean;
  graph_expansion_depth?: number;
  graph_expansion_limit?: number;
};

export type RagStreamHandlers = {
  onMetadata?: (data: { session_id: string; user_message_id: string }) => void;
  onToken?: (delta: string) => void;
  onCitations?: (citations: RagCitation[]) => void;
  onDone?: (data: { assistant_message_id: string }) => void;
  onError?: (message: string) => void;
  signal?: AbortSignal;
};

type SseEvent = {
  event: string;
  data: string;
};

export async function streamRagChat(
  request: RagStreamRequest,
  handlers: RagStreamHandlers,
): Promise<void> {
  const token = getStoredAccessToken();
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    Accept: "text/event-stream",
  };
  if (token) {
    headers.Authorization = `Bearer ${token}`;
  }

  const response = await fetch(`${API_BASE_URL}/api/chat/rag/stream`, {
    method: "POST",
    headers,
    body: JSON.stringify({ stream: true, ...request }),
    signal: handlers.signal,
  });

  if (!response.ok || !response.body) {
    throw new ApiError(
      response.status,
      `Streaming request failed with status ${response.status}.`,
    );
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) {
        break;
      }
      buffer += decoder.decode(value, { stream: true });
      buffer = drainBuffer(buffer, handlers);
    }
    buffer += decoder.decode();
    drainBuffer(`${buffer}\n\n`, handlers);
  } finally {
    reader.releaseLock();
  }
}

function drainBuffer(buffer: string, handlers: RagStreamHandlers): string {
  let working = buffer;
  let separatorIndex = working.indexOf("\n\n");
  while (separatorIndex !== -1) {
    const rawEvent = working.slice(0, separatorIndex);
    working = working.slice(separatorIndex + 2);
    const parsed = parseEventBlock(rawEvent);
    if (parsed) {
      dispatchEvent(parsed, handlers);
    }
    separatorIndex = working.indexOf("\n\n");
  }
  return working;
}

function parseEventBlock(rawEvent: string): SseEvent | null {
  const lines = rawEvent.split("\n");
  let event = "message";
  const dataLines: string[] = [];

  for (const line of lines) {
    if (line.startsWith("event:")) {
      event = line.slice("event:".length).trim();
    } else if (line.startsWith("data:")) {
      dataLines.push(line.slice("data:".length).trim());
    }
  }

  if (dataLines.length === 0) {
    return null;
  }

  return { event, data: dataLines.join("\n") };
}

function dispatchEvent(parsed: SseEvent, handlers: RagStreamHandlers): void {
  let payload: unknown;
  try {
    payload = JSON.parse(parsed.data);
  } catch {
    return;
  }

  switch (parsed.event) {
    case "metadata":
      handlers.onMetadata?.(
        payload as { session_id: string; user_message_id: string },
      );
      break;
    case "token":
      handlers.onToken?.((payload as { delta: string }).delta);
      break;
    case "citations":
      handlers.onCitations?.(payload as RagCitation[]);
      break;
    case "done":
      handlers.onDone?.(payload as { assistant_message_id: string });
      break;
    case "error":
      handlers.onError?.((payload as { message: string }).message);
      break;
    default:
      break;
  }
}
