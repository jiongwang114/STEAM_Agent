import { Menu, RefreshCw } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import AuthModal from "./features/auth/AuthModal";
import ChatWorkspace from "./features/chat/ChatWorkspace";
import SettingsPanel from "./features/settings/SettingsPanel";
import ThreadSidebar from "./features/threads/ThreadSidebar";
import { api, attachChatRun, streamChat } from "./services/api";

function newThreadId() {
  return `thread_${crypto.randomUUID?.() || `${Date.now()}_${Math.random().toString(16).slice(2)}`}`;
}

function normalizeMessages(messages) {
  return messages.map((item, index) => ({
    ...item,
    id: `${item.turn || "message"}-${item.role}-${index}`,
  }));
}

const terminationReasonMessages = {
  insufficient_evidence: "当前检索证据不足，无法可靠推荐；请补充平台、预算或玩法。",
  deadline: "检索超时，已停止本轮；请缩小条件后重试。",
  agent_timeout: "检索超时，已停止本轮；请缩小条件后重试。",
  token_budget: "本轮检索已达到预算，请缩小条件后重试。",
  model_error: "模型服务暂时不可用，请稍后重试。",
  archive_failed: "回答已生成，但保存历史失败；请稍后重试。",
  cancelled: "已停止生成，当前回答可能不完整。",
};

function explainTerminationReason(reason) {
  return terminationReasonMessages[reason] || "回答可能没有完整完成，请稍后重试。";
}

export default function App() {
  const [authLoading, setAuthLoading] = useState(true);
  const [user, setUser] = useState(null);
  const [threads, setThreads] = useState([]);
  const [threadsLoading, setThreadsLoading] = useState(false);
  const [threadError, setThreadError] = useState("");
  const [currentId, setCurrentId] = useState("");
  const [messagesByThread, setMessagesByThread] = useState({});
  const [runsByThread, setRunsByThread] = useState({});
  const [messagesLoading, setMessagesLoading] = useState(false);
  const [messageError, setMessageError] = useState("");
  const [input, setInput] = useState("");
  const [notice, setNotice] = useState("");
  const [authMode, setAuthMode] = useState("");
  const [authError, setAuthError] = useState("");
  const [authBusy, setAuthBusy] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [settingsError, setSettingsError] = useState("");
  const [settingsBusy, setSettingsBusy] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const runSubscriptionsRef = useRef(new Map());
  const loadRequestRef = useRef(0);
  const currentIdRef = useRef(currentId);
  currentIdRef.current = currentId;

  const updateThreadMessages = useCallback((threadId, updater) => {
    setMessagesByThread((previous) => ({
      ...previous,
      [threadId]: updater(previous[threadId] || []),
    }));
  }, []);

  const setThreadRun = useCallback((threadId, run) => {
    setRunsByThread((previous) => {
      const next = { ...previous };
      if (run) next[threadId] = run;
      else delete next[threadId];
      return next;
    });
  }, []);

  const showAuthExpired = useCallback(() => {
    for (const subscription of runSubscriptionsRef.current.values()) subscription.controller.abort();
    runSubscriptionsRef.current.clear();
    setUser(null);
    setSettingsOpen(false);
    setThreads([]);
    setMessagesByThread({});
    setRunsByThread({});
    setCurrentId("");
    setAuthError("登录状态已失效，请重新登录。");
    setAuthMode("login");
  }, []);

  const mergeRunSnapshot = useCallback((threadId, snapshot, preferredAssistantId) => {
    if (!snapshot?.run_id) return;
    if (snapshot.error?.message && currentIdRef.current === threadId) setNotice(snapshot.error.message);
    const runId = snapshot.run_id;
    const reply = String(snapshot.reply || "");
    const isRunning = snapshot.status === "running";
    const assistantId = preferredAssistantId || `run-${runId}-assistant`;
    setThreadRun(threadId, isRunning ? {
      runId,
      status: "running",
      streamStatus: snapshot.stream_status || "正在处理",
    } : null);

    updateThreadMessages(threadId, (previous) => {
      const next = [...previous];
      let assistantIndex = next.findIndex((item) => item.id === assistantId || item.run_id === runId);
      if (assistantIndex < 0 && isRunning) {
        assistantIndex = next.findIndex((item, index) => (
          item.role === "assistant"
          && item.pending
          && next[index - 1]?.role === "user"
          && next[index - 1]?.content === snapshot.message
        ));
      }
      if (assistantIndex < 0 && !isRunning) {
        const archivedIndex = next.findIndex((item, index) => (
          item.role === "user"
          && item.content === snapshot.message
          && next[index + 1]?.role === "assistant"
          && next[index + 1]?.content === reply
        ));
        if (archivedIndex >= 0) return previous;
      }
      if (assistantIndex < 0) {
        const userExists = next.some((item) => item.run_id === runId && item.role === "user");
        if (!userExists) {
          next.push({
            id: `run-${runId}-user`,
            run_id: runId,
            role: "user",
            content: snapshot.message,
            time: new Date().toISOString(),
          });
        }
        assistantIndex = next.length;
        next.push({ id: assistantId, run_id: runId, role: "assistant", content: "" });
      }
      const prior = next[assistantIndex];
      next[assistantIndex] = {
        ...prior,
        id: assistantId,
        run_id: runId,
        content: reply,
        pending: isRunning,
        progress: snapshot.progress || prior.progress || [],
        ...(snapshot.result ? {
          execution: snapshot.result.execution,
          degraded: snapshot.result.status === "degraded",
        } : {}),
      };
      return next;
    });
  }, [setThreadRun, updateThreadMessages]);

  const handleRunEvent = useCallback((threadId, event, preferredAssistantId) => {
    const data = event.data || {};
    if (event.event === "snapshot") {
      mergeRunSnapshot(threadId, data, preferredAssistantId);
      setThreads((previous) => previous.map((thread) => (
        thread.thread_id === threadId ? { ...thread, is_running: data.status === "running" } : thread
      )));
      return;
    }
    const subscription = runSubscriptionsRef.current.get(threadId);
    if (event.event === "done" || event.event === "cancelled") {
      if (subscription) subscription.terminal = true;
      setThreadRun(threadId, null);
      setThreads((previous) => previous.map((thread) => (
        thread.thread_id === threadId ? { ...thread, is_running: false } : thread
      )));
    }
    if (event.event === "status") {
      const label = String(data || "正在处理");
      setRunsByThread((previous) => ({
        ...previous,
        [threadId]: { ...previous[threadId], status: "running", streamStatus: label },
      }));
      updateThreadMessages(threadId, (previous) => previous.map((item) => {
        if (item.id !== preferredAssistantId && item.run_id !== subscription?.runId) return item;
        const progress = (item.progress || []).map((step) => (
          step.status === "in_progress" ? { ...step, status: "completed" } : step
        ));
        return { ...item, progress: [...progress, { label, status: "in_progress" }] };
      }));
    } else if (event.event === "token") {
      updateThreadMessages(threadId, (previous) => previous.map((item) => (
        item.id === preferredAssistantId || item.run_id === subscription?.runId
          ? { ...item, content: `${item.content}${data || ""}` }
          : item
      )));
    } else if (event.event === "error") {
      if (currentIdRef.current === threadId) setNotice(data.message || "回答过程中出现问题");
    } else if (event.event === "done") {
      const result = data;
      updateThreadMessages(threadId, (previous) => previous.map((item) => (
        item.id === preferredAssistantId || item.run_id === subscription?.runId
          ? {
            ...item,
            content: result.reply || item.content,
            execution: result.execution,
            degraded: result.status === "degraded",
            pending: false,
            progress: (item.progress || []).map((step) => ({ ...step, status: "completed" })),
          }
          : item
      )));
      if (result.status === "degraded" && currentIdRef.current === threadId) {
        setNotice(explainTerminationReason(result.run_metadata?.termination_reason));
      }
    } else if (event.event === "cancelled") {
      updateThreadMessages(threadId, (previous) => previous.map((item) => (
        item.id === preferredAssistantId || item.run_id === subscription?.runId
          ? {
            ...item,
            content: item.content || "已停止生成。",
            degraded: true,
            pending: false,
            progress: (item.progress || []).map((step) => ({ ...step, status: "failed" })),
          }
          : item
      )));
      if (currentIdRef.current === threadId) setNotice("已停止生成，已保留当前回答。");
    }
  }, [mergeRunSnapshot, setThreadRun, updateThreadMessages]);

  const subscribeToRun = useCallback((threadId) => {
    const existing = runSubscriptionsRef.current.get(threadId);
    if (existing?.active) return;
    const subscription = { active: true, controller: new AbortController(), terminal: false };
    runSubscriptionsRef.current.set(threadId, subscription);
    attachChatRun(threadId, {
      signal: subscription.controller.signal,
      onEvent: (event) => {
        if (event.event === "snapshot") subscription.runId = event.data?.run_id;
        handleRunEvent(threadId, event);
      },
    }).then((found) => {
      if (found) return;
      setThreadRun(threadId, null);
      setThreads((previous) => previous.map((thread) => (
        thread.thread_id === threadId ? { ...thread, is_running: false } : thread
      )));
      updateThreadMessages(threadId, (previous) => previous.map((item) => (
        item.pending
          ? {
            ...item,
            content: item.content || "这次没有拿到回答。",
            pending: false,
            degraded: true,
            progress: (item.progress || []).map((step) => ({ ...step, status: "failed" })),
          }
          : item
      )));
    }).catch((error) => {
      if (error.name === "AbortError") return;
      if (error.status === 401) showAuthExpired();
      else if (currentIdRef.current === threadId) setNotice(error.message || "正在恢复回答连接");
    }).finally(() => {
      subscription.active = false;
      if (runSubscriptionsRef.current.get(threadId) === subscription) {
        runSubscriptionsRef.current.delete(threadId);
      }
    });
  }, [handleRunEvent, setThreadRun, showAuthExpired, updateThreadMessages]);

  const applyUser = useCallback((payload) => {
    const nextUser = payload?.user || null;
    setUser(nextUser);
    document.documentElement.dataset.theme = nextUser?.theme || "light";
    return nextUser;
  }, []);

  const refreshThreads = useCallback(async () => {
    setThreadsLoading(true);
    setThreadError("");
    try {
      const payload = await api.getThreads();
      const nextThreads = payload.threads || [];
      setThreads(nextThreads);
      return nextThreads;
    } catch (error) {
      if (error.status === 401) showAuthExpired();
      else setThreadError(error.message || "会话列表暂时无法加载");
      throw error;
    } finally {
      setThreadsLoading(false);
    }
  }, [showAuthExpired]);

  const loadThread = useCallback(async (threadId) => {
    const requestId = ++loadRequestRef.current;
    setCurrentId(threadId);
    setSidebarOpen(false);
    setMessagesLoading(true);
    setMessageError("");
    setNotice("");
    try {
      const payload = await api.getMessages(threadId);
      if (requestId === loadRequestRef.current) {
        if (!runSubscriptionsRef.current.get(threadId)?.active) {
          updateThreadMessages(threadId, () => normalizeMessages(payload.messages || []));
          subscribeToRun(threadId);
        }
      }
    } catch (error) {
      if (requestId !== loadRequestRef.current) return;
      if (error.status === 401) showAuthExpired();
      else setMessageError(error.message || "历史消息暂时无法加载");
    } finally {
      if (requestId === loadRequestRef.current) setMessagesLoading(false);
    }
  }, [showAuthExpired, subscribeToRun, updateThreadMessages]);

  const handleNewThread = useCallback(() => {
    const threadId = newThreadId();
    setCurrentId(threadId);
    updateThreadMessages(threadId, () => []);
    setMessageError("");
    setNotice("");
    setSidebarOpen(false);
  }, [updateThreadMessages]);

  useEffect(() => {
    let active = true;
    api.getUserInfo()
      .then(async (payload) => {
        const nextUser = applyUser(payload);
        if (!active || !nextUser) return;
        try {
          const nextThreads = await refreshThreads();
          if (!active) return;
          const initialThread = nextThreads.find((thread) => thread.is_running) || nextThreads[0];
          if (initialThread) await loadThread(initialThread.thread_id);
          else handleNewThread();
        } catch {
          // Individual request handlers already expose a useful state.
        }
      })
      .catch((error) => {
        if (active && error.status !== 401) setNotice(error.message || "无法确认登录状态");
        if (active && error.status === 401) handleNewThread();
      })
      .finally(() => {
        if (active) setAuthLoading(false);
      });
    return () => {
      active = false;
      for (const subscription of runSubscriptionsRef.current.values()) subscription.controller.abort();
      runSubscriptionsRef.current.clear();
    };
  }, [applyUser, handleNewThread, loadThread, refreshThreads]);

  const currentTitle = useMemo(
    () => threads.find((thread) => thread.thread_id === currentId)?.title || "新会话",
    [currentId, threads],
  );
  const messages = messagesByThread[currentId] || [];
  const currentRun = runsByThread[currentId];

  async function handleSend(event) {
    event.preventDefault();
    const message = input.trim();
    if (!message || currentRun?.status === "running") return;
    if (message.length > 6000) {
      setNotice("消息不能超过 6000 个字符。");
      return;
    }
    if (!user) {
      setAuthError("");
      setAuthMode("login");
      return;
    }

    const threadId = currentId || newThreadId();
    setCurrentId(threadId);
    setInput("");
    setNotice("");
    setMessageError("");
    const assistantId = `pending-${Date.now()}`;
    const userMessageId = `user-${Date.now()}`;
    updateThreadMessages(threadId, (previous) => [
      ...previous,
      { id: userMessageId, role: "user", content: message, time: new Date().toISOString() },
      { id: assistantId, role: "assistant", content: "", pending: true, progress: [] },
    ]);
    setThreadRun(threadId, { status: "running", streamStatus: "正在理解问题" });
    setThreads((previous) => {
      const found = previous.some((thread) => thread.thread_id === threadId);
      const updated = previous.map((thread) => (
        thread.thread_id === threadId ? { ...thread, is_running: true } : thread
      ));
      if (found) return updated;
      return [{
        thread_id: threadId,
        title: message.slice(0, 50) || "新会话",
        last_active: new Date().toISOString(),
        msg_count: 0,
        is_running: true,
      }, ...updated];
    });

    const controller = new AbortController();
    const subscription = { active: true, controller, terminal: false, runId: null };
    runSubscriptionsRef.current.set(threadId, subscription);
    let reconnect = false;
    try {
      const streamPromise = streamChat({ thread_id: threadId, message }, {
        signal: controller.signal,
        onEvent: (event) => {
          if (event.event === "snapshot") subscription.runId = event.data?.run_id;
          handleRunEvent(threadId, event, assistantId);
        },
      });
      void refreshThreads().catch(() => {});
      await streamPromise;
      await refreshThreads();
    } catch (error) {
      if (error.status === 401) {
        showAuthExpired();
      } else if (error.name === "AbortError") {
        return;
      } else if (error.code === "thread_run_active") {
        updateThreadMessages(threadId, (previous) => previous.filter((item) => (
          item.id !== assistantId && item.id !== userMessageId
        )));
        setThreadRun(threadId, { status: "running", streamStatus: "正在恢复回答连接" });
        setNotice("该会话已有回答正在生成，正在恢复进度。");
        reconnect = true;
      } else if (error.status && error.status < 500) {
        updateThreadMessages(threadId, (previous) => previous.map((item) => (
          item.id === assistantId
            ? {
              ...item,
              content: item.content || error.message || "这次没有拿到回答。",
              pending: false,
              degraded: true,
              progress: (item.progress || []).map((step) => ({ ...step, status: "failed" })),
            }
            : item
        )));
        setThreadRun(threadId, null);
        setThreads((previous) => previous.map((thread) => (
          thread.thread_id === threadId ? { ...thread, is_running: false } : thread
        )));
        setNotice(error.message || "这次没有拿到回答。");
      } else {
        setNotice(error.message || "网络连接中断，正在恢复回答");
        setThreadRun(threadId, { status: "running", streamStatus: "正在恢复回答连接" });
        reconnect = true;
      }
    } finally {
      subscription.active = false;
      if (runSubscriptionsRef.current.get(threadId) === subscription) {
        runSubscriptionsRef.current.delete(threadId);
      }
      if (reconnect && !subscription.terminal) subscribeToRun(threadId);
    }
  }

  async function stopStream() {
    if (!currentId) return;
    try {
      await api.cancelRun(currentId);
    } catch (error) {
      if (error.status === 401) showAuthExpired();
      else setNotice(error.message || "停止生成失败");
    }
  }

  async function handleAuthSubmit(body) {
    setAuthBusy(true);
    setAuthError("");
    try {
      const action = authMode === "register" ? api.register : api.login;
      await action(body);
      const payload = await api.getUserInfo();
      applyUser(payload);
      setAuthMode("");
      const nextThreads = await refreshThreads();
      const initialThread = nextThreads.find((thread) => thread.is_running) || nextThreads[0];
      if (initialThread) await loadThread(initialThread.thread_id);
      else handleNewThread();
    } catch (error) {
      setAuthError(error.message || "认证失败，请重试");
    } finally {
      setAuthBusy(false);
    }
  }

  async function handleDelete(thread) {
    if (!window.confirm(`删除“${thread.title || "新会话"}”？`)) return;
    try {
      const payload = await api.deleteThread(thread.thread_id);
      const remaining = threads.filter((item) => item.thread_id !== thread.thread_id);
      setThreads(remaining);
      if (payload.status === "accepted" || payload.pending_layers?.length) {
        setNotice("会话已删除，剩余清理将在后台完成。");
      }
      if (currentId === thread.thread_id) {
        if (remaining[0]) await loadThread(remaining[0].thread_id);
        else handleNewThread();
      }
    } catch (error) {
      if (error.status === 401) showAuthExpired();
      else setNotice(error.message || "会话删除失败");
    }
  }

  async function handleRename(thread) {
    const title = window.prompt("会话标题", thread.title || "新会话")?.trim();
    if (!title || title === thread.title) return;
    try {
      await api.setTitle(thread.thread_id, title);
      setThreads((previous) => previous.map((item) => item.thread_id === thread.thread_id ? { ...item, title } : item));
    } catch (error) {
      if (error.status === 401) showAuthExpired();
      else setNotice(error.message || "会话标题保存失败");
    }
  }

  async function handleTheme(theme) {
    const previousTheme = user?.theme || "light";
    document.documentElement.dataset.theme = theme;
    setSettingsError("");
    try {
      await api.setTheme(theme);
      setUser((previous) => ({ ...previous, theme }));
      return true;
    } catch (error) {
      document.documentElement.dataset.theme = previousTheme;
      if (error.status === 401) showAuthExpired();
      else setSettingsError(error.message || "主题保存失败");
      return false;
    }
  }

  async function handleBind(steamId) {
    setSettingsBusy(true);
    setSettingsError("");
    try {
      const payload = await api.bindSteam(steamId);
      setUser((previous) => ({ ...previous, bound_steam_id: payload.steam_id }));
    } catch (error) {
      if (error.status === 401) showAuthExpired();
      else setSettingsError(error.message || "Steam ID 绑定失败");
    } finally {
      setSettingsBusy(false);
    }
  }

  async function handleLogout(revoke = false) {
    try {
      await (revoke ? api.revokeAll() : api.logout());
    } finally {
      for (const subscription of runSubscriptionsRef.current.values()) subscription.controller.abort();
      runSubscriptionsRef.current.clear();
      setUser(null);
      setSettingsOpen(false);
      setThreads([]);
      setMessagesByThread({});
      setRunsByThread({});
      setCurrentId("");
      setAuthError("");
      setAuthMode("login");
      handleNewThread();
    }
  }

  if (authLoading) {
    return <div className="app-loading"><RefreshCw className="spin" size={18} /> 正在确认登录状态</div>;
  }

  return (
    <div className="app-shell">
      <div className="mobile-topbar">
        <button className="icon-button" type="button" onClick={() => setSidebarOpen(true)} aria-label="打开会话列表" title="打开会话列表"><Menu size={19} /></button>
        <strong>{currentTitle}</strong>
        <button className="avatar-button" type="button" onClick={() => user ? setSettingsOpen(true) : setAuthMode("login")} aria-label={user ? "打开设置" : "登录"} title={user ? "打开设置" : "登录"}>{user ? user.username.slice(0, 1).toUpperCase() : "?"}</button>
      </div>
      {sidebarOpen && <button className="sidebar-backdrop" type="button" onClick={() => setSidebarOpen(false)} aria-label="关闭会话列表" />}
      <ThreadSidebar
        open={sidebarOpen}
        user={user}
        threads={threads}
        loading={threadsLoading}
        error={threadError}
        currentId={currentId}
        onRetry={refreshThreads}
        onSelect={loadThread}
        onNew={handleNewThread}
        onRename={handleRename}
        onDelete={handleDelete}
        onSettings={() => user ? setSettingsOpen(true) : setAuthMode("login")}
        onClose={() => setSidebarOpen(false)}
      />
      <ChatWorkspace
        title={currentTitle}
        user={user}
        messages={messages}
        loading={messagesLoading}
        error={messageError}
        onRetry={() => currentId && loadThread(currentId)}
        streaming={currentRun?.status === "running"}
        streamStatus={currentRun?.streamStatus || ""}
        input={input}
        onInput={setInput}
        onSend={handleSend}
        onStop={stopStream}
        onPrompt={(prompt) => setInput(prompt)}
        onOpenAuth={() => { setAuthError(""); setAuthMode("login"); }}
        onTheme={handleTheme}
      />
      {notice && <div className="notice" role="status" aria-live="polite"><span>{notice}</span><button type="button" onClick={() => setNotice("")} aria-label="关闭提示">×</button></div>}
      {authMode && <AuthModal mode={authMode} onMode={setAuthMode} onSubmit={handleAuthSubmit} onClose={() => setAuthMode("")} error={authError} busy={authBusy} />}
      {settingsOpen && user && <SettingsPanel user={user} onClose={() => setSettingsOpen(false)} onTheme={handleTheme} onBind={handleBind} onLogout={() => handleLogout(false)} onRevoke={() => handleLogout(true)} busy={settingsBusy} error={settingsError} />}
    </div>
  );
}
