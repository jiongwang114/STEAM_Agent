const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../night-museum-frontend/app.js'), 'utf8');

class Element {
  constructor(tag) {
    this.tagName = tag;
    this.childNodes = [];
    this.className = '';
    this.dataset = {};
    this.attributes = {};
    this.handlers = {};
    this.style = { setProperty() {} };
    this.hidden = false;
    this.scrollHeight = 1000;
    this.scrollTop = 1000;
    this.clientHeight = 500;
    this.classList = {
      add: (...names) => { this.className = [...new Set([...this.className.split(/\s+/).filter(Boolean), ...names])].join(' '); },
      remove: (...names) => { this.className = this.className.split(/\s+/).filter((name) => name && !names.includes(name)).join(' '); },
      contains: (name) => this.className.split(/\s+/).includes(name),
      toggle: (name, force) => {
        const enabled = force ?? !this.classList.contains(name);
        if (enabled) this.classList.add(name); else this.classList.remove(name);
        return enabled;
      },
    };
  }
  get children() { return this.childNodes.filter((child) => child instanceof Element); }
  get textContent() { return this.childNodes.map((child) => child instanceof Element ? child.textContent : child).join(''); }
  set textContent(text) { this.childNodes = [String(text)]; }
  append(...children) { children.forEach((child) => { if (child instanceof Element) child.parent = this; this.childNodes.push(child); }); }
  replaceChildren(...children) { this.childNodes = []; this.append(...children); }
  remove() { if (this.parent) this.parent.childNodes = this.parent.childNodes.filter((child) => child !== this); }
  setAttribute(name, value) { this.attributes[name] = value; }
  addEventListener(name, action) { (this.handlers[name] ||= []).push(action); }
  click() { (this.handlers.click || []).forEach((action) => action({ target: this })); }
  matches(selector) {
    if (selector.startsWith('.')) return selector.slice(1).split('.').every((name) => this.classList.contains(name));
    const index = selector.match(/^\[data-message-index="(\d+)"\]$/);
    return index ? String(this.dataset.messageIndex) === index[1] : false;
  }
  querySelectorAll(selector) {
    return this.children.flatMap((child) => [...(child.matches(selector) ? [child] : []), ...child.querySelectorAll(selector)]);
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
}

function functionSource(name) {
  const start = source.indexOf('function ' + name + '(');
  assert.ok(start >= 0, name + ' exists');
  const remaining = source.slice(start);
  const firstLine = remaining.split(/\r?\n/)[0];
  if (firstLine.endsWith('}')) return firstLine;
  const end = remaining.search(/\r?\n}/);
  assert.ok(end >= 0, name + ' has an end');
  return remaining.slice(0, end).concat('\n}');
}

function harness() {
  const elements = Object.fromEntries(['chatFeed', 'emptyState', 'conversation', 'messages', 'prompt'].map((name) => [name, new Element('div')]));
  const state = { threadId: 'thread', messages: [], busy: true };
  const context = vm.createContext({
    URL,
    Intl,
    state,
    INITIAL_PROGRESS: '正在理解问题',
    document: {
      createElement: (tag) => new Element(tag),
      querySelector: (selector) => selector.startsWith('#') ? elements[selector.slice(1)] : elements.messages.querySelector(selector),
    },
    $: (selector) => elements[selector.slice(1)],
    setArchiveMode() {},
    setArchiveRoom() {},
    installRevealObserver() {},
    refreshSessions: () => Promise.resolve(),
    setTimeout() { throw new Error('Presentation must not add a second playback timer'); },
  });
  const functions = ['safeSteamUrl', 'safeImageUrl', 'inferGameType', 'timeText', 'node', 'button', 'inlineMarkdown', 'streamPreview', 'presentationGame', 'updatePresentation', 'applyDoneEvent', 'splitRecommendations', 'paperFor', 'renderAssistantText', 'messageElement', 'renderMessages', 'handleStreamEvent'];
  vm.runInContext(source.match(/^const recommendationPattern = .+$/m)[0] + '\n' + functions.map(functionSource).join('\n'), context);
  const assistant = { role: 'assistant', content: '', processing: true, stage: '处理中' };
  state.messages.push(assistant);
  context.renderMessages();
  return { context, assistant, state, elements, send: (event, data) => context.handleStreamEvent({ event, data }, assistant, 'thread') };
}

function game(appid) {
  return { appid: String(appid), name: '游戏 ' + appid, store_url: 'https://store.steampowered.com/app/' + appid, image_url: '', reason: '符合条件' };
}

test('summary, each card and done preserve DOM and expanded card state', () => {
  const { assistant, elements, send } = harness();
  send('presentation', { total_games: 2 });
  const outer = elements.messages.children[0];
  const answer = outer.querySelector('.answer');
  const lead = answer.querySelector('.answer-lead');
  assert.equal(lead.className, 'answer-lead');
  assert.ok(answer.classList.contains('presentation-answer'));
  assert.equal(answer.querySelector('.curation-wall').hidden, true);
  const summary = '已核对这些游戏：\n包含 "引号"、\\ 和 🎮';
  const prefix = '{"summary":' + JSON.stringify(summary) + ',"games":[';
  for (const character of prefix) {
    send('token', character);
    assert.equal(answer.querySelector('.answer-lead'), lead);
  }
  assert.equal(lead.textContent, summary);
  assert.equal(lead.hidden, false);
  send('card', { index: 0, game: game(10) });
  const firstCard = answer.querySelector('.curation-card');
  firstCard.querySelector('.reason-toggle').click();
  assert.equal(firstCard.querySelector('.reason').classList.contains('open'), true);
  send('card', { index: 1, game: game(20) });
  send('card', { index: 0, game: game(10) });
  assert.equal(answer.querySelectorAll('.curation-card').length, 2);
  assert.equal(answer.querySelector('.curation-card'), firstCard);
  assert.equal(firstCard.querySelector('.reason').classList.contains('open'), true);
  assert.equal(answer.querySelector('.curation-wall').hidden, false);
  assert.equal(assistant.processing, true);
  send('done', { status: 'success', reply: JSON.stringify({ summary, games: [game(10), game(20)] }) });
  assert.equal(assistant.processing, false);
  assert.equal(elements.messages.children[0], outer);
  assert.equal(answer.querySelector('.answer-lead'), lead);
  assert.equal(answer.querySelector('.curation-card'), firstCard);
  assert.equal(firstCard.querySelector('.reason').classList.contains('open'), true);
  assert.equal(outer.querySelector('.status-line'), null);
  assert.ok(answer.classList.contains('presentation-answer'));
  assert.ok(outer.querySelector('.followup-btn'));
});

test('snapshot restores cards immediately and later events do not duplicate them', () => {
  const { assistant, elements, send } = harness();
  const prefix = '{"summary":"已核对","games":[' + JSON.stringify(game(10));
  send('snapshot', { stream_status: '正在展示结果', reply: prefix, presentation: { total_games: 2, games: [game(10)] } });
  const answer = elements.messages.children[0].querySelector('.answer');
  const firstCard = answer.querySelector('.curation-card');
  assert.equal(answer.querySelector('.answer-lead').textContent, '已核对');
  send('card', { index: 0, game: game(10) });
  send('card', { index: 1, game: game(20) });
  assert.equal(assistant.presentation.games.length, 2);
  assert.equal(answer.querySelector('.curation-card'), firstCard);
  assert.equal(answer.querySelectorAll('.curation-card').length, 2);
});

test('summary-only replies finish in place and never expose JSON escapes', () => {
  const { context, elements, send } = harness();
  assert.equal(context.streamPreview('{"summary":"a\\u00'), 'a');
  assert.equal(context.streamPreview('{"summary":"a\\u0001'), 'a\u0001');
  send('presentation', { total_games: 0 });
  const outer = elements.messages.children[0];
  const lead = outer.querySelector('.answer-lead');
  assert.equal(lead.className, 'answer-lead plain-reply');
  const reply = JSON.stringify({ summary: '请说明 **你的偏好**。', games: [] });
  send('token', reply);
  send('done', { status: 'success', reply });
  assert.equal(elements.messages.children[0], outer);
  assert.equal(outer.querySelector('.answer-lead'), lead);
  assert.equal(lead.textContent, '请说明 你的偏好。');
  assert.equal(outer.querySelectorAll('.curation-card').length, 0);
});

test('completed stages do not overwrite useful progress and other threads are ignored', () => {
  const { context, assistant } = harness();
  context.handleStreamEvent({ event: 'stage', data: { category: 'validation', status: 'started', message: '正在核对游戏信息' } }, assistant, 'thread');
  context.handleStreamEvent({ event: 'stage', data: { category: 'analysis', status: 'completed', message: '' } }, assistant, 'thread');
  assert.equal(assistant.stage, '正在核对游戏信息');
  context.handleStreamEvent({ event: 'stage', data: { category: 'analysis', status: 'started' } }, assistant, 'thread');
  assert.equal(assistant.stage, '正在核对游戏信息');
  context.handleStreamEvent({ event: 'presentation', data: { total_games: 1 } }, assistant, 'other-thread');
  assert.equal(assistant.presentation, undefined);
});

test('cancellation retains partial summary and already published cards', () => {
  const { assistant, elements, send } = harness();
  send('presentation', { total_games: 2 });
  send('token', '{"summary":"已核对的总结","games":[');
  send('card', { index: 0, game: game(10) });
  send('cancelled', { reply: '{"summary":"已核对的总结","games":[' });
  assert.equal(assistant.processing, false);
  assert.equal(assistant.incomplete, true);
  assert.equal(assistant.content, '已核对的总结');
  assert.equal(elements.messages.querySelectorAll('.curation-card').length, 1);
});

test('completed snapshot and terminal done preserve restored cards', () => {
  const { assistant, elements, send } = harness();
  const reply = JSON.stringify({ summary: '完成的回答', games: [game(10), game(20)] });
  send('snapshot', { status: 'completed', reply, presentation: { total_games: 2, games: [game(10), game(20)] } });
  const outer = elements.messages.children[0];
  const firstCard = outer.querySelector('.curation-card');
  send('done', { status: 'success', reply });
  assert.equal(assistant.processing, false);
  assert.equal(elements.messages.children[0], outer);
  assert.equal(outer.querySelector('.curation-card'), firstCard);
  assert.equal(outer.querySelectorAll('.curation-card').length, 2);
});

test('legacy token and done events still render the complete answer', () => {
  const { assistant, elements, send } = harness();
  const reply = JSON.stringify({ summary: '旧协议回答', games: [game(10)] });
  send('token', reply);
  assert.equal(elements.messages.querySelectorAll('.curation-card').length, 0);
  send('done', { status: 'success', reply });
  assert.equal(assistant.processing, false);
  assert.equal(elements.messages.querySelectorAll('.curation-card').length, 1);
  assert.equal(elements.messages.querySelector('.answer-lead').textContent, '旧协议回答');
});
