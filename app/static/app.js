/* DocuMind AI web interface. Plain JavaScript, no build step. */
(() => {
  'use strict';

  const TOKEN_KEY = 'documind.token';
  const ALLOWED_EXT = ['.pdf', '.docx', '.txt', '.md'];
  const MAX_UPLOAD_BYTES = 15 * 1024 * 1024;

  const $ = (sel, root = document) => root.querySelector(sel);

  const el = {
    authView: $('#auth-view'), authForm: $('#auth-form'), authHeading: $('#auth-heading'),
    authEmail: $('#auth-email'), authPassword: $('#auth-password'), authHint: $('#auth-password-hint'),
    authError: $('#auth-error'), authSubmit: $('#auth-submit'),
    appView: $('#app-view'), scrim: $('#scrim'),
    docList: $('#doc-list'), sessionList: $('#session-list'), scopeHint: $('#scope-hint'),
    dropzone: $('#dropzone'), fileInput: $('#file-input'), browseBtn: $('#browse-btn'),
    userEmail: $('#user-email'), thread: $('#thread'), threadInner: $('#thread-inner'),
    chatTitle: $('#chat-title'), modePill: $('#mode-pill'),
    composer: $('#composer'), input: $('#composer-input'), sendBtn: $('#send-btn'), hint: $('#composer-hint'),
    evidenceSub: $('#evidence-sub'), evidenceBody: $('#evidence-body'),
    usageDialog: $('#usage-dialog'), usageBody: $('#usage-body'),
    toasts: $('#toasts'), srStatus: $('#sr-status'),
  };

  const state = {
    token: null, user: null, health: null, authMode: 'login',
    docs: [], pending: [], selectedDocs: new Set(),
    sessions: [], sessionId: null,
    entries: [], selectedUid: null, streaming: false, nextUid: 1,
  };

  /* ------------------------------------------------------------ helpers */
  const esc = (value) =>
    String(value).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  const fmtNumber = (n) => Number(n || 0).toLocaleString();
  const fmtCost = (usd) => `$${Number(usd || 0).toFixed(usd > 0 && usd < 0.01 ? 4 : 2)}`;
  const fmtMs = (ms) => (ms >= 1000 ? `${(ms / 1000).toFixed(1)} s` : `${ms} ms`);
  const fmtBytes = (b) => (b >= 1048576 ? `${(b / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(b / 1024))} KB`);
  const parseUtc = (iso) => new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(iso) ? iso : `${iso}Z`);
  const truncate = (s, n) => (s.length > n ? `${s.slice(0, n - 1).trimEnd()}…` : s);
  const isPdf = (name) => name.toLowerCase().endsWith('.pdf');
  const unitLabel = (name, n) => (isPdf(name) ? (n === 1 ? 'page' : 'pages') : n === 1 ? 'section' : 'sections');
  const pageLabel = (name, page) => (isPdf(name) ? `page ${page}` : `section ${page}`);

  function store(key, value) {
    try {
      if (value === undefined) return localStorage.getItem(key);
      if (value === null) localStorage.removeItem(key);
      else localStorage.setItem(key, value);
    } catch (_) { /* storage can be blocked; the app still works for this tab */ }
    return null;
  }

  function announce(text) {
    el.srStatus.textContent = '';
    window.setTimeout(() => { el.srStatus.textContent = text; }, 40);
  }

  function toast(message, kind = 'info') {
    const node = document.createElement('div');
    node.className = `toast${kind === 'error' ? ' is-error' : ''}`;
    node.textContent = message;
    el.toasts.appendChild(node);
    window.setTimeout(() => node.remove(), kind === 'error' ? 7000 : 4000);
  }

  class ApiError extends Error {
    constructor(message, code = 'ERROR', status = 0) {
      super(message);
      this.code = code;
      this.status = status;
    }
  }

  async function toApiError(res) {
    let body = null;
    try { body = await res.json(); } catch (_) { /* not JSON */ }
    const err = body && body.error;
    let message = (err && err.message) || `Request failed (${res.status}).`;
    if (err && err.code === 'VALIDATION_ERROR' && err.details && err.details.length) {
      message = String(err.details[0].message).replace(/^Value error, /, '');
    }
    return new ApiError(message, (err && err.code) || 'ERROR', res.status);
  }

  async function api(path, { method = 'GET', json, form } = {}) {
    const headers = {};
    if (state.token) headers.Authorization = `Bearer ${state.token}`;
    let body;
    if (json !== undefined) { headers['Content-Type'] = 'application/json'; body = JSON.stringify(json); }
    if (form) body = form;
    let res;
    try {
      res = await fetch(path, { method, headers, body });
    } catch (_) {
      throw new ApiError('Could not reach the server. Check your connection and try again.', 'NETWORK');
    }
    if (!res.ok) {
      const err = await toApiError(res);
      if (err.status === 401 && state.token && !path.startsWith('/api/auth/')) {
        signOut('Your session expired. Sign in again.');
      }
      throw err;
    }
    if (res.status === 204) return null;
    return res.json();
  }

  /* ------------------------------------------------------------ auth */
  function setAuthMode(mode) {
    state.authMode = mode;
    const register = mode === 'register';
    for (const tab of document.querySelectorAll('.seg')) {
      const active = tab.dataset.mode === mode;
      tab.classList.toggle('is-active', active);
      tab.setAttribute('aria-selected', String(active));
    }
    el.authHeading.textContent = register ? 'Create your account' : 'Welcome back';
    el.authSubmit.textContent = register ? 'Create account' : 'Sign in';
    el.authPassword.autocomplete = register ? 'new-password' : 'current-password';
    el.authHint.hidden = !register;
    showAuthError('');
  }

  function showAuthError(message) {
    el.authError.hidden = !message;
    el.authError.textContent = message;
  }

  async function submitAuth(event) {
    event.preventDefault();
    const email = el.authEmail.value.trim();
    const password = el.authPassword.value;
    if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) return showAuthError('Enter a valid email address.');
    if (state.authMode === 'register' && password.length < 8) return showAuthError('Use a password with at least 8 characters.');
    if (!password) return showAuthError('Enter your password.');

    el.authSubmit.disabled = true;
    showAuthError('');
    try {
      const data = await api(`/api/auth/${state.authMode}`, { method: 'POST', json: { email, password } });
      state.token = data.access_token;
      state.user = data.user;
      store(TOKEN_KEY, state.token);
      el.authPassword.value = '';
      await enterApp();
    } catch (err) {
      showAuthError(err.message);
    } finally {
      el.authSubmit.disabled = false;
    }
  }

  function signOut(message) {
    state.token = null;
    state.user = null;
    store(TOKEN_KEY, null);
    Object.assign(state, { docs: [], pending: [], selectedDocs: new Set(), sessions: [], sessionId: null, entries: [], selectedUid: null, streaming: false });
    el.appView.hidden = true;
    el.authView.hidden = false;
    closeDrawers();
    if (message) showAuthError(message);
  }

  async function enterApp() {
    el.authView.hidden = true;
    el.appView.hidden = false;
    el.userEmail.textContent = state.user.email;
    renderThread();
    renderEvidence();
    await Promise.all([loadHealth(), refreshDocs(), refreshSessions()]);
    renderThread();
    el.input.focus();
  }

  /* ------------------------------------------------------------ health and mode */
  async function loadHealth() {
    try {
      state.health = await api('/api/health');
    } catch (_) {
      state.health = null;
    }
    const live = state.health && state.health.mode === 'claude';
    if (!state.health) {
      el.modePill.hidden = true;
    } else {
      el.modePill.hidden = false;
      el.modePill.className = `pill ${live ? 'is-live' : 'is-demo'}`;
      el.modePill.textContent = live ? 'AI answers on' : 'Demo mode';
      el.modePill.title = live ? `Model: ${state.health.model}` : 'No AI key is set, so answers are the closest passages from your documents.';
    }
    el.hint.textContent = live || !state.health
      ? 'Answers use only your documents. Press Enter to send, Shift+Enter for a new line.'
      : 'Demo mode: no AI key is set, so answers are the closest passages from your documents. Set ANTHROPIC_API_KEY for written answers.';
  }

  /* ------------------------------------------------------------ documents */
  async function refreshDocs() {
    try {
      state.docs = await api('/api/documents');
    } catch (err) {
      toast(err.message, 'error');
    }
    const ready = new Set(state.docs.filter((d) => d.status === 'ready').map((d) => d.id));
    for (const id of [...state.selectedDocs]) if (!ready.has(id)) state.selectedDocs.delete(id);
    renderDocs();
  }

  function renderDocs() {
    const rows = [];
    for (const p of state.pending) {
      rows.push(`<li class="doc is-busy"><span></span><div><span class="doc-name" title="${esc(p.name)}">${esc(p.name)}</span><span class="doc-meta"><span class="spinner"></span>Indexing…</span></div><span></span></li>`);
    }
    for (const d of state.docs) {
      const ready = d.status === 'ready';
      const failed = d.status === 'failed';
      const check = ready
        ? `<input type="checkbox" data-doc="${d.id}" aria-label="Search only ${esc(d.filename)}" ${state.selectedDocs.has(d.id) ? 'checked' : ''}>`
        : '<span></span>';
      let meta;
      if (ready) meta = `<span class="doc-meta">${fmtNumber(d.pages)} ${unitLabel(d.filename, d.pages)} · ${fmtNumber(d.words)} words</span>`;
      else if (failed) meta = `<span class="doc-meta is-error">${esc(d.error || 'Could not be indexed')}</span>`;
      else meta = '<span class="doc-meta"><span class="spinner"></span>Indexing…</span>';
      rows.push(
        `<li class="doc${ready ? '' : ' is-busy'}">${check}<div><span class="doc-name" title="${esc(d.filename)}">${esc(d.filename)}</span>${meta}</div>` +
        `<div class="doc-actions"><button type="button" class="doc-remove" data-remove-doc="${d.id}" aria-label="Remove ${esc(d.filename)}">Remove</button></div></li>`
      );
    }
    el.docList.innerHTML = rows.length ? rows.join('') : '<li class="empty-note">No documents yet. Add a PDF, Word, text or Markdown file to start asking questions.</li>';

    const readyCount = state.docs.filter((d) => d.status === 'ready').length;
    const picked = state.selectedDocs.size;
    el.scopeHint.textContent = !readyCount ? '' : picked ? `Searching only the ${picked} selected.` : `Searching all ${readyCount} ${readyCount === 1 ? 'document' : 'documents'}. Tick boxes to narrow.`;
    if (!state.entries.length) renderThread();
  }

  async function uploadFiles(fileList) {
    const files = [...fileList];
    for (const file of files) {
      const ext = file.name.includes('.') ? `.${file.name.split('.').pop().toLowerCase()}` : '';
      if (!ALLOWED_EXT.includes(ext)) { toast(`${file.name}: only PDF, DOCX, TXT and MD files are supported.`, 'error'); continue; }
      if (file.size > MAX_UPLOAD_BYTES) { toast(`${file.name} is ${fmtBytes(file.size)}. The limit is 15 MB.`, 'error'); continue; }
      const pending = { key: state.nextUid++, name: file.name };
      state.pending.push(pending);
      renderDocs();
      try {
        const form = new FormData();
        form.append('file', file);
        const doc = await api('/api/documents', { method: 'POST', form });
        toast(`${doc.filename} is ready to search.`);
        announce(`${doc.filename} is ready.`);
      } catch (err) {
        toast(`${file.name}: ${err.message}`, 'error');
      } finally {
        state.pending = state.pending.filter((p) => p.key !== pending.key);
        await refreshDocs();
      }
    }
  }

  async function removeDoc(id) {
    const doc = state.docs.find((d) => d.id === id);
    try {
      await api(`/api/documents/${id}`, { method: 'DELETE' });
      state.selectedDocs.delete(id);
      if (doc) toast(`Removed ${doc.filename}.`);
    } catch (err) {
      toast(err.message, 'error');
    }
    await refreshDocs();
  }

  /* ------------------------------------------------------------ sessions */
  async function refreshSessions() {
    try { state.sessions = await api('/api/sessions'); } catch (err) { toast(err.message, 'error'); }
    renderSessions();
  }

  function renderSessions() {
    if (!state.sessions.length) {
      el.sessionList.innerHTML = '<li class="empty-note">Your questions are saved here.</li>';
      return;
    }
    el.sessionList.innerHTML = state.sessions.map((s) =>
      `<li class="session${s.id === state.sessionId ? ' is-active' : ''}">` +
      `<button type="button" class="session-open" data-session="${s.id}" title="${esc(s.title)}">${esc(s.title)}</button>` +
      `<button type="button" class="session-remove" data-remove-session="${s.id}" aria-label="Delete chat ${esc(s.title)}">` +
      '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M5 5l10 10M15 5L5 15"/></svg></button></li>'
    ).join('');
  }

  async function openSession(id) {
    if (state.streaming) return;
    try {
      const messages = await api(`/api/sessions/${id}/messages`);
      const entries = [];
      let current = null;
      for (const m of messages) {
        if (m.role === 'user') {
          current = { uid: state.nextUid++, question: m.content, status: 'done', text: '', answer: '', citations: [], found: false, confidence: 'none', tools: [], messageId: null, feedback: null };
          entries.push(current);
        } else if (current) {
          Object.assign(current, { answer: m.content, citations: m.citations || [], found: !!m.found, confidence: m.confidence || 'none', tools: m.tools_used || [], messageId: m.id, feedback: m.feedback });
          current = null;
        }
      }
      state.entries = entries;
      state.sessionId = id;
      const last = [...entries].reverse().find((e) => e.citations.length) || entries[entries.length - 1];
      state.selectedUid = last ? last.uid : null;
      renderSessions();
      renderThread({ scroll: 'bottom' });
      renderEvidence();
      closeDrawers();
    } catch (err) {
      toast(err.message, 'error');
    }
  }

  async function removeSession(id) {
    try {
      await api(`/api/sessions/${id}`, { method: 'DELETE' });
      if (state.sessionId === id) newChat();
    } catch (err) {
      toast(err.message, 'error');
    }
    await refreshSessions();
  }

  function newChat() {
    if (state.streaming) return;
    state.sessionId = null;
    state.entries = [];
    state.selectedUid = null;
    renderSessions();
    renderThread();
    renderEvidence();
    closeDrawers();
    el.input.focus();
  }

  /* ------------------------------------------------------------ rendering: answers */
  const STOP = new Set('a an and are as at be been by can could did do does for from had has have he her his how i if in into is it its of on or our she should so that the their them then there these they this to was we were what when where which who why will with would you your tell me about please give show'.split(' '));

  function stem(w) {
    if (w.length > 4 && w.endsWith('ies')) return `${w.slice(0, -3)}y`;
    if (w.length > 5 && w.endsWith('ing')) return w.slice(0, -3);
    if (w.length > 4 && w.endsWith('ed')) return w.slice(0, -2);
    if (w.length > 3 && w.endsWith('s') && !w.endsWith('ss')) return w.slice(0, -1);
    return w;
  }
  const tokens = (text) => (text.toLowerCase().match(/[a-z0-9]+/g) || []).filter((w) => !STOP.has(w)).map(stem);
  const sentences = (text) => text.split(/(?<=[.!?])\s+(?!\[)|\n+/).map((s) => s.trim()).filter(Boolean);

  function inline(text, cites) {
    let out = esc(text)
      .replace(/`([^`]+)`/g, '<code>$1</code>')
      .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    out = out.replace(/\[(\d{1,3})\]/g, (match, n) => {
      if (cites === null) return `<span class="cite-pending">${n}</span>`;
      return cites.has(Number(n))
        ? `<button type="button" class="cite" data-cite="${n}" aria-label="Show source ${n}">${n}</button>`
        : match;
    });
    return out;
  }

  function renderMarkdown(text, cites) {
    let html = '';
    let para = [];
    let list = null;
    const flushPara = () => { if (para.length) { html += `<p>${inline(para.join(' '), cites)}</p>`; para = []; } };
    const flushList = () => {
      if (list) { html += `<${list.tag}>${list.items.map((i) => `<li>${inline(i, cites)}</li>`).join('')}</${list.tag}>`; list = null; }
    };
    for (const raw of text.replace(/\r/g, '').split('\n')) {
      const line = raw.trim();
      if (!line) { flushPara(); flushList(); continue; }
      const bullet = line.match(/^[-*•]\s+(.*)$/);
      const numbered = line.match(/^\d+[.)]\s+(.*)$/);
      const heading = line.match(/^#{1,4}\s+(.*)$/);
      if (bullet || numbered) {
        flushPara();
        const tag = bullet ? 'ul' : 'ol';
        if (!list || list.tag !== tag) { flushList(); list = { tag, items: [] }; }
        list.items.push((bullet || numbered)[1]);
      } else if (heading) {
        flushPara(); flushList();
        html += `<p><strong>${inline(heading[1], cites)}</strong></p>`;
      } else {
        flushList();
        para.push(line);
      }
    }
    flushPara(); flushList();
    return html;
  }

  /* The part of the answer that source n supports: the sentence carrying [n] plus any sentences
     just before it in the same paragraph or bullet that have no marker of their own. */
  function claimTokens(answer, n) {
    const claim = new Set();
    for (const line of answer.split('\n')) {
      let pending = [];
      for (const s of sentences(line)) {
        const marks = [...s.matchAll(/\[(\d{1,3})\]/g)].map((m) => Number(m[1]));
        pending.push(s);
        if (marks.length) {
          if (marks.includes(n)) pending.forEach((p) => tokens(p.replace(/\[\d+\]/g, '')).forEach((t) => claim.add(t)));
          pending = [];
        }
      }
    }
    return claim;
  }

  /* Mark the sentence(s) in a passage that best support the answer's claim for source n. */
  function passageHtml(snippet, answer, n) {
    const claim = claimTokens(answer, n);
    if (!claim.size) return esc(snippet);
    const ranked = sentences(snippet)
      .map((s) => {
        const set = new Set(tokens(s));
        let overlap = 0;
        set.forEach((t) => { if (claim.has(t)) overlap += 1; });
        return { s, overlap, score: set.size ? overlap / set.size : 0 };
      })
      .filter((p) => p.overlap >= 2 && p.score >= 0.4)
      .sort((a, b) => b.score - a.score)
      .slice(0, 2);
    const spans = ranked
      .map((p) => ({ i: snippet.indexOf(p.s), len: p.s.length }))
      .filter((x) => x.i >= 0)
      .sort((a, b) => a.i - b.i);
    let out = '';
    let pos = 0;
    for (const sp of spans) {
      if (sp.i < pos) continue;
      out += `${esc(snippet.slice(pos, sp.i))}<mark>${esc(snippet.slice(sp.i, sp.i + sp.len))}</mark>`;
      pos = sp.i + sp.len;
    }
    return out + esc(snippet.slice(pos));
  }

  const TOOL_LABELS = { search_documents: 'Searched documents', calculate: 'Calculator', document_stats: 'Document details', summarize_document: 'Summary' };
  const CONFIDENCE_LABELS = { high: 'High confidence', medium: 'Medium confidence', low: 'Low confidence' };

  function answerHtml(e) {
    if (e.status === 'error') {
      return `<div class="error-note" role="alert"><span>${esc(e.error)}</span><button type="button" data-retry="${e.uid}">Try again</button></div>`;
    }
    if (e.status === 'streaming') {
      if (!e.text.trim()) {
        return `<p class="working"><span class="spinner"></span>${esc(e.statusText || 'Reading your documents…')}</p>`;
      }
      return `<div class="prose is-streaming">${renderMarkdown(e.text, null)}</div>`;
    }

    const nums = new Set(e.citations.map((c) => c.number));
    let html = `<div class="prose">${renderMarkdown(e.answer, nums)}</div>`;

    const badges = [];
    if (!e.found) badges.push('<span class="badge is-none">Not in your documents</span>');
    else if (CONFIDENCE_LABELS[e.confidence]) badges.push(`<span class="badge is-${e.confidence}">${CONFIDENCE_LABELS[e.confidence]}</span>`);
    for (const t of e.tools) if (t !== 'search_documents' || !e.citations.length) badges.push(`<span class="tool-chip">${esc(TOOL_LABELS[t] || t)}</span>`);

    const feedback = e.messageId
      ? `<span class="feedback" role="group" aria-label="Rate this answer">` +
        `<button type="button" class="fb-btn" data-feedback="1" aria-pressed="${e.feedback === 1}">Helpful</button>` +
        `<button type="button" class="fb-btn" data-feedback="-1" aria-pressed="${e.feedback === -1}">Not helpful</button></span>`
      : '';
    html += `<div class="meta">${badges.join('')}${feedback}</div>`;

    if (e.citations.length) {
      html += '<div class="sources">' + e.citations.map((c) =>
        `<button type="button" class="source-chip" data-cite="${c.number}"><span class="cite static">${c.number}</span>` +
        `<span class="src-name" title="${esc(c.filename)}">${esc(c.filename)}</span><span class="src-page">${esc(pageLabel(c.filename, c.page))}</span></button>`
      ).join('') + '</div>';
    }
    return html;
  }

  function entryHtml(e) {
    return `<article class="entry${e.uid === state.selectedUid ? ' is-selected' : ''}" data-uid="${e.uid}">` +
      `<h3 class="q">${esc(e.question)}</h3><div class="answer">${answerHtml(e)}</div></article>`;
  }

  function welcomeHtml() {
    const ready = state.docs.filter((d) => d.status === 'ready');
    if (!ready.length) {
      return '<section class="welcome"><h2>Add a document to begin.</h2>' +
        '<p>Upload a PDF, Word file, text file or Markdown file from the panel on the left. Once it is indexed you can ask questions and see exactly where each answer comes from.</p>' +
        '<div class="suggestions"><button type="button" class="suggestion" data-action="browse">Choose files to upload</button></div></section>';
    }
    const name = ready[0].filename;
    const picks = [
      `Summarize ${name}`,
      `How many ${isPdf(name) ? 'pages' : 'sections'} does ${name} have?`,
      'What is 15% of 2,400?',
    ].filter(Boolean);
    return '<section class="welcome"><h2>Ask your documents.</h2>' +
      `<p>${ready.length === 1 ? 'Your document is' : `Your ${ready.length} documents are`} indexed. Ask a question in plain language, and open any numbered source to read the passage behind the answer.</p>` +
      `<div class="suggestions">${picks.map((p) => `<button type="button" class="suggestion" data-ask="${esc(p)}">${esc(p)}</button>`).join('')}</div></section>`;
  }

  function renderThread({ scroll } = {}) {
    el.chatTitle.textContent = state.sessionId
      ? (state.sessions.find((s) => s.id === state.sessionId) || {}).title || 'Chat'
      : 'New chat';
    el.threadInner.innerHTML = state.entries.length ? state.entries.map(entryHtml).join('') : welcomeHtml();
    if (scroll === 'bottom') el.thread.scrollTop = el.thread.scrollHeight;
  }

  function refreshEntry(entry, { follow = false } = {}) {
    const node = el.threadInner.querySelector(`.entry[data-uid="${entry.uid}"]`);
    if (!node) return renderThread();
    const wasNearBottom = el.thread.scrollHeight - el.thread.scrollTop - el.thread.clientHeight < 140;
    node.classList.toggle('is-selected', entry.uid === state.selectedUid);
    node.querySelector('.answer').innerHTML = answerHtml(entry);
    if (follow && wasNearBottom) el.thread.scrollTop = el.thread.scrollHeight;
  }

  /* ------------------------------------------------------------ rendering: evidence */
  function selectedEntry() {
    return state.entries.find((e) => e.uid === state.selectedUid) || null;
  }

  function renderEvidence(focus) {
    const e = selectedEntry();
    if (!e) {
      el.evidenceSub.textContent = 'The passages behind an answer appear here.';
      el.evidenceBody.innerHTML = '<div class="evidence-empty"><strong>Nothing selected yet</strong>Ask a question. The passages used to answer it show up here, with the sentence that supports the answer highlighted.</div>';
      return;
    }
    if (e.status === 'streaming') {
      el.evidenceSub.textContent = 'Looking for passages…';
      el.evidenceBody.innerHTML = '<div class="evidence-empty"><strong>Reading your documents</strong>Sources appear as soon as the answer is ready.</div>';
      return;
    }
    if (e.status === 'error') {
      el.evidenceSub.textContent = 'No answer to show sources for.';
      el.evidenceBody.innerHTML = '<div class="evidence-empty"><strong>That question did not finish</strong>Try asking it again.</div>';
      return;
    }
    if (!e.citations.length) {
      const usedTool = e.tools.some((t) => t !== 'search_documents');
      el.evidenceSub.textContent = truncate(`For: ${e.question}`, 70);
      el.evidenceBody.innerHTML = e.found && usedTool
        ? '<div class="evidence-empty"><strong>No passage needed</strong>This answer came from a built-in tool, such as the calculator, document details or a summary, rather than from a quoted passage.</div>'
        : '<div class="evidence-empty"><strong>Nothing in your documents matched</strong>No passage supports an answer to this question. Try rephrasing it, or add the document that covers it.</div>';
      return;
    }
    el.evidenceSub.textContent = truncate(`${e.citations.length} ${e.citations.length === 1 ? 'source' : 'sources'} for: ${e.question}`, 80);
    el.evidenceBody.innerHTML = e.citations.map((c) =>
      `<section class="passage" id="passage-${c.number}" data-n="${c.number}">` +
      `<div class="passage-head"><span class="cite static">${c.number}</span>` +
      `<span class="passage-file" title="${esc(c.filename)}">${esc(c.filename)}</span>` +
      `<span class="passage-loc">${esc(pageLabel(c.filename, c.page))}</span></div>` +
      `<p class="passage-text">${passageHtml(c.snippet, e.answer, c.number)}</p></section>`
    ).join('');
    if (focus) {
      const node = $(`#passage-${focus}`, el.evidenceBody);
      if (node) {
        node.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
        node.classList.add('is-flash');
        window.setTimeout(() => node.classList.remove('is-flash'), 900);
      }
    }
  }

  function selectEntry(uid, focus) {
    state.selectedUid = uid;
    for (const node of el.threadInner.querySelectorAll('.entry')) node.classList.toggle('is-selected', Number(node.dataset.uid) === uid);
    for (const btn of el.threadInner.querySelectorAll('button.cite')) {
      const owner = btn.closest('.entry');
      btn.classList.toggle('is-active', !!focus && owner && Number(owner.dataset.uid) === uid && Number(btn.dataset.cite) === focus);
    }
    renderEvidence(focus);
    if (focus && window.matchMedia('(max-width: 1180px)').matches) openEvidence();
  }

  /* ------------------------------------------------------------ asking */
  async function ask(question) {
    question = question.trim();
    if (!question || state.streaming) return;
    const ready = state.docs.filter((d) => d.status === 'ready');
    if (!ready.length) {
      toast('Add a document first, then ask your question.', 'error');
      return;
    }
    const entry = { uid: state.nextUid++, question, status: 'streaming', statusText: 'Reading your documents…', text: '', answer: '', citations: [], found: false, confidence: 'none', tools: [], messageId: null, feedback: null };
    state.entries.push(entry);
    state.selectedUid = entry.uid;
    state.streaming = true;
    setComposerBusy(true);
    renderThread({ scroll: 'bottom' });
    renderEvidence();

    const payload = { question };
    if (state.sessionId) payload.session_id = state.sessionId;
    const scope = [...state.selectedDocs].filter((id) => ready.some((d) => d.id === id));
    if (scope.length) payload.document_ids = scope;

    try {
      await streamChat(payload, entry);
    } catch (err) {
      entry.status = 'error';
      entry.error = err.message || 'Something went wrong. Try again.';
      refreshEntry(entry);
      renderEvidence();
    } finally {
      state.streaming = false;
      setComposerBusy(false);
      el.input.focus();
    }
  }

  async function streamChat(payload, entry) {
    let res;
    try {
      res = await fetch('/api/chat/stream', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${state.token}` },
        body: JSON.stringify(payload),
      });
    } catch (_) {
      throw new ApiError('Could not reach the server. Check your connection and try again.', 'NETWORK');
    }
    if (!res.ok) {
      const err = await toApiError(res);
      if (err.status === 401) signOut('Your session expired. Sign in again.');
      throw err;
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let finished = false;
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let cut;
      while ((cut = buffer.indexOf('\n\n')) >= 0) {
        const frame = buffer.slice(0, cut);
        buffer = buffer.slice(cut + 2);
        const line = frame.split('\n').find((l) => l.startsWith('data:'));
        if (!line) continue;
        const event = JSON.parse(line.slice(5).trim());
        if (event.type === 'error') throw new ApiError(event.message, event.code);
        if (handleEvent(event, entry)) finished = true;
      }
    }
    if (!finished) throw new ApiError('The connection closed before the answer finished. Try again.', 'INCOMPLETE');
  }

  function handleEvent(event, entry) {
    switch (event.type) {
      case 'status':
        entry.statusText = event.text;
        if (!entry.text.trim()) refreshEntry(entry);
        break;
      case 'token':
        entry.text += event.text;
        refreshEntry(entry, { follow: true });
        break;
      case 'reset':
        entry.text = '';
        refreshEntry(entry);
        break;
      case 'final':
        Object.assign(entry, {
          status: 'done', answer: event.answer, citations: event.citations || [], found: event.found,
          confidence: event.confidence, tools: event.tools_used || [], messageId: event.message_id,
        });
        state.sessionId = event.session_id;
        refreshEntry(entry, { follow: true });
        if (state.selectedUid === entry.uid) renderEvidence();
        announce(event.found ? 'Answer ready.' : 'No answer found in your documents.');
        refreshSessions().then(() => {
          const current = state.sessions.find((s) => s.id === state.sessionId);
          el.chatTitle.textContent = current ? current.title : 'Chat';
        });
        return true;
      default:
        break;
    }
    return false;
  }

  function setComposerBusy(busy) {
    el.sendBtn.disabled = busy;
    el.sendBtn.textContent = busy ? 'Working…' : 'Ask';
    el.input.setAttribute('aria-busy', String(busy));
  }

  async function sendFeedback(entry, value) {
    if (!entry.messageId || entry.feedback === value) return;
    try {
      await api(`/api/messages/${entry.messageId}/feedback`, { method: 'POST', json: { value } });
      entry.feedback = value;
      refreshEntry(entry);
    } catch (err) {
      toast(err.message, 'error');
    }
  }

  /* ------------------------------------------------------------ usage */
  async function openUsage() {
    el.usageBody.innerHTML = '<p class="note">Loading…</p>';
    if (!el.usageDialog.open) el.usageDialog.showModal();
    try {
      el.usageBody.innerHTML = usageHtml(await api('/api/usage'));
    } catch (err) {
      el.usageBody.innerHTML = `<p class="form-error">${esc(err.message)}</p>`;
    }
  }

  function usageHtml(u) {
    const demo = state.health && state.health.mode !== 'claude';
    const stats = [
      ['Questions asked', fmtNumber(u.requests)],
      ['Tokens used', fmtNumber(u.total_tokens)],
      ['Estimated cost', fmtCost(u.cost_usd)],
      ['Average response', fmtMs(u.avg_latency_ms)],
      ['Slowest 5%', fmtMs(u.p95_latency_ms)],
      ['Indexed', `${fmtNumber(u.documents)} docs, ${fmtNumber(u.chunks)} passages`],
    ].map(([k, v]) => `<div class="stat"><dt>${k}</dt><dd>${esc(v)}</dd></div>`).join('');

    const max = Math.max(1, ...u.daily.map((d) => d.requests));
    const W = 640, H = 170, top = 22, base = 132, slot = W / u.daily.length, bw = Math.min(54, slot * 0.56);
    const bars = u.daily.map((d, i) => {
      const h = Math.round((d.requests / max) * (base - top));
      const x = Math.round(i * slot + (slot - bw) / 2);
      const day = parseUtc(`${d.date}T00:00:00`).toLocaleDateString(undefined, { month: 'short', day: 'numeric', timeZone: 'UTC' });
      return `<rect class="bar" x="${x}" y="${base - h}" width="${bw}" height="${Math.max(h, d.requests ? 2 : 0)}" rx="3"></rect>` +
        `<text class="val" x="${x + bw / 2}" y="${base - h - 6}" text-anchor="middle">${d.requests}</text>` +
        `<text x="${x + bw / 2}" y="${base + 20}" text-anchor="middle">${esc(day)}</text>`;
    }).join('');
    const summary = u.daily.map((d) => `${d.date}: ${d.requests}`).join(', ');

    const rows = u.recent.length
      ? u.recent.map((r) => `<tr><td>${esc(parseUtc(r.created_at).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }))}</td><td>${esc(r.endpoint)}</td><td>${fmtNumber(r.tokens)}</td><td>${fmtCost(r.cost_usd)}</td><td>${fmtMs(r.latency_ms)}</td></tr>`).join('')
      : '<tr><td colspan="5" class="note">No questions yet.</td></tr>';

    return `<dl class="stat-grid">${stats}</dl>` +
      `<section class="chart"><h3>Questions per day, last 7 days</h3><svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Questions per day. ${esc(summary)}"><line class="axis" x1="0" y1="${base}" x2="${W}" y2="${base}"></line>${bars}</svg></section>` +
      `<section><h3>Recent questions</h3><div class="table-wrap"><table><thead><tr><th>When</th><th>Type</th><th>Tokens</th><th>Cost</th><th>Time</th></tr></thead><tbody>${rows}</tbody></table></div></section>` +
      (demo ? '<p class="note">Demo mode does not call an AI model, so tokens and cost stay at zero. Set ANTHROPIC_API_KEY to see real usage.</p>' : '<p class="note">Cost is an estimate based on the token prices in your settings.</p>');
  }

  /* ------------------------------------------------------------ drawers (small screens) */
  function openRail() { el.appView.classList.add('rail-open'); el.scrim.hidden = false; }
  function openEvidence() { el.appView.classList.add('evidence-open'); el.scrim.hidden = false; }
  function closeDrawers() {
    el.appView.classList.remove('rail-open', 'evidence-open');
    el.scrim.hidden = true;
  }

  /* ------------------------------------------------------------ events */
  const armed = new Map();
  function twoStep(button, action) {
    if (button.classList.contains('is-confirming')) {
      window.clearTimeout(armed.get(button));
      action();
      return;
    }
    const original = button.innerHTML;
    button.classList.add('is-confirming');
    button.textContent = 'Confirm';
    armed.set(button, window.setTimeout(() => {
      button.classList.remove('is-confirming');
      button.innerHTML = original;
    }, 3000));
  }

  function autosize() {
    el.input.style.height = 'auto';
    el.input.style.height = `${Math.min(el.input.scrollHeight, 180)}px`;
  }

  function bind() {
    el.authForm.addEventListener('submit', submitAuth);
    for (const tab of document.querySelectorAll('.seg')) tab.addEventListener('click', () => setAuthMode(tab.dataset.mode));

    $('#sign-out').addEventListener('click', () => signOut());
    $('#new-chat').addEventListener('click', newChat);
    $('#open-usage').addEventListener('click', openUsage);
    $('#close-usage').addEventListener('click', () => el.usageDialog.close());
    el.usageDialog.addEventListener('click', (ev) => { if (ev.target === el.usageDialog) el.usageDialog.close(); });

    $('#open-rail').addEventListener('click', openRail);
    $('#close-rail').addEventListener('click', closeDrawers);
    $('#open-evidence').addEventListener('click', () => { renderEvidence(); openEvidence(); });
    $('#close-evidence').addEventListener('click', closeDrawers);
    el.scrim.addEventListener('click', closeDrawers);
    document.addEventListener('keydown', (ev) => { if (ev.key === 'Escape') closeDrawers(); });

    el.browseBtn.addEventListener('click', () => el.fileInput.click());
    el.dropzone.addEventListener('click', () => el.fileInput.click());
    el.dropzone.addEventListener('keydown', (ev) => { if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); el.fileInput.click(); } });
    el.fileInput.addEventListener('change', () => { uploadFiles(el.fileInput.files); el.fileInput.value = ''; });
    for (const type of ['dragenter', 'dragover']) {
      el.dropzone.addEventListener(type, (ev) => { ev.preventDefault(); el.dropzone.classList.add('is-over'); });
    }
    for (const type of ['dragleave', 'drop']) {
      el.dropzone.addEventListener(type, (ev) => { ev.preventDefault(); el.dropzone.classList.remove('is-over'); });
    }
    el.dropzone.addEventListener('drop', (ev) => uploadFiles(ev.dataTransfer.files));

    el.docList.addEventListener('change', (ev) => {
      const box = ev.target.closest('input[data-doc]');
      if (!box) return;
      const id = Number(box.dataset.doc);
      if (box.checked) state.selectedDocs.add(id); else state.selectedDocs.delete(id);
      renderDocs();
    });
    el.docList.addEventListener('click', (ev) => {
      const btn = ev.target.closest('[data-remove-doc]');
      if (btn) twoStep(btn, () => removeDoc(Number(btn.dataset.removeDoc)));
    });

    el.sessionList.addEventListener('click', (ev) => {
      const open = ev.target.closest('[data-session]');
      if (open) return openSession(Number(open.dataset.session));
      const rm = ev.target.closest('[data-remove-session]');
      if (rm) twoStep(rm, () => removeSession(Number(rm.dataset.removeSession)));
      return undefined;
    });

    el.threadInner.addEventListener('click', (ev) => {
      const suggestion = ev.target.closest('[data-ask]');
      if (suggestion) return ask(suggestion.dataset.ask);
      if (ev.target.closest('[data-action="browse"]')) return el.fileInput.click();
      const retry = ev.target.closest('[data-retry]');
      if (retry) {
        const uid = Number(retry.dataset.retry);
        const failed = state.entries.find((e) => e.uid === uid);
        if (failed) {
          state.entries = state.entries.filter((e) => e.uid !== uid);
          renderThread();
          return ask(failed.question);
        }
      }
      const entryNode = ev.target.closest('.entry');
      if (!entryNode) return undefined;
      const uid = Number(entryNode.dataset.uid);
      const entry = state.entries.find((e) => e.uid === uid);
      const fb = ev.target.closest('[data-feedback]');
      if (fb && entry) return sendFeedback(entry, Number(fb.dataset.feedback));
      const cite = ev.target.closest('[data-cite]');
      if (cite) return selectEntry(uid, Number(cite.dataset.cite));
      if (entry && entry.status === 'done' && uid !== state.selectedUid) selectEntry(uid);
      return undefined;
    });

    el.composer.addEventListener('submit', (ev) => {
      ev.preventDefault();
      const q = el.input.value;
      if (!q.trim()) return;
      el.input.value = '';
      autosize();
      ask(q);
    });
    el.input.addEventListener('input', autosize);
    el.input.addEventListener('keydown', (ev) => {
      if (ev.key === 'Enter' && !ev.shiftKey && !ev.isComposing) { ev.preventDefault(); el.composer.requestSubmit(); }
    });
  }

  /* ------------------------------------------------------------ start */
  async function start() {
    bind();
    setAuthMode('login');
    const saved = store(TOKEN_KEY);
    if (saved) {
      state.token = saved;
      try {
        state.user = await api('/api/auth/me');
        await enterApp();
        return;
      } catch (_) {
        state.token = null;
        store(TOKEN_KEY, null);
      }
    }
    el.authView.hidden = false;
    el.authEmail.focus();
  }

  start();
})();
