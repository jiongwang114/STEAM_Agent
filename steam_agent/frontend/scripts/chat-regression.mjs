import assert from "node:assert/strict";
import { createServer as createHttpServer } from "node:http";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";
import { createServer as createViteServer } from "vite";

const frontendRoot = fileURLToPath(new URL("..", import.meta.url));
const markdownReply = "## 推荐\n\n- [Far Far West](https://store.steampowered.com/app/3124540/)\n- 支持 **Markdown** 和 `行内代码`\n\n![Far Far West](https://example.com/far-far-west.png)";
const execution = {
  status: "success",
  duration_ms: 420,
  steps: [{ name: "查找相似游戏", status: "completed", duration_ms: 320, round: 1 }],
};

const wait = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));

function writeJson(response, payload, status = 200) {
  response.writeHead(status, {
    "Access-Control-Allow-Credentials": "true",
    "Access-Control-Allow-Origin": "http://127.0.0.1:4174",
    "Content-Type": "application/json",
  });
  response.end(JSON.stringify(payload));
}

async function handleApi(request, response) {
  response.setHeader("Access-Control-Allow-Credentials", "true");
  response.setHeader("Access-Control-Allow-Origin", "http://127.0.0.1:4174");
  response.setHeader("Access-Control-Allow-Headers", "Content-Type, Accept");
  if (request.method === "OPTIONS") {
    response.writeHead(204);
    response.end();
    return;
  }

  const path = new URL(request.url, "http://127.0.0.1").pathname;
  if (path === "/auth/user-info") {
    writeJson(response, { user: { username: "markdown-tester", theme: "light" } });
    return;
  }
  if (path === "/threads") {
    writeJson(response, { threads: [{ thread_id: "thread-regression", title: "Markdown 回归" }] });
    return;
  }
  if (path === "/messages") {
    writeJson(response, { messages: [] });
    return;
  }
  if (path === "/chat/stream") {
    response.writeHead(200, {
      "Cache-Control": "no-cache",
      "Content-Type": "text/event-stream",
      Connection: "keep-alive",
    });
    const send = (event, data) => response.write(`data: ${JSON.stringify({ event, data })}\n\n`);
    send("status", "正在查找相似游戏");
    await wait(350);
    send("token", "## 推荐\n\n- [Far Far West](https://store.steampowered.com/app/3124540/)\n");
    await wait(100);
    send("token", "- 支持 **Markdown** 和 `行内代码`\n\n![Far Far West](https://example.com/far-far-west.png)");
    send("done", {
      status: "success",
      thread_id: "thread-regression",
      reply: markdownReply,
      tool_calls_made: ["store_search"],
      tool_rounds: 1,
      token_usage: {},
      execution,
      run_metadata: {},
    });
    response.end();
    return;
  }
  writeJson(response, { error: { message: "Not found" } }, 404);
}

const apiServer = createHttpServer((request, response) => void handleApi(request, response));
await new Promise((resolve) => apiServer.listen(0, "127.0.0.1", resolve));
const apiPort = apiServer.address().port;
process.env.VITE_API_BASE = `http://127.0.0.1:${apiPort}`;

const vite = await createViteServer({
  configFile: fileURLToPath(new URL("../vite.config.js", import.meta.url)),
  root: frontendRoot,
  server: { host: "127.0.0.1", port: 4174, strictPort: true },
});
await vite.listen();

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
const pageErrors = [];
page.on("pageerror", (error) => pageErrors.push(error.message));

try {
  await page.goto("http://127.0.0.1:4174/", { waitUntil: "networkidle" });
  await page.locator("#chat-input").fill("推荐一款合作游戏");
  await page.locator("#chat-input").press("Enter");

  const assistant = page.locator(".message-row.assistant").last();
  await assistant.locator(".execution-progress").waitFor();
  await page.waitForFunction(
    () => document.querySelector(".execution-progress")?.textContent.includes("正在查找相似游戏"),
    null,
    { timeout: 10000 },
  );
  assert.match(await assistant.locator(".execution-progress").innerText(), /正在查找相似游戏/);
  assert.equal(await assistant.locator(".message-pending").isVisible(), true);

  await assistant.locator(".message-content h2").waitFor({ timeout: 10000 });
  assert.equal(await assistant.locator(".message-content h2").innerText(), "推荐");
  assert.equal(await assistant.locator(".message-content a").getAttribute("href"), "https://store.steampowered.com/app/3124540/");
  assert.equal(await assistant.locator(".message-content img").getAttribute("src"), "https://example.com/far-far-west.png");
  assert.equal(await assistant.locator(".message-content strong").innerText(), "Markdown");
  assert.equal(await assistant.locator(".execution-details").count(), 0);

  const order = await assistant.evaluate((element) => [...element.querySelector(".message-body").children].map((child) => child.className));
  assert.ok(order.indexOf("execution-progress") < order.indexOf("message-content"), `进度必须在正文前: ${order.join(", ")}`);
  assert.match(await assistant.locator(".execution-progress").innerText(), /完成/);
  const layout = await page.evaluate(() => ({ viewport: innerWidth, document: document.documentElement.scrollWidth }));
  assert.ok(layout.document <= layout.viewport, `页面出现横向溢出: ${JSON.stringify(layout)}`);
  assert.deepEqual(pageErrors, [], `浏览器运行时错误: ${pageErrors.join("; ")}`);
  console.log("Chat Markdown and live progress regression checks passed.");
} finally {
  await browser.close();
  await vite.close();
  await new Promise((resolve) => apiServer.close(resolve));
}
