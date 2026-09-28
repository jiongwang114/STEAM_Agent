import { Check, CircleUserRound, Link2, LogOut, Moon, Save, ShieldCheck, Sun, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";

export default function SettingsPanel({ user, onClose, onTheme, onBind, onLogout, onRevoke, busy, error }) {
  const [steamId, setSteamId] = useState(user.bound_steam_id || "");
  const [theme, setTheme] = useState(user.theme || "light");
  const closeRef = useRef(null);

  useEffect(() => {
    closeRef.current?.focus();
    function handleEscape(event) {
      if (event.key === "Escape") onClose();
    }
    document.addEventListener("keydown", handleEscape);
    return () => document.removeEventListener("keydown", handleEscape);
  }, [onClose]);

  async function saveTheme(nextTheme) {
    const previousTheme = theme;
    setTheme(nextTheme);
    const saved = await onTheme(nextTheme);
    if (!saved) setTheme(previousTheme);
  }

  function bind(event) {
    event.preventDefault();
    onBind(steamId.trim());
  }

  return (
    <div className="drawer-backdrop" role="presentation" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <aside className="settings-panel" role="dialog" aria-modal="true" aria-labelledby="settings-title" aria-describedby="settings-description">
        <header className="panel-header">
          <div><span className="auth-kicker">个人工作台</span><h2 id="settings-title">设置</h2></div>
          <button ref={closeRef} className="icon-button" type="button" onClick={onClose} aria-label="关闭设置" title="关闭设置"><X size={18} /></button>
        </header>
        <p id="settings-description" className="sr-only">账户主题、Steam ID 和登录状态设置</p>

        <div className="account-strip">
          <div className="account-avatar large" aria-hidden="true">{user.username.slice(0, 1).toUpperCase()}</div>
          <div><strong>{user.username}</strong><span>加入于 {user.created_at || "最近"}</span></div>
          <CircleUserRound className="account-badge" size={18} aria-hidden="true" />
        </div>

        <section className="settings-section">
          <div className="section-label"><Sun size={16} aria-hidden="true" /> 显示偏好</div>
          <p className="section-help">选择工作台的明暗主题，偏好会保存到账号。</p>
          <div className="theme-switcher" role="group" aria-label="选择主题">
            <button type="button" className={theme === "light" ? "selected" : ""} aria-pressed={theme === "light"} onClick={() => saveTheme("light")}><Sun size={16} aria-hidden="true" /><span>浅色</span>{theme === "light" && <Check size={14} />}</button>
            <button type="button" className={theme === "dark" ? "selected" : ""} aria-pressed={theme === "dark"} onClick={() => saveTheme("dark")}><Moon size={16} aria-hidden="true" /><span>深色</span>{theme === "dark" && <Check size={14} />}</button>
          </div>
        </section>

        <section className="settings-section steam-section">
          <div className="section-label"><span className="steam-symbol"><Link2 size={14} /></span> Steam 游戏库</div>
          <p className="section-help">绑定后，Agent 可以参考你的游戏库和游玩记录。</p>
          <form className="steam-form" onSubmit={bind}>
            <label className="sr-only" htmlFor="steam-id">Steam ID</label>
            <input id="steam-id" value={steamId} onChange={(event) => setSteamId(event.target.value.replace(/\D/g, "").slice(0, 17))} placeholder="17 位数字 Steam ID" inputMode="numeric" maxLength="17" aria-describedby="steam-id-help" />
            <button className="primary-action compact" type="submit" disabled={busy || !/^\d{17}$/.test(steamId)}><Save size={15} aria-hidden="true" /> <span>{busy ? "保存中" : "保存"}</span></button>
          </form>
          <p id="steam-id-help" className="field-help">Steam ID 必须是 17 位数字。</p>
          {user.bound_steam_id && <p className="bound-state"><ShieldCheck size={14} aria-hidden="true" /> 已连接 · {user.bound_steam_id}</p>}
        </section>
        {error && <p className="form-error panel-error" role="alert">{error}</p>}

        <div className="panel-actions">
          <button className="secondary-action" type="button" onClick={onLogout}><LogOut size={16} aria-hidden="true" /> <span>退出登录</span></button>
          <button className="text-button danger-text" type="button" onClick={onRevoke}>退出所有设备</button>
        </div>
      </aside>
    </div>
  );
}
