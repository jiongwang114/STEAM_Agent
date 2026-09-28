const API_BASE = import.meta.env.VITE_API_BASE || "";

function readError(payload, status) {
  const error = payload?.error || payload?.detail || {};
  if (typeof error === "string") return { code: "request_failed", message: error, details: [] };
  return {
    code: error.code || (status === 401 ? "authentication_required" : "request_failed"),
    message: error.message || (status >= 500 ? "服务器暂时不可用" : "请求失败"),
    details: error.details || [],
  };
}

export class ApiError extends Error {
  constructor(status, payload) {
    const error = readError(payload, status);
    super(error.message);
    this.name = "ApiError";
    this.status = status;
    this.code = error.code;
    this.details = error.details;
  }
}

async function request(path, options = {}) {
  const headers = { Accept: "application/json", ...(options.headers || {}) };
  if (options.body) headers["Content-Type"] = "application/json";
  const response = await fetch(`${API_BASE}${path}`, {
    credentials: "include",
    ...options,
    headers,
  });
  const payload = response.status === 204 ? {} : await response.json().catch(() => ({}));
  if (!response.ok) throw new ApiError(response.status, payload);
  return payload;
}

export const api = {
  getUserInfo: () => request("/auth/user-info"),
  getThreads: () => request("/threads"),
  getMessages: (threadId) => request(`/messages?thread_id=${encodeURIComponent(threadId)}`),
  cancelRun: (threadId) => request(`/chat/runs/${encodeURIComponent(threadId)}/cancel`, { method: "POST" }),
  setTitle: (threadId, title) => request("/thread-title", {
    method: "POST",
    body: JSON.stringify({ thread_id: threadId, title }),
  }),
  deleteThread: (threadId) => request(`/threads?thread_id=${encodeURIComponent(threadId)}`, { method: "DELETE" }),
  retryThreadCleanup: (threadId) => request(`/threads/${encodeURIComponent(threadId)}/cleanup/retry`, { method: "POST" }),
  login: (body) => request("/auth/login", { method: "POST", body: JSON.stringify(body) }),
  register: (body) => request("/auth/register", { method: "POST", body: JSON.stringify(body) }),
  logout: () => request("/auth/logout", { method: "POST" }),
  revokeAll: () => request("/auth/revoke-all", { method: "POST" }),
  setTheme: (theme) => request("/auth/theme", { method: "POST", body: JSON.stringify({ theme }) }),
  bindSteam: (steamId) => request("/bind-steam", { method: "POST", body: JSON.stringify({ steam_id: steamId }) }),
};

async function readEventStream(response, onEvent) {
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new ApiError(response.status, payload);
  }
  if (!response.body) throw new Error("浏览器不支持流式响应");

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  const dispatch = (chunk) => {
    const data = chunk
      .split(/\r?\n/)
      .filter((line) => line.startsWith("data:"))
      .map((line) => line.slice(5).trimStart())
      .join("\n");
    if (!data) return;
    try {
      onEvent(JSON.parse(data));
    } catch {
      throw new Error("服务器返回了无法识别的流式数据");
    }
  };

  try {
    while (true) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
      const chunks = buffer.split(/\r?\n\r?\n/);
      buffer = chunks.pop() || "";
      chunks.filter(Boolean).forEach(dispatch);
      if (done) {
        if (buffer.trim()) dispatch(buffer);
        break;
      }
    }
  } finally {
    reader.releaseLock();
  }
}

export async function streamChat(body, { signal, onEvent }) {
  const response = await fetch(`${API_BASE}/chat/stream`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify(body),
    signal,
  });
  return readEventStream(response, onEvent);
}

export async function attachChatRun(threadId, { signal, onEvent }) {
  const response = await fetch(`${API_BASE}/chat/runs/${encodeURIComponent(threadId)}`, {
    credentials: "include",
    headers: { Accept: "text/event-stream" },
    signal,
  });
  if (response.status === 204) return false;
  await readEventStream(response, onEvent);
  return true;
}
