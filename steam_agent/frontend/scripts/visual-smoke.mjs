import assert from "node:assert/strict";
import { access, mkdtemp, mkdir } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";
import { createServer } from "vite";

const frontendRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const configuredBrowser = process.env.PLAYWRIGHT_EXECUTABLE_PATH;
const screenshotDir = process.env.VISUAL_OUTPUT_DIR
  ? resolve(process.env.VISUAL_OUTPUT_DIR)
  : await mkdtemp(join(tmpdir(), "steam-agent-visual-"));

async function fileExists(path) {
  if (!path) return false;
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
  const windows = [
    programFilesX86 && join(programFilesX86, "Microsoft", "Edge", "Application", "msedge.exe"),
    programFiles && join(programFiles, "Microsoft", "Edge", "Application", "msedge.exe"),
    programFiles && join(programFiles, "Google", "Chrome", "Application", "chrome.exe"),
    localAppData && join(localAppData, "Microsoft", "Edge", "Application", "msedge.exe"),
    localAppData && join(localAppData, "Google", "Chrome", "Application", "chrome.exe"),
  ];
  const mac = [
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  ];
  const linux = ["/usr/bin/chromium", "/usr/bin/chromium-browser", "/usr/bin/google-chrome", "/usr/bin/microsoft-edge"];
  return [configuredBrowser, ...(process.platform === "win32" ? windows : process.platform === "darwin" ? mac : linux)].filter(Boolean);
}

await mkdir(screenshotDir, { recursive: true });

let server;
let browser;
try {
  const executablePath = await browserCandidates().reduce(async (found, candidate) => {
    const prior = await found;
    return prior || (await fileExists(candidate) ? candidate : null);
  }, Promise.resolve(null));

  if (configuredBrowser && !(await fileExists(configuredBrowser))) {
    throw new Error(`PLAYWRIGHT_EXECUTABLE_PATH does not exist: ${configuredBrowser}`);
  }

  server = await createServer({
    configFile: resolve(frontendRoot, "vite.config.js"),
    root: frontendRoot,
    server: { host: "127.0.0.1", port: 0, strictPort: true },
  });
  await server.listen();
  const address = server.httpServer.address();
  const appUrl = `http://127.0.0.1:${address.port}/`;

  browser = await chromium.launch({ headless: true, ...(executablePath ? { executablePath } : {}) });
  const pageErrors = [];

  async function openLoggedOutPage(viewport) {
    const page = await browser.newPage({ viewport });
    page.on("pageerror", (error) => pageErrors.push(error.message));
    await page.route("**/auth/user-info", (route) => route.fulfill({
      status: 401,
      contentType: "application/json",
      body: JSON.stringify({ error: { code: "unauthorized", message: "Not signed in" } }),
    }));
    await page.goto(appUrl, { waitUntil: "networkidle" });
    await page.locator(".empty-chat h2").waitFor();
    assert.equal(await page.title(), "Steam Agent");
    return page;
  }

  const desktop = await openLoggedOutPage({ width: 1440, height: 1000 });
  assert.equal(await desktop.getByRole("main", { name: "聊天工作区" }).isVisible(), true);
  assert.equal(await desktop.getByRole("complementary", { name: "会话列表" }).isVisible(), true);
  assert.equal(await desktop.locator(".login-pill").isVisible(), true);
  const desktopWidth = await desktop.evaluate(() => ({ viewport: innerWidth, page: document.documentElement.scrollWidth }));
  assert.ok(desktopWidth.page <= desktopWidth.viewport, `Desktop horizontal overflow: ${JSON.stringify(desktopWidth)}`);
  await desktop.screenshot({ path: join(screenshotDir, "desktop.png"), fullPage: true, animations: "disabled" });

  const mobile = await openLoggedOutPage({ width: 390, height: 844 });
  assert.equal(await mobile.locator(".mobile-topbar").isVisible(), true);
  const sidebar = mobile.locator(".sidebar");
  const closedBounds = await sidebar.boundingBox();
  assert.ok(closedBounds.x + closedBounds.width <= 1, `Mobile sidebar should start closed: ${JSON.stringify(closedBounds)}`);
  const mobileWidth = await mobile.evaluate(() => ({ viewport: innerWidth, page: document.documentElement.scrollWidth }));
  assert.ok(mobileWidth.page <= mobileWidth.viewport, `Mobile horizontal overflow: ${JSON.stringify(mobileWidth)}`);
  await mobile.screenshot({ path: join(screenshotDir, "mobile.png"), fullPage: true, animations: "disabled" });

  await mobile.getByRole("button", { name: "打开会话列表" }).click();
  assert.equal(await sidebar.evaluate((element) => element.classList.contains("is-open")), true);
  await mobile.screenshot({ path: join(screenshotDir, "mobile-sidebar-open.png"), fullPage: true, animations: "disabled" });
  const openBounds = await sidebar.boundingBox();
  assert.ok(openBounds.x >= 0 && openBounds.x + openBounds.width <= 390, `Mobile sidebar should fit viewport: ${JSON.stringify(openBounds)}`);

  await mobile.getByRole("button", { name: "关闭会话栏" }).click();
  await mobile.getByRole("button", { name: "登录后开始对话" }).click();
  const authDialog = mobile.getByRole("dialog", { name: "登录 Steam Agent" });
  const authBounds = await authDialog.boundingBox();
  assert.ok(authBounds.x >= 0 && authBounds.x + authBounds.width <= 390, `Mobile login dialog should fit viewport: ${JSON.stringify(authBounds)}`);
  await mobile.screenshot({ path: join(screenshotDir, "mobile-login.png"), fullPage: true, animations: "disabled" });

  const history = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  history.on("pageerror", (error) => pageErrors.push(error.message));
  await history.route("**/*", async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname === "/auth/user-info") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ user: { username: "history-tester", theme: "light" } }),
      });
      return;
    }
    if (url.pathname === "/threads") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ threads: [{ thread_id: "thread-history", title: "多轮历史测试" }] }),
      });
      return;
    }
    if (url.pathname === "/messages") {
      const messages = Array.from({ length: 30 }, (_, index) => ({
        turn: Math.floor(index / 2) + 1,
        role: index % 2 ? "assistant" : "user",
        content: `第 ${index + 1} 条消息：${"这是用于滚动回归测试的较长内容。".repeat(4)}`,
        time: "2026-09-27 12:00:00",
      }));
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ messages }) });
      return;
    }
    await route.continue();
  });
  await history.goto(appUrl, { waitUntil: "networkidle" });
  await history.locator(".message-row").last().waitFor();
  const historyLayout = await history.evaluate(() => {
    const shell = document.querySelector(".app-shell");
    const scroller = document.querySelector(".message-scroller");
    return {
      viewport: innerHeight,
      shellHeight: shell.clientHeight,
      pageHeight: document.documentElement.scrollHeight,
      messageHeight: scroller.clientHeight,
      messageContentHeight: scroller.scrollHeight,
    };
  });
  assert.equal(historyLayout.shellHeight, historyLayout.viewport, `Chat shell should stay within viewport: ${JSON.stringify(historyLayout)}`);
  assert.ok(historyLayout.pageHeight <= historyLayout.viewport, `History should scroll inside the chat pane: ${JSON.stringify(historyLayout)}`);
  assert.ok(historyLayout.messageContentHeight > historyLayout.messageHeight, `History should overflow the chat pane: ${JSON.stringify(historyLayout)}`);
  const historyScroller = history.locator(".message-scroller");
  const historyBounds = await historyScroller.boundingBox();
  const initialScroll = await historyScroller.evaluate((scroller) => scroller.scrollTop);
  await history.mouse.move(historyBounds.x + historyBounds.width / 2, historyBounds.y + historyBounds.height / 2);
  await history.mouse.wheel(0, -600);
  await history.waitForTimeout(50);
  const afterWheelUp = await historyScroller.evaluate((scroller) => scroller.scrollTop);
  await history.mouse.wheel(0, 600);
  await history.waitForTimeout(50);
  const scrollResult = await historyScroller.evaluate((scroller) => {
    const last = scroller.querySelector(".message-row:last-child").getBoundingClientRect();
    const viewport = scroller.getBoundingClientRect();
    return { scrollTop: scroller.scrollTop, lastBottom: last.bottom, viewportBottom: viewport.bottom };
  });
  assert.ok(afterWheelUp < initialScroll, `Mouse wheel up should scroll the history pane: ${JSON.stringify({ initialScroll, afterWheelUp })}`);
  assert.ok(scrollResult.scrollTop > afterWheelUp, `Mouse wheel down should scroll the history pane: ${JSON.stringify({ afterWheelUp, afterWheelDown: scrollResult.scrollTop })}`);
  assert.ok(scrollResult.lastBottom <= scrollResult.viewportBottom + 1, `Last message should be reachable: ${JSON.stringify(scrollResult)}`);
  await history.screenshot({ path: join(screenshotDir, "desktop-chat-history.png"), fullPage: true, animations: "disabled" });
  await history.close();

  assert.deepEqual(pageErrors, [], `Browser runtime errors: ${pageErrors.join("; ")}`);
  console.log(`Visual smoke checks passed using ${executablePath || "Playwright Chromium"}.`);
  console.log(`Screenshots: ${screenshotDir}`);
} catch (error) {
  if (!configuredBrowser && !browser) {
    error.message += "\nInstall a local browser with `npx playwright install chromium`, or set PLAYWRIGHT_EXECUTABLE_PATH.";
  }
  throw error;
} finally {
  await browser?.close();
  await server?.close();
}
