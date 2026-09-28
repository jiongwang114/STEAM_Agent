# Steam Agent Frontend

独立的 React/Vite 聊天工作台，按 `../docs/API_CONTRACT.md` 接入认证、会话、消息历史、执行摘要、SSE 聊天、Steam 绑定和主题设置。

```powershell
npm install
npm run dev
```

开发服务器默认将业务接口代理到 `http://localhost:8000`。部署到其他地址时设置 `VITE_API_BASE`，并由反向代理提供同源 Cookie 策略。

## 本地视觉验收

```powershell
npm run test:visual
```

该命令会启动临时 Vite 服务，模拟未登录接口，并使用本机已安装的 Edge/Chrome 运行 Playwright 截图级冒烟检查。截图输出到临时目录；也可通过 `PLAYWRIGHT_EXECUTABLE_PATH` 指定浏览器，或通过 `VISUAL_OUTPUT_DIR` 指定截图目录。它不依赖 Codex 浏览器令牌，也不会提交登录或聊天请求。

## 真实后端端到端验收

先按 `../docs/BACKEND_DEPLOYMENT.md` 启动后端，再启动 Vite 开发服务器：

```powershell
npm run test:e2e:real
```

该命令使用临时账号，通过真实 Session Cookie 完成注册、用户信息、Steam ID 绑定、主题保存、SSE 聊天、会话删除和退出登录；删除返回 `202` 时会继续调用公开清理重试接口，直到清理完成或测试失败。测试账号名和 Steam ID 使用时间戳生成，完成后应手动清理开发数据库中的一次性账号。
