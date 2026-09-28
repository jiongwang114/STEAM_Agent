import { Clock3, Edit3, Gamepad2, Inbox, LoaderCircle, LogIn, Plus, RefreshCw, Settings2, Trash2, X } from "lucide-react";

function asDate(value) {
  if (!value) return null;
  const date = new Date(String(value).replace(" ", "T"));
  return Number.isNaN(date.getTime()) ? null : date;
}

function formatTime(value) {
  const date = asDate(value);
  if (!date) return "";
  return date.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
}

function groupName(value) {
  const date = asDate(value);
  if (!date) return "最近";
  const now = new Date();
  const startToday = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const day = new Date(date.getFullYear(), date.getMonth(), date.getDate());
  const days = Math.round((startToday - day) / 86400000);
  if (days <= 0) return "今天";
  if (days === 1) return "昨天";
  if (days < 7) return "本周";
  return "更早";
}

function groupThreads(threads) {
  const groups = new Map();
  threads.forEach((thread) => {
    const key = groupName(thread.last_active);
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(thread);
  });
  return [...groups.entries()];
}

export default function ThreadSidebar({
  open,
  user,
  threads,
  loading,
  error,
  currentId,
  onRetry,
  onSelect,
  onNew,
  onRename,
  onDelete,
  onSettings,
  onClose,
}) {
  const groups = groupThreads(threads);

  return (
    <aside className={`sidebar ${open ? "is-open" : ""}`} aria-label="会话列表">
      <header className="sidebar-header">
        <div className="brand-mark" aria-hidden="true"><Gamepad2 size={18} strokeWidth={2.4} /></div>
        <div className="brand-copy"><strong>Steam Agent</strong><span>游戏发现工作台</span></div>
        <button className="icon-button mobile-close" type="button" onClick={onClose} aria-label="关闭会话栏" title="关闭会话栏"><X size={18} /></button>
      </header>

      <button className="new-thread" type="button" onClick={onNew}>
        <Plus size={16} aria-hidden="true" />
        <span>新建会话</span>
        <kbd>N</kbd>
      </button>

      <div className="thread-heading"><span>最近会话</span><Clock3 size={14} aria-hidden="true" /></div>
      {error && (
        <div className="sidebar-error" role="alert">
          <span>{error}</span>
          <button className="icon-button small" type="button" onClick={onRetry} aria-label="重新加载会话" title="重新加载会话"><RefreshCw size={14} /></button>
        </div>
      )}

      <nav className="thread-list" aria-label="最近会话">
        {loading && <div className="thread-loading" aria-label="正在载入会话"><LoaderCircle className="spin" size={16} /><span>正在同步会话</span></div>}
        {!loading && groups.map(([label, items]) => (
          <section className="thread-group" key={label}>
            <h2>{label}</h2>
            {items.map((thread) => {
              const active = thread.thread_id === currentId;
              return (
                <div className={`thread-item ${active ? "active" : ""}`} key={thread.thread_id}>
                  <button type="button" onClick={() => onSelect(thread.thread_id)} aria-current={active ? "page" : undefined}>
                    <span className="thread-icon" aria-hidden="true">
                      {thread.is_running
                        ? <LoaderCircle className="spin" size={14} />
                        : <Inbox size={14} />}
                    </span>
                    <span className="thread-copy">
                      <strong title={thread.title || "新会话"}>{thread.title || "新会话"}</strong>
                      <small>{thread.is_running ? "正在生成回答" : formatTime(thread.last_active) || `${thread.msg_count || 0} 条消息`}</small>
                    </span>
                  </button>
                  {active && <div className="thread-actions">
                    <button className="icon-button small" type="button" onClick={() => onRename(thread)} aria-label="重命名会话" title="重命名会话"><Edit3 size={13} /></button>
                    <button className="icon-button small danger" type="button" onClick={() => onDelete(thread)} aria-label="删除会话" title="删除会话"><Trash2 size={13} /></button>
                  </div>}
                </div>
              );
            })}
          </section>
        ))}
        {!loading && !groups.length && <div className="sidebar-empty"><Inbox size={18} aria-hidden="true" /><span>还没有历史会话</span></div>}
      </nav>

      <footer className="sidebar-footer">
        <button className="account-button" type="button" onClick={onSettings}>
          <span className={`account-avatar ${user ? "" : "guest"}`} aria-hidden="true">{user ? user.username.slice(0, 1).toUpperCase() : <LogIn size={15} />}</span>
          <span className="account-copy"><strong>{user?.username || "未登录"}</strong><small>{user ? (user.bound_steam_id ? "Steam 已绑定" : "连接 Steam ID") : "登录以保存会话"}</small></span>
          <Settings2 size={16} aria-hidden="true" />
        </button>
        <button className="settings-link" type="button" onClick={onSettings}><Settings2 size={15} aria-hidden="true" /><span>设置</span></button>
        <div className="sidebar-footnote"><span className="status-dot" />仅为你保存</div>
      </footer>
    </aside>
  );
}
