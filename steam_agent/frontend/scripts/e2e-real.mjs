import assert from "node:assert/strict";
import { access, mkdir } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { chromium } from "playwright";

const appUrl = process.env.E2E_APP_URL || "http://127.0.0.1:4173/";
const screenshotDir = resolve(process.env.VISUAL_OUTPUT_DIR || join(tmpdir(), "steam-agent-e2e"));
const configuredBrowser = process.env.PLAYWRIGHT_EXECUTABLE_PATH;

async function exists(path) {
  try {
    await access(path);
    return true;
  } catch {
    return false;
  }
}

function browserCandidates() {
  const programFilesX86 = process.env["PROGRAMFILES(X86)"];
  const programFiles = process.env.ProgramFiles;
  const localAppData = process.env.LOCALAPPDATA;
  return [
    configuredBrowser,
    programFilesX86 && join(programFilesX86, "Microsoft", "Edge", "Application", "msedge.exe"),
    programFiles && join(programFiles, "Microsoft", "Edge", "Application", "msedge.exe"),
    programFiles && join(programFiles, "Google", "Chrome", "Application", "chrome.exe"),
    localAppData && join(localAppData, "Microsoft", "Edge", "Application", "msedge.exe"),
    localAppData && join(localAppData, "Google", "Chrome", "Application", "chrome.exe"),
  ].filter(Boolean);
}

const executablePath = await browserCandidates().reduce(async (found, candidate) => {
  const prior = await found;
  return prior || (await exists(candidate) ? candidate : null);
}, Promise.resolve(null));

if (!executablePath) throw new Error("找不到本机 Edge/Chrome，请设置 PLAYWRIGHT_EXECUTABLE_PATH");
await mkdir(screenshotDir, { recursive: true });

const stamp = Date.now();
const username = `e2e_${stamp}`;
const password = `SteamE2e_${stamp}!`;
const steamId = `7656119${String(stamp).slice(-10)}`;
const browser = await chromium.launch({ headless: true, executablePath });
const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
const page = await context.newPage();
const pageErrors = [];
page.on("pageerror", (error) => pageErrors.push(error.message));

try {
  await page.goto(appUrl, { waitUntil: "domcontentloaded" });
  await page.getByRole("button", { name: "登录后开始对话" }).waitFor({ timeout: 30000 });
  await page.getByRole("button", { name: "登录后开始对话" }).click();

  let auth = page.getByRole("dialog", { name: "登录 Steam Agent" });
  await auth.getByRole("button", { name: /还没有账号/ }).click();
  auth = page.getByRole("dialog", { name: "建立你的推荐空间" });
  await auth.locator("#auth-username").fill(username);
  await auth.locator("#auth-password").fill(password);
  await auth.getByRole("button", { name: "创建账号", exact: true }).click();
  await auth.waitFor({ state: "detached" });

  await page.getByRole("button", { name: "设置", exact: true }).click();
  const settings = page.getByRole("dialog", { name: "设置" });
  await settings.getByText(username, { exact: true }).waitFor();
  await settings.locator("#steam-id").fill(steamId);
  await settings.getByRole("button", { name: "保存", exact: true }).click();
  await settings.getByText(new RegExp(steamId)).waitFor({ timeout: 30000 });
  await settings.getByRole("button", { name: "浅色", exact: true }).click();
  await page.waitForFunction(() => document.documentElement.dataset.theme === "light");
  await settings.getByRole("button", { name: "关闭设置" }).click();

  const input = page.locator("#chat-input");
  await input.fill("请只用一句话确认这是一条真实接口联调消息。");
  await input.press("Enter");
  const assistant = page.locator(".message-row.assistant .message-content").last();
  await assistant.waitFor({ timeout: 10000 });
  await page.waitForFunction(
    (selector) => {
      const text = document.querySelector(selector)?.textContent?.trim() || "";
      return Boolean(text) && !text.includes("正在整理回答");
    },
    ".message-row.assistant .message-content",
    { timeout: 90000 },
  );
  await page.getByRole("button", { name: "设置", exact: true }).waitFor();
  await page.locator(".thread-item").first().waitFor({ timeout: 30000 });
  await page.screenshot({ path: join(screenshotDir, "authenticated-chat.png"), fullPage: true });

  const deleteResponsePromise = page.waitForResponse((response) => (
    response.request().method() === "DELETE" && response.url().includes("/threads?")
  ));
  page.once("dialog", (dialog) => dialog.accept());
  await page.locator(".thread-item").first().getByRole("button", { name: "删除会话" }).click();
  const deleteResponse = await deleteResponsePromise;
  const deletePayload = await deleteResponse.json();
  assert.ok([200, 202].includes(deleteResponse.status()), `删除接口返回 ${deleteResponse.status()}`);
  await page.locator(".thread-item").waitFor({ state: "detached", timeout: 30000 });

  let cleanupRetry = null;
  if (deleteResponse.status() === 202) {
    cleanupRetry = await page.evaluate(async (threadId) => {
      let lastResult;
      for (let attempt = 0; attempt < 10; attempt += 1) {
        const response = await fetch(`/threads/${encodeURIComponent(threadId)}/cleanup/retry`, {
          method: "POST",
          credentials: "include",
        });
        lastResult = { attempt: attempt + 1, status: response.status, body: await response.json() };
        if (response.status === 200) return lastResult;
        await new Promise((resolve) => setTimeout(resolve, 1000));
      }
      return lastResult;
    }, deletePayload.thread_id || new URL(deleteResponse.url()).searchParams.get("thread_id"));
    assert.equal(cleanupRetry.status, 200, `清理重试仍未完成: ${JSON.stringify(cleanupRetry.body)}`);
  }

  await page.getByRole("button", { name: "设置", exact: true }).click();
  await page.getByRole("dialog", { name: "设置" }).getByRole("button", { name: "退出登录" }).click();
  await page.getByRole("dialog", { name: "登录 Steam Agent" }).waitFor();
  assert.deepEqual(pageErrors, [], `浏览器运行时错误: ${pageErrors.join("; ")}`);
  console.log(JSON.stringify({ status: "passed", username, steamId, deleteStatus: deleteResponse.status(), cleanupRetry, screenshot: join(screenshotDir, "authenticated-chat.png") }));
} finally {
  await browser.close();
}
