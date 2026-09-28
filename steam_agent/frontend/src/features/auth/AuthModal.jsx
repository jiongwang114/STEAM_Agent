import { ArrowRight, Gamepad2, KeyRound, LockKeyhole, UserRound, UserPlus, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";

export default function AuthModal({ mode, onMode, onSubmit, onClose, error, busy }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const usernameRef = useRef(null);
  const busyRef = useRef(busy);
  const register = mode === "register";
  busyRef.current = busy;

  useEffect(() => {
    const previousFocus = document.activeElement;
    usernameRef.current?.focus();
    function handleEscape(event) {
      if (event.key === "Escape" && !busyRef.current) onClose();
    }
    document.addEventListener("keydown", handleEscape);
    return () => {
      document.removeEventListener("keydown", handleEscape);
      previousFocus?.focus?.();
    };
  }, [onClose]);

  function submit(event) {
    event.preventDefault();
    onSubmit({ username: username.trim(), password });
  }

  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={(event) => event.target === event.currentTarget && !busy && onClose()}>
      <section className="auth-modal" role="dialog" aria-modal="true" aria-labelledby="auth-title" aria-describedby="auth-description">
        <button className="icon-button modal-close" type="button" onClick={onClose} disabled={busy} aria-label="关闭登录窗口" title="关闭登录窗口"><X size={18} /></button>
        <div className="auth-layout">
          <div className="auth-aside">
            <div className="auth-brand"><span className="brand-mark"><Gamepad2 size={18} /></span><strong>Steam Agent</strong></div>
            <div className="auth-aside-copy">
              <span className="auth-kicker">私有游戏工作台</span>
              <strong>把想玩的，聊清楚。</strong>
              <span>会话、偏好和 Steam 绑定都留在你的工作台里。</span>
            </div>
            <div className="auth-aside-stamp"><KeyRound size={14} /> Session protected</div>
          </div>
          <div className="auth-content">
            <div className="auth-heading"><span className="auth-icon"><KeyRound size={18} aria-hidden="true" /></span><span className="auth-kicker">{register ? "建立个人空间" : "继续探索"}</span></div>
            <h2 id="auth-title">{register ? "建立你的推荐空间" : "登录 Steam Agent"}</h2>
            <p id="auth-description">{register ? "保存会话、偏好和 Steam 绑定。" : "登录后继续你的游戏发现对话。"}</p>
            <div className="auth-tabs" role="group" aria-label="认证方式">
              <button type="button" aria-pressed={!register} className={!register ? "selected" : ""} onClick={() => onMode("login")} disabled={busy}>登录</button>
              <button type="button" aria-pressed={register} aria-label={register ? "已有账号？登录" : "还没有账号？创建账号"} className={register ? "selected" : ""} onClick={() => onMode("register")} disabled={busy}>创建账号</button>
            </div>
            <form onSubmit={submit} className="auth-form">
              <label htmlFor="auth-username"><UserRound size={15} aria-hidden="true" /> 用户名</label>
              <input ref={usernameRef} id="auth-username" value={username} onChange={(event) => setUsername(event.target.value)} minLength="2" maxLength="64" autoComplete="username" disabled={busy} required placeholder="输入用户名" />
              <label htmlFor="auth-password"><LockKeyhole size={15} aria-hidden="true" /> 密码</label>
              <input id="auth-password" type="password" value={password} onChange={(event) => setPassword(event.target.value)} minLength="8" maxLength="256" autoComplete={register ? "new-password" : "current-password"} disabled={busy} required placeholder={register ? "至少 8 位字符" : "输入密码"} />
              {error && <p className="form-error" role="alert">{error}</p>}
              <button className="primary-action" type="submit" disabled={busy}>
                <span>{busy ? "正在处理" : register ? "创建账号" : "登录"}</span>
                {register ? <UserPlus size={16} aria-hidden="true" /> : <ArrowRight size={16} aria-hidden="true" />}
              </button>
            </form>
            <p className="auth-footnote">你的登录状态由服务端 Session Cookie 保护。</p>
          </div>
        </div>
      </section>
    </div>
  );
}
