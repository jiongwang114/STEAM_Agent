const MAX_MESSAGE_LENGTH = 666;
const $ = (selector) => document.querySelector(selector);
const state = { user: null, sessions: [], threadId: null, messages: [], pending: null, stream: null, busy: false, authExpired: false };
let steamIdValue = '';
function setSteamProfile(steamId) { const link = $('#steamProfile'); if (!link) return; const valid = /^\d{17}$/.test(steamId); link.hidden = !valid; if (valid) link.href = `https://steamcommunity.com/profiles/${steamId}`; }
// When the standalone frontend is opened from disk, route API calls to the
// local FastAPI server instead of attempting fetches against the file origin.
const apiBase = new URLSearchParams(location.search).get('api')
  || (location.protocol === 'file:' || location.port === '4174' ? 'http://localhost:8000' : '');

class ApiError extends Error {
  constructor(status, payload) {
    const detail = payload?.error || payload?.detail || {};
    super(typeof detail === 'string' ? detail : detail.message || (status === 401 ? '登录状态已失效' : '请求失败，请稍后重试'));
    this.status = status;
    this.code = detail.code || 'request_failed';
  }
}

async function request(path, options = {}) {
  const response = await fetch(apiBase + path, { credentials: 'include', ...options, headers: { Accept: 'application/json', ...(options.body ? { 'Content-Type': 'application/json' } : {}), ...options.headers } });
  const data = response.status === 204 ? {} : await response.json().catch(() => ({}));
  if (!response.ok) throw new ApiError(response.status, data);
  return data;
}

async function readStream(path, options, onEvent) {
  const response = await fetch(apiBase + path, { credentials: 'include', ...options, headers: { Accept: 'text/event-stream', ...(options.body ? { 'Content-Type': 'application/json' } : {}) } });
  if (response.status === 204) return false;
  if (!response.ok) throw new ApiError(response.status, await response.json().catch(() => ({})));
  if (!response.body) throw new Error('当前浏览器无法读取实时回答');
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  while (true) {
    const { value, done } = await reader.read();
    buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
    const parts = buffer.split(/\r?\n\r?\n/);
    buffer = parts.pop() || '';
    for (const part of parts) {
      const data = part.split(/\r?\n/).filter((line) => line.startsWith('data:')).map((line) => line.slice(5).trimStart()).join('\n');
      if (data) onEvent(JSON.parse(data));
    }
    if (done) break;
  }
  if (buffer.trim()) {
    const data = buffer.split(/\r?\n/).filter((line) => line.startsWith('data:')).map((line) => line.slice(5).trimStart()).join('\n');
    if (data) onEvent(JSON.parse(data));
  }
  return true;
}

const api = {
  getCurrentUser: () => request('/auth/user-info'),
  getSessions: () => request('/threads'),
  getMessages: (id) => request('/messages?thread_id=' + encodeURIComponent(id)),
  createSession: () => crypto.randomUUID(),
  renameSession: (id, title) => request('/thread-title', { method: 'POST', body: JSON.stringify({ thread_id: id, title }) }),
  deleteSession: (id) => request('/threads?thread_id=' + encodeURIComponent(id), { method: 'DELETE' }),
  sendMessage: (id, message, onEvent, signal) => readStream('/chat/stream', { method: 'POST', body: JSON.stringify({ thread_id: id, message }), signal }, onEvent),
  retryMessage: (id, message, onEvent, signal) => api.sendMessage(id, message, onEvent, signal),
  attachRun: (id, onEvent, signal) => readStream('/chat/runs/' + encodeURIComponent(id), { signal }, onEvent),
  getSteamBinding: () => request('/steam-id'),
  rebindSteam: (steamId) => request('/bind-steam', { method: 'POST', body: JSON.stringify({ steam_id: steamId }) }),
  login: (username, password, register = false) => request('/auth/' + (register ? 'register' : 'login'), { method: 'POST', body: JSON.stringify({ username, password }) }),
  logout: () => request('/auth/logout', { method: 'POST' })
  ,setTheme: (theme) => request('/auth/theme', { method: 'POST', body: JSON.stringify({ theme }) })
};
window.museumApi = api;

let toastTimer;
function toast(message) { $('#toast').textContent = message; $('#toast').classList.add('show'); clearTimeout(toastTimer); toastTimer = setTimeout(() => $('#toast').classList.remove('show'), 3000); }
function timeText(value) { if (!value) return '刚刚'; const date = new Date(value.replace(' ', 'T')); return Number.isNaN(date.getTime()) ? value : new Intl.DateTimeFormat('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }).format(date); }
function node(tag, className, text) { const el = document.createElement(tag); if (className) el.className = className; if (text !== undefined) el.textContent = text; return el; }
function button(text, className, action) { const el = node('button', className, text); el.type = 'button'; el.addEventListener('click', action); return el; }
function showFailure(error, retry) {
  if (error?.status === 401) { expireLogin(); return; }
  const box = node('div', 'state-note');
  box.append(node('strong', '', error?.message || '这份档案暂时无法继续查阅。'));
  if (retry) box.append(button('重试', 'state-action', retry));
  if (error?.code?.includes('steam')) box.append(button('重新绑定 Steam', 'state-action', openSteam));
  $('#messages').append(box);
}
function expireLogin() { state.authExpired = true; $('#noticeTitle').textContent = '登录状态已失效'; $('#noticeText').textContent = '当前输入和已生成内容仍然保留。'; $('#loginExpired').hidden = false; }
function clearNotice() { state.authExpired = false; $('#loginExpired').hidden = true; }

function renderSessions() {
  const list = $('#sessionList'); list.replaceChildren(); $('#sessionCount').textContent = String(state.sessions.length).padStart(2, '0');
  if (!state.sessions.length) { list.append(node('p', 'empty-rail', '还没有夜班记录')); return; }
  for (const [rowIndex, session] of state.sessions.entries()) {
    const row = node('div', 'session-row');
    row.style.setProperty('--row-index', rowIndex);
    const open = button('', 'session' + (session.thread_id === state.threadId ? ' active' : ''), () => selectSession(session.thread_id));
    open.append(node('span', 'session-dot'));
    const copy = node('span', 'session-copy'); copy.append(node('b', '', session.title || '新夜班记录'), node('small', '', `${timeText(session.last_active)} · ${session.msg_count || 0} 条记录`)); open.append(copy);
    const menu = button('···', 'session-menu-btn', () => row.classList.toggle('menu-open')); menu.setAttribute('aria-label', `管理 ${session.title || '会话'}`);
    const actions = node('div', 'session-actions');
    actions.append(button('修改标题', '', () => renameSession(session)), button('删除会话', '', () => deleteSession(session)));
    row.append(open, menu, actions); list.append(row);
  }
}
async function refreshSessions() { const result = await api.getSessions(); state.sessions = result.threads || []; renderSessions(); }
async function renameSession(session) { const title = prompt('修改夜班记录标题', session.title || '新夜班记录')?.trim(); if (!title) return; if (title.length > 50) return toast('标题最多 50 字'); try { await api.renameSession(session.thread_id, title); await refreshSessions(); updateTitle(); toast('标题已更新'); } catch (error) { showFailure(error, () => renameSession(session)); } }
async function deleteSession(session) { if (!confirm(`删除“${session.title || '新夜班记录'}”？`)) return; try { const result = await api.deleteSession(session.thread_id); if (state.threadId === session.thread_id) newSession(); await refreshSessions(); toast(result.status === 'partial' ? '记录已移除，后台仍在完成清理' : '夜班记录已删除'); } catch (error) { showFailure(error, () => deleteSession(session)); } }
function updateTitle() { $('#currentTitle').textContent = state.sessions.find((s) => s.thread_id === state.threadId)?.title || '新夜班记录'; }
function newSession() { state.stream?.abort(); state.stream = null; state.threadId = api.createSession(); state.messages = []; state.pending = null; state.busy = false; clearNotice(); renderMessages(); updateTitle(); renderSessions(); $('#rail').classList.remove('open'); $('#prompt').focus({ preventScroll: true }); }
async function selectSession(id) {
  if (state.threadId === id) { $('#rail').classList.remove('open'); return; }
  state.stream?.abort(); state.stream = null; state.threadId = id; state.pending = null; state.messages = []; state.busy = false; clearNotice(); renderMessages(); updateTitle(); renderSessions(); $('#rail').classList.remove('open');
  try { const messages = (await api.getMessages(id)).messages || []; if (state.threadId !== id) return; state.messages = messages; renderMessages(); if (state.sessions.find((s) => s.thread_id === id)?.is_running) await attachRun(id); }
  catch (error) { if (state.threadId === id) showFailure(error, () => selectSession(id)); }
}

const recommendationPattern = /\*\*\[([^\]]+)\]\((https:\/\/(?:store\.steampowered\.com\/app\/\d+[^\s)]*))\)\*\*(?:[^\n]*)\n?(?:\[!\[[^\]]*\]\((https:\/\/[^\s)]+)\)\]\(https:\/\/store\.steampowered\.com\/app\/\d+[^\s)]*\)\s*)?/g;
function splitRecommendations(text) {
  const raw = String(text || '').trim().replace(/^```(?:json)?\s*|\s*```$/g, '');
  try { const structured = JSON.parse(raw); if (Array.isArray(structured.games) && typeof structured.summary === 'string') return { structured: true, lead: structured.summary, games: structured.games.map((game) => ({ name: game.name || '', url: game.store_url || '', image: game.image_url || '', reason: game.reason || '', selected: Boolean(game.selected) })).filter((game) => game.name) }; } catch (_) { /* older Markdown responses remain supported */ }
  const lines = String(text || '').split(/\r?\n/);
  const tableStart = lines.findIndex((line, index) => line.trim().startsWith('|') && lines[index + 1]?.includes('---'));
  if (tableStart >= 0) {
    const cells = (line) => line.trim().replace(/^\||\|$/g, '').split('|').map((cell) => cell.trim());
    const headers = cells(lines[tableStart]).map((cell) => cell.toLowerCase());
    const indexOf = (names) => headers.findIndex((header) => names.some((name) => header.includes(name)));
    const titleIndex = indexOf(['title', 'name', '游戏', '名称']);
    if (titleIndex >= 0) {
      const read = (values, names) => { const index = indexOf(names); return index >= 0 ? values[index] || '' : ''; };
      const games = lines.slice(tableStart + 2).filter((line) => line.trim().startsWith('|')).map((line) => { const values = cells(line); const title = values[titleIndex] || ''; const link = title.match(/\[([^\]]+)\]\(([^)]+)\)/); return { name: link?.[1] || title.replace(/[\*`]/g, ''), url: read(values, ['steam', '链接', 'link']) || link?.[2] || '', image: read(values, ['image', '封面', '图片']), match: read(values, ['match', '匹配']), price: read(values, ['price', '价格']), original: read(values, ['original', '原价']), discount: read(values, ['discount', '折扣']), reason: read(values, ['reason', 'description', '推荐', '理由']), selected: /^(true|yes|是|当前)/i.test(read(values, ['selected', '选择', '当前'])) }; }).filter((game) => game.name);
      if (games.length) return { structured: false, lead: lines.slice(0, tableStart).join('\n').trim(), games };
    }
  }
  const games = []; const matches = [...text.matchAll(recommendationPattern)];
  for (let index = 0; index < matches.length; index++) {
    const match = matches[index];
    const end = matches[index + 1]?.index ?? text.length;
    const description = text.slice(match.index + match[0].length, end).trim();
    const url = new URL(match[2]);
    games.push({ name: match[1], url: url.href, image: match[3] || '', reason: description, start: match.index, end });
  }
  return { structured: false, lead: text.slice(0, games[0]?.start || 0).trim(), games };
}
function paperFor(game, index) {
  const paper = node('article', 'paper curation-card ' + (index ? `small-paper paper-${index + 1}` : 'hero-paper'));
  paper.tabIndex = 0; paper.setAttribute('aria-label', `选择 ${game.name} 推荐`);
  const head = node('div', 'paper-head'); head.append(node('span', 'mono', `EXHIBIT ${String(index + 1).padStart(2, '0')}`), node('span', 'match', '匹配度未提供'));
  const grid = node('div', 'paper-grid'); const cover = node('div', 'cover');
  if (game.image) { const img = node('img'); img.src = game.image; img.alt = `${game.name} 游戏封面`; img.loading = 'lazy'; img.onerror = () => img.remove(); cover.append(img); }
  else cover.append(node('span', '', game.name));
  const copy = node('div', 'paper-copy'); copy.append(node('h3', '', game.name), node('p', 'paper-kicker', '馆藏推荐'), node('p', '', game.reason || '查看这件藏品的 Steam 页面。'));
  const link = node('a', 'text-btn', '在 Steam 查看 ↗'); link.href = game.url; link.target = '_blank'; link.rel = 'noopener noreferrer'; copy.append(link); grid.append(cover, copy);
  const toggle = button('展开依据 ＋', 'reason-toggle', () => { reason.classList.toggle('open'); paper.classList.toggle('flip-open'); toggle.textContent = reason.classList.contains('open') ? '收起依据 −' : '展开依据 ＋'; });
  const reason = node('div', 'reason'); reason.append(node('span', 'mono', "CURATOR'S NOTE"), node('p', '', game.reason || '后端本轮未提供更详细的推荐依据。'));
  paper.append(head, grid, toggle, reason);
  paper.addEventListener('click', (event) => { if (event.target.closest('button,a')) return; const wall = paper.closest('.curation-wall'); wall.querySelectorAll('.paper').forEach((el) => el.classList.remove('selected')); wall.querySelectorAll('.reason.open').forEach((el) => el.classList.remove('open')); wall.querySelectorAll('.reason-toggle').forEach((el) => { el.textContent = '展开依据 ＋'; }); paper.classList.add('selected'); toggle.click(); });
  paper.addEventListener('keydown', (event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); paper.click(); } });
  return paper;
}
function renderAssistantText(answer, text) {
  answer.replaceChildren(); const parsed = splitRecommendations(text);
  if (parsed?.structured && !parsed.games.length) { answer.append(node('p', 'answer-lead plain-reply', parsed.lead || '正在整理馆藏记录…')); return; }
  if (!parsed?.games?.length) { answer.append(node('p', 'answer-lead plain-reply', text || '正在整理馆藏记录…')); return; }
  if (parsed.lead) answer.append(node('p', 'answer-lead', parsed.lead));
  const summary = node('div', 'curation-summary'); summary.append(node('strong', '', `根据你的条件，我找到了 ${parsed.games.length} 款 Steam 游戏。`), node('span', '', `${parsed.games.length} 款结果${parsed.games.some((game) => game.url) ? ' · 可直接查看 Steam 页面' : ''}`)); answer.append(summary);
  const stack = node('div', 'curation-wall'); const selected = parsed.games.find((game) => game.selected); if (selected) { const feature = node('section', 'curation-featured'); feature.append(node('div', 'section-label', `当前选择：${selected.name}`), paperFor(selected, 0)); stack.append(feature); } const others = node('section', 'curation-others'); others.append(node('div', 'section-label', '其他推荐')); const grid = node('div', 'curation-grid'); parsed.games.filter((game) => !selected || game.name !== selected.name).forEach((game, index) => grid.append(paperFor(game, index + 1))); others.append(grid); stack.append(others); answer.append(stack);
  [...answer.children].forEach((child, index) => child.style.setProperty('--line-index', index));
}
function messageElement(message, index) {
  if (message.role === 'user') { const outer = node('div', 'message user-message'); const meta = node('div', 'message-meta'); meta.append(node('span', 'role-tag', '访客提问'), node('span', 'mono', timeText(message.time))); outer.append(meta, node('p', '', message.content)); return outer; }
  const outer = node('div', 'assistant-block'); outer.dataset.messageIndex = index; const meta = node('div', 'message-meta'); meta.append(node('span', 'role-tag assistant-tag', '夜班讲解'), node('span', 'mono', timeText(message.time))); outer.append(meta);
  if (message.processing) { const line = node('div', 'status-line'); line.append(node('span', 'pulse'), node('span', 'status-stage', message.stage || '正在分析偏好')); outer.append(line); }
  const answer = node('div', 'answer'); renderAssistantText(answer, message.content || ''); outer.append(answer);
  if (message.error) { const note = node('div', 'state-note'); note.append(node('strong', '', message.error)); if (message.retry) note.append(button('重试', 'state-action', message.retry)); outer.append(note); }
  if (message.incomplete) { const note = node('div', 'state-note'); note.append(node('strong', '', '回答尚未完成')); note.append(button('继续生成', 'state-action', () => continueGeneration(message))); outer.append(note); }
  const isLatest = index === state.messages.length - 1;
  if (isLatest && !message.processing && !message.error && !message.incomplete) outer.append(button('继续追问 ↗', 'followup-btn', () => $('#prompt').focus()));
  return outer;
}
function renderMessages() {
  const feed = $('#chatFeed');
  const wasAtBottom = feed.hidden || feed.scrollHeight - feed.scrollTop - feed.clientHeight < 80;
  const isEmptyConversation = state.messages.length === 0 && !state.busy;
  $('#emptyState').hidden = !isEmptyConversation;
  feed.hidden = isEmptyConversation;
  $('#conversation').classList.toggle('is-empty', isEmptyConversation);
  const area = $('#messages');
  area.replaceChildren();
  state.messages.forEach((message, index) => area.append(messageElement(message, index)));
  if (isEmptyConversation) feed.scrollTop = 0;
  else if (wasAtBottom) feed.scrollTop = feed.scrollHeight;
}
document.addEventListener('click', (event) => {
  if (event.target.closest('.curation-card')) return;
  document.querySelectorAll('.curation-card .reason.open').forEach((reason) => {
    reason.classList.remove('open');
    const toggle = reason.parentElement?.querySelector('.reason-toggle');
    if (toggle) toggle.textContent = '展开依据 ＋';
  });
});
const stages = ['正在分析偏好', '正在读取 Steam 信息', '正在比对游戏特征', '正在整理推荐理由'];
function startStages(message) { let index = 0; clearInterval(message.stageTimer); message.stageTimer = setInterval(() => { if (!message.processing) return clearInterval(message.stageTimer); message.stage = stages[index++ % stages.length]; const label = $('#messages .assistant-block:last-child .status-stage'); if (label) label.textContent = message.stage; }, 2400); }
function stopStages(message) { message.processing = false; clearInterval(message.stageTimer); }

function handleStreamEvent(event, assistant, threadId) {
  if (threadId !== state.threadId) return;
  if (event.event === 'status') { assistant.stage = typeof event.data === 'string' ? event.data : stages[0]; renderMessages(); }
  if (event.event === 'token') { assistant.rawContent = (assistant.rawContent || '') + String(event.data || ''); }
  if (event.event === 'snapshot') {
    const snapshot = event.data || {}; assistant.stage = snapshot.stream_status || assistant.stage;
    if (snapshot.error) assistant.error = typeof snapshot.error === 'string' ? snapshot.error : snapshot.error.message;
    renderMessages();
  }
  if (event.event === 'error') { assistant.error = event.data?.message || '生成途中遇到问题'; renderMessages(); }
  if (event.event === 'done') {
    stopStages(assistant); assistant.content = event.data?.reply || assistant.content; assistant.incomplete = event.data?.status !== 'success';
    if (assistant.incomplete && !assistant.error) assistant.error = '回答可能未完整生成';
    state.busy = false; renderMessages(); refreshSessions().catch(() => {});
  }
  if (event.event === 'cancelled') { stopStages(assistant); assistant.content = event.data?.reply || assistant.content; assistant.incomplete = true; state.busy = false; renderMessages(); }
}
async function runMessage(text, retry = false) {
  if (state.busy) return; state.busy = true;
  const threadId = state.threadId || api.createSession(); state.threadId = threadId;
  if (!retry) state.messages.push({ role: 'user', content: text, time: new Date().toISOString() });
  const assistant = { role: 'assistant', content: '', processing: true, stage: stages[0], retry: () => runMessage(text, true) };
  state.messages.push(assistant); renderMessages(); startStages(assistant);
  const controller = new AbortController(); state.stream = controller; let finished = false;
  try {
    await api.sendMessage(threadId, text, (event) => { handleStreamEvent(event, assistant, threadId); if (event.event === 'done' || event.event === 'cancelled') finished = true; }, controller.signal);
    if (!finished) { stopStages(assistant); assistant.incomplete = true; assistant.error = '连接中断，回答尚未完成'; renderMessages(); }
  } catch (error) {
    stopStages(assistant); if (error.name !== 'AbortError') { assistant.error = error.message; assistant.incomplete = !!assistant.content; if (error.status === 401) { $('#prompt').value = text; $('#charCount').textContent = `${text.length} / ${MAX_MESSAGE_LENGTH}`; expireLogin(); } renderMessages(); }
  } finally { clearInterval(assistant.stageTimer); if (state.stream === controller) state.stream = null; state.busy = false; refreshSessions().catch(() => {}); }
}
async function attachRun(threadId) {
  const assistant = { role: 'assistant', content: '', processing: true, stage: stages[0] }; state.messages.push(assistant); renderMessages(); startStages(assistant);
  const controller = new AbortController(); state.stream = controller;
  try { const attached = await api.attachRun(threadId, (event) => handleStreamEvent(event, assistant, threadId), controller.signal); if (!attached) { state.messages.pop(); renderMessages(); } }
  catch (error) { if (error.name !== 'AbortError') { assistant.error = error.message; assistant.incomplete = true; renderMessages(); } }
  finally { stopStages(assistant); state.stream = null; state.busy = false; }
}
async function continueGeneration(message) { if (state.busy) return; const id = state.threadId; try { const attached = await api.attachRun(id, (event) => handleStreamEvent(event, message, id)); if (!attached) await runMessage('请继续刚才未完成的回答'); } catch (error) { showFailure(error, () => continueGeneration(message)); } }

function openAuth() { $('#authError').textContent = ''; $('#authDialog').showModal(); }
function syncAuthMenu() { $('#menuAuthAction').textContent = state.user && !state.authExpired ? '退出登录' : '登录'; }
function openSteam() {
  $('#steamError').textContent = '';
  if (!$('#steamGuide .help-mark')) { const mark = node('span', 'help-mark', '?'); mark.title = '用于读取你的 Steam 游戏档案，帮助生成更定制化的推荐。'; mark.setAttribute('aria-label', '绑定 Steam 的作用'); $('#steamGuide').append(' ', mark); }
  const loggedIn = Boolean(state.user) && !state.authExpired;
  $('#steamGuide').textContent = loggedIn ? '绑定后可根据你的 Steam 档案获得更个性化的游戏推荐。' : '请先在右上角登录或注册，登录完成后再回来绑定 Steam。';
  $('#steamInput').disabled = !loggedIn;
  $('#steamSubmit').hidden = !loggedIn;
  $('#steamLoginGuide').hidden = loggedIn;
  $('#steamDialog').showModal();
}
async function loadAccount() {
  try { const data = await api.getCurrentUser(); state.user = data.user; clearNotice(); syncAuthMenu(); $('#avatar').textContent = (data.user.username || '?')[0].toUpperCase(); await refreshSessions(); try { const binding = await api.getSteamBinding(); steamIdValue = binding.steam_id || ''; $('#steamStatus').textContent = steamIdValue ? 'STEAM 已绑定' : 'STEAM 未绑定'; $('#steamId').textContent = steamIdValue ? `${steamIdValue.slice(0, 4)}****${steamIdValue.slice(-4)}` : '连接你的 Steam 档案'; $('#copySteam').hidden = !steamIdValue; setSteamProfile(steamIdValue); } catch (bindingError) { steamIdValue = ''; $('#steamStatus').textContent = 'STEAM 状态读取失败'; $('#steamId').textContent = '点击重试或绑定'; $('#copySteam').hidden = true; setSteamProfile(''); if (bindingError.status === 401) throw bindingError; } if (state.threadId && state.sessions.some((s) => s.thread_id === state.threadId)) await selectSession(state.threadId); else if (state.sessions.length) await selectSession(state.sessions[0].thread_id); else newSession(); }
  catch (error) { if (error.status === 401) { expireLogin(); } else showFailure(error, loadAccount); }
}

$('#newSession').addEventListener('click', newSession);
$('#avatar').addEventListener('click', () => { const menu = $('#accountMenu'); menu.hidden = !menu.hidden; $('#avatar').setAttribute('aria-expanded', String(!menu.hidden)); });
document.addEventListener('click', (event) => { if (!event.target.closest('.account-wrap')) { $('#accountMenu').hidden = true; $('#avatar').setAttribute('aria-expanded', 'false'); } });
$('#menuToggle').addEventListener('click', () => $('#rail').classList.add('open'));
$('#closeRail').addEventListener('click', () => $('#rail').classList.remove('open'));
$('#backToSessions').addEventListener('click', () => $('#rail').classList.add('open'));
$('#steamBinding').addEventListener('click', openSteam); $('#reloginBtn').addEventListener('click', openSteam);
$('#steamLoginGuide').addEventListener('click', () => { $('#steamDialog').close(); $('#accountMenu').hidden = false; openAuth(); });
$('#recoverBtn').addEventListener('click', openAuth);
$('#menuAuthAction').addEventListener('click', async () => { if (!state.user) return openAuth(); try { await api.logout(); state.user = null; syncAuthMenu(); $('#avatar').textContent = '♙'; } catch (error) { showFailure(error); } });
const menuActions = [...document.querySelectorAll('#accountMenu > button:not(#menuAuthAction)')];
menuActions.forEach((item, index) => item.addEventListener('click', async () => {
  $('#accountMenu').hidden = true;
  if (!state.user) return openAuth();
  if (index === 0) return toast(`个人资料：${state.user.username || '当前账户'}`);
  if (index === 1) return openSteam();
  if (index === 2) { const theme = state.user.theme === 'light' ? 'dark' : 'light'; try { await api.setTheme(theme); state.user.theme = theme; document.documentElement.dataset.theme = theme; toast(`偏好设置已切换为${theme === 'light' ? '浅色' : '深色'}主题`); } catch (error) { showFailure(error); } return; }
  if (index === 3) { $('#rail').classList.add('open'); return; }
  if (index === 4) return toast('数据与隐私：当前会话由服务端安全保存。');
  toast('帮助与反馈：请通过项目支持渠道提交问题。');
}));
$('#copySteam').addEventListener('click', async (event) => { event.stopPropagation(); if (!steamIdValue) return toast('当前没有可复制的 Steam ID'); try { if (navigator.clipboard?.writeText) await navigator.clipboard.writeText(steamIdValue); else { const helper = document.createElement('textarea'); helper.value = steamIdValue; helper.setAttribute('aria-hidden', 'true'); helper.style.cssText = 'position:fixed;opacity:0;pointer-events:none'; document.body.appendChild(helper); helper.select(); if (!document.execCommand('copy')) throw new Error('copy failed'); helper.remove(); } toast('已复制'); } catch { toast('复制失败，请检查浏览器剪贴板权限'); } });
$('#prompt').addEventListener('input', () => $('#charCount').textContent = `${$('#prompt').value.length} / ${MAX_MESSAGE_LENGTH}`);
$('#prompt').addEventListener('keydown', (event) => { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); $('#sendBtn').click(); } });
$('#sendBtn').addEventListener('click', () => { const text = $('#prompt').value.trim(); if (!text) return toast('先写下一句你的夜班线索'); if (text.length > MAX_MESSAGE_LENGTH) return toast(`输入不能超过 ${MAX_MESSAGE_LENGTH} 个字符`); if (state.authExpired || !state.user) return openAuth(); $('#prompt').value = ''; $('#charCount').textContent = `0 / ${MAX_MESSAGE_LENGTH}`; runMessage(text); });
function pressFeedback(element) { element.classList.remove('is-pressed'); void element.offsetWidth; element.classList.add('is-pressed'); setTimeout(() => element.classList.remove('is-pressed'), 280); }
$('#newSession').addEventListener('click', () => { pressFeedback($('#newSession')); $('#newSession').classList.add('is-opening'); setTimeout(() => $('#newSession').classList.remove('is-opening'), 800); });
$('#sendBtn').addEventListener('click', () => pressFeedback($('#sendBtn')));
$('#authClose').addEventListener('click', () => $('#authDialog').close());
$('#authMode').addEventListener('click', () => { const register = $('#authForm').dataset.mode !== 'register'; $('#authForm').dataset.mode = register ? 'register' : 'login'; $('#authHeading').textContent = register ? '注册夜班通行证' : '重新登录'; $('#authSubmit').textContent = register ? '注册' : '登录'; $('#authMode').textContent = register ? '已有账号？登录' : '注册账号'; });
$('#authForm').addEventListener('submit', async (event) => { event.preventDefault(); try { await api.login($('#authName').value.trim(), $('#authPassword').value, $('#authForm').dataset.mode === 'register'); $('#authPassword').value = ''; $('#authDialog').close(); await loadAccount(); } catch (error) { $('#authError').textContent = error.message; } });
$('#steamClose').addEventListener('click', () => $('#steamDialog').close());
$('#steamForm').addEventListener('submit', async (event) => { event.preventDefault(); try { await api.rebindSteam($('#steamInput').value.trim()); $('#steamDialog').close(); const binding = await api.getSteamBinding(); steamIdValue = binding.steam_id || ''; $('#steamStatus').textContent = 'STEAM 已绑定'; $('#steamId').textContent = `${steamIdValue.slice(0, 4)}****${steamIdValue.slice(-4)}`; $('#copySteam').hidden = !steamIdValue; setSteamProfile(steamIdValue); $('#steamBinding .status-led').classList.add('sweeping'); setTimeout(() => $('#steamBinding .status-led').classList.remove('sweeping'), 1000); toast('Steam 档案已连接'); } catch (error) { if (error.status === 401) { $('#steamDialog').close(); expireLogin(); } else $('#steamError').textContent = error.message; } });
if (!matchMedia('(prefers-reduced-motion: reduce)').matches) {
  const torch = $('.torch'); let x = innerWidth * .72, y = innerHeight * .38, tx = x, ty = y;
  addEventListener('pointermove', (event) => {
    tx = event.clientX; ty = event.clientY;
    const px = (event.clientX / innerWidth - .5) * 14; const py = (event.clientY / innerHeight - .5) * 10;
    document.documentElement.style.setProperty('--parallax-x', `${px.toFixed(2)}px`); document.documentElement.style.setProperty('--parallax-y', `${py.toFixed(2)}px`);
    document.querySelectorAll('.new-session:hover,.send-btn:hover,.quiet-btn:hover,.steam-link:hover,.session-menu-btn:hover').forEach((button) => { const rect = button.getBoundingClientRect(); button.style.setProperty('--magnet-x', `${Math.max(-4, Math.min(4, (event.clientX - (rect.left + rect.width / 2)) * .08)).toFixed(2)}px`); button.style.setProperty('--magnet-y', `${Math.max(-3, Math.min(3, (event.clientY - (rect.top + rect.height / 2)) * .08)).toFixed(2)}px`); });
  });
  (function animate() { x += (tx - x) * .12; y += (ty - y) * .12; torch.style.left = x + 'px'; torch.style.top = y + 'px'; requestAnimationFrame(animate); })();
}
renderMessages();
loadAccount();

// Quiet room motion: the archive remains alive while the existing chat flow stays intact.
(function initNightShiftAtmosphere() {
  const canvas = document.querySelector('#dustCanvas');
  const promptField = document.querySelector('#prompt');
  if (!canvas || !promptField || matchMedia('(prefers-reduced-motion: reduce)').matches) return;
  const ctx = canvas.getContext('2d');
  const particles = Array.from({ length: 54 }, (_, index) => ({ x: Math.random(), y: Math.random(), size: .35 + Math.random() * 1.3, speed: .00008 + Math.random() * .00018, drift: (Math.random() - .5) * .00008, phase: index }));
  let width = 0; let height = 0; let last = performance.now(); let idleTimer;
  const resize = () => { const ratio = Math.min(devicePixelRatio || 1, 2); width = innerWidth; height = innerHeight; canvas.width = width * ratio; canvas.height = height * ratio; ctx.setTransform(ratio, 0, 0, ratio, 0, 0); };
  const frame = (now) => { const delta = now - last; last = now; ctx.clearRect(0, 0, width, height); for (const particle of particles) { particle.y -= particle.speed * delta; particle.x += particle.drift * delta; if (particle.y < -.02) particle.y = 1.02; if (particle.x < -.04 || particle.x > 1.04) particle.drift *= -1; ctx.fillStyle = `rgba(225, 199, 135, ${.12 + particle.size * .08})`; ctx.beginPath(); ctx.arc(particle.x * width, particle.y * height, particle.size, 0, Math.PI * 2); ctx.fill(); } requestAnimationFrame(frame); };
  const markWriting = () => { document.body.classList.toggle('is-writing', promptField.value.length > 0); document.body.classList.add('is-focused'); clearTimeout(idleTimer); idleTimer = setTimeout(() => { document.body.classList.remove('is-writing', 'is-focused'); document.body.classList.add('is-idle'); }, 6500); };
  addEventListener('resize', resize); promptField.addEventListener('focus', () => { document.body.classList.add('is-focused'); document.body.classList.remove('is-idle'); }); promptField.addEventListener('input', markWriting); promptField.addEventListener('blur', () => { clearTimeout(idleTimer); idleTimer = setTimeout(() => document.body.classList.remove('is-focused', 'is-writing'), 3200); }); resize(); requestAnimationFrame(frame);
}());









