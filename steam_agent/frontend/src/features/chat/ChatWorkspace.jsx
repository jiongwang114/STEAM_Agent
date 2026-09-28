import { Bot, Check, CircleAlert, Clock3, LoaderCircle, Moon, RefreshCw, Send, ShieldAlert, Square, Sparkles, Sun, UserRound } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { useEffect, useRef } from "react";

function formatClock(value) {
  if (!value) return "";
  const date = new Date(String(value).replace(" ", "T"));
  return Number.isNaN(date.getTime()) ? "" : date.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
}

function statusLabel(status) {
  if (status === "success" || status === "completed") return "完成";
  if (status === "degraded" || status === "cancelled") return "部分完成";
  return "需关注";
}

function progressStatusLabel(status) {
  if (status === "completed") return "完成";
  if (status === "failed") return "未完成";
  return "进行中";
}

function ExecutionProgress({ execution, progress, pending }) {
  const steps = execution?.steps?.length
    ? execution.steps.map((step) => ({ label: step.name, status: step.status }))
    : progress || [];
  if (!steps.length && !pending) return null;

  return (
    <div className="execution-progress" aria-live={pending ? "polite" : undefined}>
      <div className="execution-progress-heading">
        <span className="execution-progress-label">执行进度</span>
        <span>{execution ? `${statusLabel(execution.status)} · ${steps.length} 步` : "实时更新"}</span>
      </div>
      <ol>
        {steps.map((step, index) => (
          <li key={`${step.label}-${index}`} className={`progress-${step.status}`}>
            <span className="progress-mark" aria-hidden="true">
              {step.status === "in_progress" ? <LoaderCircle className="spin" size={11} /> : <Check size={11} />}
            </span>
            <span>{step.label}</span>
            <span>{progressStatusLabel(step.status)}</span>
          </li>
        ))}
        {!steps.length && pending && <li className="progress-in_progress"><LoaderCircle className="spin" size={11} /><span>正在准备本轮回答</span></li>}
      </ol>
    </div>
  );
}

function MarkdownContent({ content }) {
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      components={{
        a: ({ node, ...props }) => <a {...props} target="_blank" rel="noreferrer" />,
        img: ({ node, ...props }) => <img {...props} loading="lazy" decoding="async" />,
      }}
    >
      {content}
    </ReactMarkdown>
  );
}

function Message({ message }) {
  const assistant = message.role === "assistant";
  const label = assistant ? "Steam Agent" : "你";
  return (
    <article className={`message-row ${assistant ? "assistant" : "user"}`}>
      <div className="message-avatar" aria-hidden="true">
        {assistant ? <Bot size={17} /> : <UserRound size={16} />}
      </div>
      <div className="message-body">
        <div className="message-meta"><strong>{label}</strong><time>{formatClock(message.time)}</time></div>
        {assistant && <ExecutionProgress execution={message.execution} progress={message.progress} pending={message.pending} />}
        <div className="message-content" aria-label={`${label}的消息`}>
          {message.content
            ? assistant ? <MarkdownContent content={message.content} /> : message.content
            : (message.pending ? <span className="message-pending"><LoaderCircle className="spin" size={15} /> 正在整理回答</span> : "")}
        </div>
        {message.degraded && <p className="message-warning"><ShieldAlert size={15} aria-hidden="true" /> 回答可能没有完整完成，请调整条件后重试。</p>}
      </div>
    </article>
  );
}

function MessageSkeleton() {
  return (
    <div className="message-skeletons" aria-label="正在载入历史消息" aria-busy="true">
      <div className="skeleton-line short" />
      <div className="skeleton-line" />
      <div className="skeleton-line medium" />
      <div className="skeleton-line user-skeleton" />
    </div>
  );
}

const starterPrompts = [
  "适合周末通关的合作游戏",
  "像 Hades 一样爽快，但节奏更短",
  "朋友刚入坑 Steam，送什么游戏好？",
];

export default function ChatWorkspace({
  title,
  user,
  messages,
  loading,
  error,
  onRetry,
  streaming,
  streamStatus,
  input,
  onInput,
  onSend,
  onStop,
  onPrompt,
  onOpenAuth,
  onTheme,
}) {
  const scrollerRef = useRef(null);

  useEffect(() => {
    const scroller = scrollerRef.current;
    if (scroller) scroller.scrollTop = scroller.scrollHeight;
  }, [messages, loading]);

  function handleKeyDown(event) {
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      event.currentTarget.form?.requestSubmit();
    }
  }

  return (
    <main className="workspace" aria-label="聊天工作区">
      <header className="workspace-header">
        <div className="workspace-title">
          <span className="eyebrow"><span className="live-dot" />工作区</span>
          <h1>{title || "新会话"}</h1>
        </div>
        <div className="workspace-actions">
          <div className="header-status" aria-live="polite">
            {streaming ? <><LoaderCircle className="spin" size={14} /> {streamStatus || "正在处理"}</> : <><span className="status-dot" /> 就绪</>}
          </div>
          {user && <button className="theme-toggle" type="button" onClick={() => onTheme(user.theme === "dark" ? "light" : "dark")} aria-label={user.theme === "dark" ? "切换到浅色主题" : "切换到深色主题"} title={user.theme === "dark" ? "切换到浅色主题" : "切换到深色主题"}>{user.theme === "dark" ? <Sun size={16} /> : <Moon size={16} />}</button>}
          {!user && <button className="login-pill" type="button" onClick={onOpenAuth}><UserRound size={14} /> 登录后开始对话</button>}
          {user && <div className="signed-in"><span className="account-avatar">{user.username.slice(0, 1).toUpperCase()}</span><span>{user.username}</span></div>}
        </div>
      </header>

      <section ref={scrollerRef} className="message-scroller" aria-live="polite" aria-busy={loading}>
        {loading && <MessageSkeleton />}
        {!loading && error && (
          <div className="inline-error" role="alert">
            <CircleAlert size={20} aria-hidden="true" />
            <div><strong>历史消息加载失败</strong><p>{error}</p></div>
            <button className="secondary-action compact" type="button" onClick={onRetry}><RefreshCw size={14} /> 重试</button>
          </div>
        )}
        {!loading && !error && messages.length === 0 && (
          <div className="empty-chat">
            <div className="empty-identity"><div className="empty-mark"><GamepadGlyph /></div><span>Steam Agent</span></div>
            <h2>把想玩的，聊清楚。</h2>
            <p>告诉我你想找的玩法、朋友的偏好或今晚的时间，我会帮你从 Steam 里筛出值得玩的选择。</p>
            <div className="empty-prompts" aria-label="问题示例">
              {starterPrompts.map((prompt) => <button type="button" key={prompt} onClick={() => onPrompt(prompt)}>{prompt}<Send size={13} aria-hidden="true" /></button>)}
            </div>
          </div>
        )}
        {!loading && !error && messages.map((message) => <Message key={message.id} message={message} />)}
      </section>

      <form className="composer" onSubmit={onSend}>
        <div className="composer-shell">
          <label className="sr-only" htmlFor="chat-input">输入消息</label>
          <textarea
            id="chat-input"
            value={input}
            onChange={(event) => onInput(event.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={user ? "描述你想找的游戏、玩法或预算..." : "登录后开始你的第一段对话..."}
            rows="1"
            maxLength="6000"
            disabled={streaming}
          />
          <div className="composer-footer">
            <span><Clock3 size={13} /> Enter 发送 · Shift + Enter 换行</span>
            <span>{input.length}/6000</span>
          </div>
        </div>
        {streaming ? (
          <button className="send-button stop-button" type="button" onClick={onStop} aria-label="停止生成" title="停止生成"><Square size={16} fill="currentColor" /></button>
        ) : (
          <button className="send-button" type="submit" disabled={!input.trim()} aria-label="发送消息" title="发送消息"><Send size={17} /></button>
        )}
      </form>
    </main>
  );
}

function GamepadGlyph() {
  return <Sparkles size={20} aria-hidden="true" />;
}
