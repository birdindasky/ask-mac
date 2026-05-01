/* Multi-LLM Chat — Alpine app */

function appData() {
  return {
    // ---- state ----
    theme: 'dark',           // user choice: 'dark' | 'light' | 'system'
    effectiveTheme: 'dark',  // resolved value applied to data-theme
    locale: 'system',        // user choice: 'system' | 'zh' | 'en'
    effectiveLocale: 'zh',   // resolved value used by t()
    fontSize: 16,            // base px, 13–22
    settingsTab: 'providers', // 'providers' | 'behavior' | 'data'
    showCheatsheet: false,
    welcomeDone: false,
    welcomeStep: 1,
    showWelcomeForce: false,
    autostart: false,        // login-item state, mirrors /api/admin/autostart
    errorDetail: { open: false, title: '', detail: '' },
    attachments: [],         // [{kind:'image'|'text', name, mime, size, data, text}]
    composerDragging: false,
    dataOps: { exporting: false, importing: false, wiping: false },
    mode: 'chat',
    get modes() {
      return [
        { id: 'chat', label: this.t('header.mode.chat', '单聊') },
        { id: 'compare', label: this.t('header.mode.compare', '对比') },
        { id: 'debate', label: this.t('header.mode.debate', '辩论') },
        { id: 'discuss', label: this.t('header.mode.discuss', '讨论') },
      ];
    },
    sessions: [],
    sessionQuery: '',
    currentSession: null,
    messages: [],
    providers: [],
    templates: [],
    categoryLabels: {},
    flatModelOptions: [],

    chatPick: { providerId: null, modelId: null, composite: '' },
    pairPick: { aProviderId: null, aModelId: null, aComposite: '', bProviderId: null, bModelId: null, bComposite: '' },
    debate: { subMode: 'lead_critic', rounds: 2, stanceA: '', stanceB: '' },
    discuss: { maxRounds: 1 },

    webSearch: {
      active: 'tavily',
      default_on: false,
      max_results: 5,
      depth: 'basic',
      providers: [],
      formKey: '',
      testing: false,
      testResult: null,
    },
    webSearchOn: false,

    draft: '',
    streaming: false,
    streamCtrl: null,

    showSettings: false,
    showProviderForm: false,
    providerForm: emptyProviderForm(),

    confirmModal: { show: false, title: '', body: '', onYes: null },
    toasts: [],

    budget: { used_tokens: 0, max_tokens: 0, pct: 0, warn: false, soft_warn: false, model_id: '' },
    summarizing: false,
    searchOverlay: { open: false, q: '', results: [], loading: false },

    // ---- init ----
    async init() {
      try { hljs.configure({ ignoreUnescapedHTML: true }); } catch (e) {}
      this.applyMarkdown();
      await Promise.all([this.loadPrefs(), this.loadProviders(), this.loadSessions(), this.loadTemplates(), this.loadWebSearch(), this.loadAutostart()]);
      this.webSearchOn = this.webSearch.default_on && !!this.activeBackendDescriptor()?.configured;
      this.applyTheme();
      this.applyFontSize();
      this._wireSystemTheme();
      // Silently mark welcome as done if the user already has providers but
      // the flag is still false (e.g. upgrading from a pre-wizard build).
      await this.maybeAutoFinishWelcome();
      // Try to restore last session
      const lastId = this._lastSessionIdHint;
      if (lastId) {
        const exists = this.sessions.find(s => s.id === lastId);
        if (exists) await this.selectSession(lastId);
      }
      this._wireMacBridge();
    },

    // ---- mac bridge ----
    // mac_launcher.py dispatches CustomEvents on window when the user
    // picks menu items / status-bar items. We intercept them here so
    // ⌘+N etc work both in dev (browser) and in the packaged .app.
    _wireMacBridge() {
      window.addEventListener('ask:new-session', () => this.newSession());
      window.addEventListener('ask:focus-composer', () => {
        const el = document.querySelector('textarea[data-composer]');
        if (el) el.focus();
      });
      window.addEventListener('ask:open-search', () => this.openSearchOverlay());
      // ⌘+F opens the overlay (works in dev browser; .app routes via menu).
      // ⌘+1..4 switch modes. We stay narrow on key matching so we don't
      // hijack the textarea's normal typing.
      // Capture phase + IME guard: Chinese IMEs (Pinyin/Wubi) emit keydown
      // with isComposing===true / keyCode===229 while composing a character.
      // Without this guard, ⌘+/ etc fire mid-composition and never reach us
      // cleanly, OR our handler eats the keystroke that the IME needed.
      document.addEventListener('keydown', (e) => {
        // IME early-out: skip while user is composing a CJK character.
        if (e.isComposing || e.keyCode === 229 || e.which === 229) return;
        if (e.metaKey && e.key === 'f') {
          e.preventDefault();
          this.openSearchOverlay();
        } else if (e.key === 'Escape' && this.searchOverlay.open) {
          this.closeSearchOverlay();
        } else if (e.metaKey && !e.shiftKey && !e.altKey && ['1', '2', '3', '4'].includes(e.key)) {
          e.preventDefault();
          const map = { '1': 'chat', '2': 'compare', '3': 'debate', '4': 'discuss' };
          this.setMode(map[e.key]);
        } else if (e.metaKey && (e.key === '=' || e.key === '+')) {
          // ⌘+= / ⌘++ — bump font size
          e.preventDefault(); this.bumpFont(1);
        } else if (e.metaKey && e.key === '-') {
          e.preventDefault(); this.bumpFont(-1);
        } else if (e.metaKey && e.key === '0') {
          e.preventDefault(); this.resetFont();
        } else if ((e.metaKey || e.ctrlKey) && e.key === '/') {
          // ⌘+/ shows the keyboard shortcut cheatsheet (⌘+? on some layouts).
          e.preventDefault(); this.showCheatsheet = true;
        } else if (e.key === '?' && (e.shiftKey) && (e.metaKey || e.ctrlKey)) {
          e.preventDefault(); this.showCheatsheet = true;
        } else if (e.key === 'Escape') {
          if (this.showCheatsheet) this.showCheatsheet = false;
          else if (this.errorDetail.open) this.errorDetail.open = false;
        }
      }, { capture: true });
      window.addEventListener('ask:open-settings', () => this.openSettings());
      window.addEventListener('ask:set-mode', (e) => {
        const m = e.detail && e.detail.mode;
        if (m && this.modes.find(x => x.id === m)) this.setMode(m);
      });
    },

    applyMarkdown() {
      if (window.marked) {
        marked.setOptions({
          breaks: true,
          gfm: true,
          highlight: (code, lang) => {
            try {
              if (lang && hljs.getLanguage(lang)) {
                return hljs.highlight(code, { language: lang }).value;
              }
              return hljs.highlightAuto(code).value;
            } catch (e) { return code; }
          }
        });
      }
    },

    renderMd(text, sources) {
      if (!text) return '';
      let html;
      try {
        html = window.marked ? marked.parse(text) : escapeHtml(text);
      } catch (e) {
        return escapeHtml(text);
      }
      // [n] → clickable citation pill linking to source.url
      if (sources && sources.length) {
        html = html.replace(/\[(\d+)\]/g, (m, n) => {
          const i = parseInt(n, 10) - 1;
          const s = sources[i];
          if (!s || !s.url) return m;
          const titleAttr = (s.title || s.url || '').replace(/"/g, '&quot;');
          const url = s.url.replace(/"/g, '&quot;');
          return `<a href="${url}" target="_blank" rel="noopener" class="citation-pill" title="${titleAttr}">${n}</a>`;
        });
      }
      // [推] → faint inference badge
      const guessTitle = (this.t('render.guessPill.title', '模型推测,非检索来源') || '').replace(/"/g, '&quot;');
      html = html.replace(/\[\s*推\s*\]/g, `<span class="guess-pill" title="${guessTitle}">推</span>`);
      return html;
    },

    hostFromUrl(url) {
      if (!url) return '';
      try { return new URL(url).host; } catch (e) { return ''; }
    },

    // ---- prefs ----
    async loadPrefs() {
      const r = await fetch('/api/ui-prefs').then(r => r.json()).catch(() => ({}));
      this.theme = r.theme || 'dark';
      this.locale = r.locale || 'system';
      this.fontSize = Math.max(13, Math.min(22, r.font_size || 16));
      this.mode = r.last_mode || 'chat';
      this._lastSessionIdHint = r.last_session_id;
      this.welcomeDone = !!r.welcome_done;
      this.applyLocale();
    },

    // ---- i18n ----
    // t(key, fallback) — looks up key under effectiveLocale; falls back to
    // the second arg (usually the original Chinese), then the key itself.
    // Templates use {name} placeholders — call tFmt for substitution.
    t(key, fallback) {
      const dict = (window.I18N && window.I18N[this.effectiveLocale]) || {};
      if (Object.prototype.hasOwnProperty.call(dict, key)) return dict[key];
      return fallback != null ? fallback : key;
    },
    tFmt(key, fallback, vars) {
      let s = this.t(key, fallback);
      if (vars) {
        for (const k of Object.keys(vars)) {
          s = s.split(`{${k}}`).join(String(vars[k]));
        }
      }
      return s;
    },
    applyLocale() {
      let eff = this.locale;
      if (eff === 'system') {
        const nav = (navigator.language || 'zh').toLowerCase();
        eff = nav.startsWith('zh') ? 'zh' : 'en';
      }
      this.effectiveLocale = (eff === 'en' || eff === 'zh') ? eff : 'zh';
      document.documentElement.setAttribute('lang', this.effectiveLocale === 'zh' ? 'zh-CN' : 'en');
    },
    async setLocale(loc) {
      if (!['system', 'zh', 'en'].includes(loc)) return;
      this.locale = loc;
      this.applyLocale();
      await fetch('/api/ui-prefs', { method: 'PUT', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ locale: loc }) });
    },

    // ---- autostart (login item) ----
    async loadAutostart() {
      try {
        const r = await fetch('/api/admin/autostart').then(r => r.ok ? r.json() : null);
        if (r) this.autostart = !!r.enabled;
      } catch (e) {}
    },
    async setAutostart(enabled) {
      try {
        const r = await fetch('/api/admin/autostart', {
          method: 'PUT', headers: { 'content-type': 'application/json' },
          body: JSON.stringify({ enabled: !!enabled })
        }).then(r => r.json());
        this.autostart = !!r.enabled;
        this.toast(this.autostart ? this.t('toast.autostart.on', '✓ 开机自启已开') : this.t('toast.autostart.off', '已关闭开机自启'), 'info');
      } catch (e) {
        this.toast(this.t('toast.autostart.fail', '设置开机自启失败'), 'error', String(e));
      }
    },

    // ---- background bridge: dock badge + system notification ----
    // Fire-and-forget: endpoint returns immediately and the AppKit side
    // hops to NSOperationQueue.mainQueue to set the dock tile / post the
    // notification. Wrap in try/catch so a 404 in dev doesn't surface.
    _dockBadge(busy) {
      try {
        fetch('/api/internal/dock-badge', {
          method: 'POST', headers: { 'content-type': 'application/json' },
          body: JSON.stringify({ busy: !!busy })
        }).catch(() => {});
      } catch (e) {}
    },
    _maybeNotifyStreamDone() {
      // Only notify when the user can't see the window — they're on
      // another desktop, the app is hidden to tray, or the tab is
      // backgrounded. Otherwise it's just noise.
      try {
        if (document.visibilityState === 'visible' && document.hasFocus()) return;
        const last = this.messages[this.messages.length - 1];
        const speaker = last?.speaker || last?.meta?.label || '';
        const title = this.t('notify.stream.done.title', 'Ask · 模型已回复');
        const body = (speaker ? `${speaker} · ` : '') + (last?.content || '').slice(0, 80);
        fetch('/api/internal/notify', {
          method: 'POST', headers: { 'content-type': 'application/json' },
          body: JSON.stringify({ title, body })
        }).catch(() => {});
      } catch (e) {}
    },
    // theme can be 'dark' | 'light' | 'system'. When 'system', we read the
    // OS preference via matchMedia and re-resolve on change. mac_launcher.py
    // also dispatches 'ask:appearance' on AppleInterfaceThemeChangedNotification
    // so .app users get instant updates without polling.
    applyTheme() {
      let eff = this.theme;
      if (eff === 'system') {
        eff = (window.matchMedia('(prefers-color-scheme: dark)').matches) ? 'dark' : 'light';
      }
      this.effectiveTheme = eff;
      document.documentElement.setAttribute('data-theme', eff);
      // Toggle highlight.js stylesheets so code blocks stay readable.
      // Tolerate missing nodes (e.g. test fixtures) — the disabled prop is
      // a no-op on null and we don't want a broken theme to crash init.
      try {
        const dark = document.getElementById('hljs-dark');
        const light = document.getElementById('hljs-light');
        if (dark) dark.disabled = (eff !== 'dark');
        if (light) light.disabled = (eff !== 'light');
      } catch (e) {}
      // Re-paint existing rendered code blocks. If nothing's been rendered
      // yet (initial load before any markdown), this is a cheap no-op.
      this.rehighlightAll();
    },
    rehighlightAll() {
      try {
        if (!window.hljs) return;
        document.querySelectorAll('pre code').forEach(b => {
          // hljs marks elements with data-highlighted="yes" after first pass
          // and refuses to re-run unless we clear it.
          b.removeAttribute('data-highlighted');
          try { hljs.highlightElement(b); } catch (e) {}
        });
      } catch (e) {}
    },
    _wireSystemTheme() {
      try {
        const mq = window.matchMedia('(prefers-color-scheme: dark)');
        const handler = () => { if (this.theme === 'system') this.applyTheme(); };
        if (mq.addEventListener) mq.addEventListener('change', handler);
        else if (mq.addListener) mq.addListener(handler);
      } catch (e) {}
      window.addEventListener('ask:appearance', () => {
        if (this.theme === 'system') this.applyTheme();
      });
    },
    async setTheme(t) {
      if (!['dark', 'light', 'system'].includes(t)) return;
      this.theme = t;
      this.applyTheme();
      await fetch('/api/ui-prefs', { method: 'PUT', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ theme: t }) });
    },
    async toggleTheme() {
      // Cycle dark → light → system → dark so the header button can flip
      // through all three states.
      const next = ({ dark: 'light', light: 'system', system: 'dark' })[this.theme] || 'dark';
      await this.setTheme(next);
    },
    themeIcon() {
      return ({ dark: '☀️', light: '🌙', system: '🖥️' })[this.theme] || '☀️';
    },
    themeLabel() {
      const map = {
        dark: this.t('header.theme.dark', '深色'),
        light: this.t('header.theme.light', '浅色'),
        system: this.t('header.theme.system', '跟随系统'),
      };
      return map[this.theme] || this.theme;
    },

    // ---- font size ----
    applyFontSize() {
      document.documentElement.style.setProperty('--ask-font-size', `${this.fontSize}px`);
    },
    async setFontSize(px) {
      this.fontSize = Math.max(13, Math.min(22, Math.round(px)));
      this.applyFontSize();
      await fetch('/api/ui-prefs', { method: 'PUT', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ font_size: this.fontSize }) });
    },
    bumpFont(delta) { this.setFontSize(this.fontSize + delta); },
    resetFont() { this.setFontSize(16); },

    async setMode(m) {
      this.mode = m;
      await fetch('/api/ui-prefs', { method: 'PUT', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ last_mode: m }) });
    },

    // ---- providers ----
    async loadProviders() {
      const r = await fetch('/api/providers').then(r => r.json());
      this.providers = r.providers || [];
      this.recomputeFlatModels();
    },
    recomputeFlatModels() {
      const opts = [];
      for (const p of this.providers) {
        if (!p.enabled) continue;
        for (const m of (p.models || [])) {
          opts.push({ value: `${p.id}::${m}`, label: `${p.name} · ${m}`, providerId: p.id, modelId: m });
        }
      }
      this.flatModelOptions = opts;
      // Auto-select if a single option exists and nothing chosen
      if (this.flatModelOptions.length > 0) {
        if (this.mode === 'chat' && !this.chatPick.providerId) {
          const first = this.flatModelOptions[0];
          this.chatPick = { providerId: first.providerId, modelId: first.modelId, composite: first.value };
        }
        if (this.mode !== 'chat') {
          if (!this.pairPick.aProviderId) {
            const first = this.flatModelOptions[0];
            this.pairPick.aProviderId = first.providerId;
            this.pairPick.aModelId = first.modelId;
            this.pairPick.aComposite = first.value;
          }
          if (!this.pairPick.bProviderId) {
            const second = this.flatModelOptions[1] || this.flatModelOptions[0];
            this.pairPick.bProviderId = second.providerId;
            this.pairPick.bModelId = second.modelId;
            this.pairPick.bComposite = second.value;
          }
        }
      }
    },
    onChatPickerChange() {
      const opt = this.flatModelOptions.find(o => o.value === this.chatPick.composite);
      if (opt) { this.chatPick.providerId = opt.providerId; this.chatPick.modelId = opt.modelId; }
    },
    onPairPickerChange(side) {
      const composite = side === 'a' ? this.pairPick.aComposite : this.pairPick.bComposite;
      const opt = this.flatModelOptions.find(o => o.value === composite);
      if (!opt) return;
      if (side === 'a') { this.pairPick.aProviderId = opt.providerId; this.pairPick.aModelId = opt.modelId; }
      else { this.pairPick.bProviderId = opt.providerId; this.pairPick.bModelId = opt.modelId; }
    },

    async loadTemplates() {
      const r = await fetch('/api/providers/templates').then(r => r.json());
      this.templates = r.templates || [];
      this.categoryLabels = r.category_labels || {};
    },

    async loadWebSearch() {
      try {
        const r = await fetch('/api/web-search').then(r => r.json());
        this.webSearch = {
          ...this.webSearch,
          active: r.active || 'tavily',
          default_on: !!r.default_on,
          max_results: r.max_results || 5,
          depth: r.depth || 'basic',
          providers: r.providers || [],
          formKey: '',
          testResult: null,
        };
      } catch (e) {}
    },
    activeBackendDescriptor() {
      return (this.webSearch.providers || []).find(b => b.name === this.webSearch.active);
    },
    async saveWebSearch(patch) {
      const r = await fetch('/api/web-search', {
        method: 'PUT', headers: { 'content-type': 'application/json' },
        body: JSON.stringify(patch)
      }).then(r => r.json());
      this.webSearch = { ...this.webSearch, ...r };
    },
    async saveWebSearchKey() {
      const key = (this.webSearch.formKey || '').trim();
      if (!key) {
        this.toast(this.t('toast.webKeyEmpty', '请先输入 key'), 'warn');
        return;
      }
      const name = this.webSearch.active;
      const r = await fetch(`/api/web-search/keys/${name}`, {
        method: 'PUT', headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ api_key: key })
      }).then(r => r.json());
      this.webSearch = { ...this.webSearch, ...r };
      this.webSearch.formKey = '';
      const label = this.activeBackendDescriptor()?.label || name;
      this.toast(this.tFmt('toast.webKeySavedTpl', `✓ ${label} key 已保存`, { label }), 'info');
    },
    async clearActiveBackendKey() {
      const name = this.webSearch.active;
      const r = await fetch(`/api/web-search/keys/${name}`, { method: 'DELETE' }).then(r => r.json());
      this.webSearch = { ...this.webSearch, ...r };
      // If user just nuked the key for the currently-on backend, turn the toggle off.
      if (!this.activeBackendDescriptor()?.configured) this.webSearchOn = false;
      this.toast(this.tFmt('toast.webKeyClearedTpl', `已清空 ${name} 的 key`, { name }), 'info');
    },
    async testWebSearch() {
      this.webSearch.testing = true;
      this.webSearch.testResult = null;
      try {
        const body = { name: this.webSearch.active };
        if (this.webSearch.formKey) body.api_key = this.webSearch.formKey;
        const r = await fetch('/api/web-search/test', {
          method: 'POST', headers: { 'content-type': 'application/json' },
          body: JSON.stringify(body)
        }).then(r => r.json());
        this.webSearch.testResult = r;
      } catch (e) {
        this.webSearch.testResult = { ok: false, message: this.tFmt('toast.testFailedTpl', '测试失败: ' + e, { err: String(e) }) };
      } finally {
        this.webSearch.testing = false;
      }
    },
    toggleWebSearch() {
      const desc = this.activeBackendDescriptor();
      if (!desc?.configured && !this.webSearchOn) {
        const label = desc?.label || this.t('settings.web.activeBackend', '当前后端');
        this.toast(this.tFmt('toast.webNeedKeyTpl', `请先在 ⚙️ 设置里给 ${label} 填 key,或换一家已配置的`, { label }), 'warn');
        this.openSettings();
        return;
      }
      this.webSearchOn = !this.webSearchOn;
      this.toast(this.webSearchOn ? this.tFmt('toast.webOnTpl', `🌐 联网开 (${desc.label})`, { label: desc.label }) : this.t('toast.webOff', '联网模式关'), 'info');
    },
    get templatesByCategory() {
      const groups = {};
      for (const t of this.templates) {
        (groups[t.category] = groups[t.category] || []).push(t);
      }
      return groups;
    },

    // ---- sessions ----
    async loadSessions() {
      const url = this.sessionQuery ? `/api/sessions?q=${encodeURIComponent(this.sessionQuery)}` : '/api/sessions';
      const r = await fetch(url).then(r => r.json());
      this.sessions = r.sessions || [];
    },
    async newSession() {
      const r = await fetch('/api/sessions', {
        method: 'POST', headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ title: this.t('session.defaultTitle', '新会话'), mode: this.mode })
      }).then(r => r.json());
      await this.loadSessions();
      await this.selectSession(r.session.id);
    },
    async selectSession(sid) {
      const r = await fetch(`/api/sessions/${sid}`).then(r => r.json());
      this.currentSession = r.session;
      this.messages = r.messages || [];
      if (r.session && r.session.mode) this.mode = r.session.mode;
      await fetch('/api/ui-prefs', {
        method: 'PUT', headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ last_session_id: sid })
      });
      this.scrollToBottom();
      this.refreshBudget();
    },

    // ---- token budget ----
    pickedModelId() {
      if (this.mode === 'chat' && this.chatPick.modelId) return this.chatPick.modelId;
      if (this.mode === 'compare' && this.pairPick.aModelId) return this.pairPick.aModelId;
      // debate mode falls back to whatever was last assistant-stamped on the session
      const last = [...this.messages].reverse().find(m => m.model_id);
      return last ? last.model_id : '';
    },
    async refreshBudget() {
      if (!this.currentSession) {
        this.budget = { used_tokens: 0, max_tokens: 0, pct: 0, warn: false, soft_warn: false, model_id: '' };
        return;
      }
      const mid = this.pickedModelId();
      const url = `/api/sessions/${this.currentSession.id}/budget` + (mid ? `?model_id=${encodeURIComponent(mid)}` : '');
      try {
        const r = await fetch(url).then(r => r.ok ? r.json() : null);
        if (r) this.budget = r;
      } catch (e) {}
    },
    budgetLabel() {
      if (!this.budget.max_tokens) return '';
      const used = this.budget.used_tokens || 0;
      const max = this.budget.max_tokens;
      const fmt = (n) => n >= 1000 ? `${(n / 1000).toFixed(1)}k` : `${n}`;
      return `${fmt(used)} / ${fmt(max)} · ${this.budget.pct}%`;
    },
    budgetClass() {
      if (this.budget.warn) return 'progress-error';
      if (this.budget.soft_warn) return 'progress-warning';
      return 'progress-primary';
    },
    async runSummarize() {
      if (!this.currentSession || this.summarizing) return;
      const mid = this.pickedModelId();
      const pid = this.chatPick.providerId || (this.providers[0]?.id);
      if (!mid || !pid) { this.toast(this.t('toast.pickOneModel', '请先选一个模型'), 'error'); return; }
      this.summarizing = true;
      try {
        const r = await fetch(`/api/sessions/${this.currentSession.id}/summarize`, {
          method: 'POST', headers: { 'content-type': 'application/json' },
          body: JSON.stringify({ provider_id: pid, model_id: mid })
        });
        if (!r.ok) throw new Error((await r.json()).detail || this.t('toast.summarizeFailedDefault', '摘要失败'));
        const body = await r.json();
        this.messages = body.messages || [];
        this.toast(this.tFmt('toast.summarizedTpl', `✓ 历史已压缩(${body.compacted_count} 条 → 摘要)`, { n: body.compacted_count }), 'info');
        await this.refreshBudget();
      } catch (e) {
        this.toast(this.tFmt('toast.summarizeFailedTpl', `摘要失败: ${e.message}`, { err: e.message }), 'error');
      } finally {
        this.summarizing = false;
      }
    },

    // ---- bubble actions ----
    async copyText(text) {
      try {
        await navigator.clipboard.writeText(text || '');
        this.toast(this.t('toast.copied', '✓ 已复制'), 'info');
      } catch (e) {
        this.toast(this.t('toast.copyFailed', '复制失败'), 'error');
      }
    },

    // ---- regenerate ----
    async regenerateLast(opts = {}) {
      if (!this.currentSession || this.streaming) return;
      // Optimistically pop the trailing assistant + the user message so the
      // re-streamed turn lands cleanly. Backend does the same on its side.
      while (this.messages.length && this.messages[this.messages.length - 1].role === 'assistant') {
        this.messages.pop();
      }
      if (this.messages.length && this.messages[this.messages.length - 1].role === 'user') {
        this.messages.pop();
      }
      this.streaming = true;
      this._dockBadge(true);
      try {
        await this.streamSSE(
          `/api/sessions/${this.currentSession.id}/regenerate`,
          {
            provider_id: opts.providerId || null,
            model_id: opts.modelId || null,
            web_search: !!this.webSearchOn,
          },
          this.currentSession.id,
        );
      } catch (e) {
        this.toast(this.tFmt('toast.regenFailedTpl', `重新生成失败: ${e.message}`, { err: e.message }), 'error');
      } finally {
        this.streaming = false;
        this.streamCtrl = null;
        this._dockBadge(false);
        this._maybeNotifyStreamDone();
        this.refreshBudget();
      }
    },

    // ---- discuss mode: continue / finalize at checkpoint ----
    async continueDiscuss() {
      if (!this.currentSession || this.streaming) return;
      if (!this.pairPick.aProviderId || !this.pairPick.bProviderId) {
        this.toast(this.t('toast.pickAB', 'A 方和 B 方都要选好模型'), 'error');
        return;
      }
      this.streaming = true;
      this._dockBadge(true);
      try {
        await this.streamSSE(
          `/api/sessions/${this.currentSession.id}/discuss/continue`,
          {
            extra_rounds: this.discuss.maxRounds,
            web_search: !!this.webSearchOn,
            side_a: { provider_id: this.pairPick.aProviderId, model_id: this.pairPick.aModelId, label: this.t('picker.sideA', 'A 方') },
            side_b: { provider_id: this.pairPick.bProviderId, model_id: this.pairPick.bModelId, label: this.t('picker.sideB', 'B 方') },
          },
          this.currentSession.id,
        );
      } catch (e) {
        this.toast(this.tFmt('toast.continueFailedTpl', `继续讨论失败: ${e.message}`, { err: e.message }), 'error');
      } finally {
        this.streaming = false;
        this.streamCtrl = null;
        this._dockBadge(false);
        this._maybeNotifyStreamDone();
        this.refreshBudget();
      }
    },
    async finalizeDiscuss() {
      if (!this.currentSession || this.streaming) return;
      if (!this.pairPick.aProviderId) {
        this.toast(this.t('toast.aMissing', 'A 方模型未选,无法写共识'), 'error');
        return;
      }
      this.streaming = true;
      this._dockBadge(true);
      try {
        await this.streamSSE(
          `/api/sessions/${this.currentSession.id}/discuss/finalize`,
          {
            web_search: !!this.webSearchOn,
            side_a: { provider_id: this.pairPick.aProviderId, model_id: this.pairPick.aModelId, label: this.t('picker.sideA', 'A 方') },
          },
          this.currentSession.id,
        );
      } catch (e) {
        this.toast(this.tFmt('toast.finalizeFailedTpl', `收尾共识失败: ${e.message}`, { err: e.message }), 'error');
      } finally {
        this.streaming = false;
        this.streamCtrl = null;
        this._dockBadge(false);
        this._maybeNotifyStreamDone();
        this.refreshBudget();
      }
    },

    // ---- global search overlay ----
    openSearchOverlay() {
      this.searchOverlay.open = true;
      this.searchOverlay.q = '';
      this.searchOverlay.results = [];
      this.$nextTick(() => {
        const el = document.querySelector('input[data-search-overlay]');
        if (el) el.focus();
      });
    },
    closeSearchOverlay() {
      this.searchOverlay.open = false;
    },
    async runSearchOverlay() {
      const q = this.searchOverlay.q.trim();
      if (!q) { this.searchOverlay.results = []; return; }
      this.searchOverlay.loading = true;
      try {
        const r = await fetch(`/api/sessions/search/messages?q=${encodeURIComponent(q)}`).then(r => r.json());
        this.searchOverlay.results = r.results || [];
      } catch (e) {
        this.toast(this.tFmt('toast.searchFailedTpl', `搜索失败: ${e.message}`, { err: e.message }), 'error');
      } finally {
        this.searchOverlay.loading = false;
      }
    },
    async jumpToSearchHit(hit) {
      this.closeSearchOverlay();
      await this.selectSession(hit.session_id);
      this.$nextTick(() => {
        const el = document.querySelector(`[data-message-id="${hit.message_id}"]`);
        if (el) el.scrollIntoView({ behavior: 'smooth', block: 'center' });
      });
    },

    confirmDelete(s) {
      this.confirmModal = {
        show: true, title: this.t('confirm.session.title', '确认删除会话?'),
        body: this.tFmt('confirm.session.bodyTpl', `“${s.title}” 会被永久删除,无法恢复。`, { title: s.title }),
        onYes: async () => {
          await fetch(`/api/sessions/${s.id}`, { method: 'DELETE' });
          if (this.currentSession && this.currentSession.id === s.id) {
            this.currentSession = null;
            this.messages = [];
          }
          await this.loadSessions();
          this.toast(this.t('toast.sessionDeleted', '会话已删除'), 'info');
        }
      };
    },

    // ---- composer attachments ----
    // Image: read as data URL → b64; text-like: read as plain text. Backend
    // already accepts {kind, name, mime, size, data, text} on ChatBody.
    async _readFile(file) {
      const isImage = (file.type || '').startsWith('image/');
      const isText = !isImage && (
        (file.type || '').startsWith('text/') ||
        /\.(md|txt|json|csv|tsv|log|py|js|ts|tsx|jsx|sh|yaml|yml|toml|ini|cfg|html|css|sql)$/i.test(file.name || '')
      );
      if (isImage) {
        return new Promise((resolve, reject) => {
          const fr = new FileReader();
          fr.onerror = () => reject(fr.error);
          fr.onload = () => {
            const m = String(fr.result).match(/^data:([^;]+);base64,(.*)$/);
            const data = m ? m[2] : '';
            const mime = m ? m[1] : (file.type || 'image/png');
            resolve({ kind: 'image', name: file.name || 'image', mime, size: file.size, data });
          };
          fr.readAsDataURL(file);
        });
      }
      if (isText) {
        return new Promise((resolve, reject) => {
          const fr = new FileReader();
          fr.onerror = () => reject(fr.error);
          fr.onload = () => {
            resolve({ kind: 'text', name: file.name || 'file.txt', mime: file.type || 'text/plain', size: file.size, text: String(fr.result || '').slice(0, 200000) });
          };
          fr.readAsText(file);
        });
      }
      return null; // unsupported binary
    },
    async pickAttachments(files) {
      if (!files || !files.length) return;
      let added = 0, skipped = 0;
      for (const f of Array.from(files)) {
        if (f.size > 8 * 1024 * 1024) { this.toast(this.tFmt('toast.tooBigTpl', `${f.name} 超过 8MB,跳过`, { name: f.name }), 'warn'); skipped++; continue; }
        try {
          const att = await this._readFile(f);
          if (att) { this.attachments.push(att); added++; }
          else skipped++;
        } catch (e) { skipped++; }
      }
      if (added) this.toast(this.tFmt('toast.attachedTpl', `已附加 ${added} 个文件`, { n: added }), 'info');
      if (skipped) this.toast(this.tFmt('toast.skippedTpl', `${skipped} 个文件不支持`, { n: skipped }), 'warn');
    },
    removeAttachment(i) { this.attachments.splice(i, 1); },
    async onComposerPaste(e) {
      const items = e.clipboardData?.items || [];
      const files = [];
      for (const it of items) {
        if (it.kind === 'file') {
          const f = it.getAsFile();
          if (f) files.push(f);
        }
      }
      if (files.length) {
        e.preventDefault();
        await this.pickAttachments(files);
      }
    },
    async onComposerDrop(e) {
      e.preventDefault();
      this.composerDragging = false;
      const fl = e.dataTransfer?.files;
      if (fl && fl.length) await this.pickAttachments(fl);
    },
    onComposerDragOver(e) { e.preventDefault(); this.composerDragging = true; },
    onComposerDragLeave(e) { this.composerDragging = false; },
    attachmentLabel(att) {
      const k = att.size || (att.text ? att.text.length : (att.data ? att.data.length : 0));
      const sz = k > 1024 ? `${Math.round(k / 1024)}KB` : `${k}B`;
      return `${att.kind === 'image' ? '🖼️' : '📄'} ${att.name} · ${sz}`;
    },

    // ---- data ops (export/import/wipe) ----
    async exportConfig() {
      this.dataOps.exporting = true;
      try {
        const r = await fetch('/api/admin/export/config').then(r => r.json());
        this._download(`ask-config-${this._stamp()}.json`, JSON.stringify(r, null, 2));
        this.toast(this.t('toast.configExported', '✓ 配置已导出'), 'info');
      } catch (e) { const err = e.message || String(e); this.toast(this.tFmt('toast.exportFailedTpl', `导出失败: ${err}`, { err }), 'error', String(e)); }
      finally { this.dataOps.exporting = false; }
    },
    async exportSessions() {
      this.dataOps.exporting = true;
      try {
        const r = await fetch('/api/admin/export/sessions').then(r => r.json());
        this._download(`ask-sessions-${this._stamp()}.json`, JSON.stringify(r, null, 2));
        const n = (r.sessions || []).length;
        this.toast(this.tFmt('toast.sessionsExportedTpl', `✓ 已导出 ${n} 个会话`, { n }), 'info');
      } catch (e) { const err = e.message || String(e); this.toast(this.tFmt('toast.exportFailedTpl', `导出失败: ${err}`, { err }), 'error', String(e)); }
      finally { this.dataOps.exporting = false; }
    },
    async importConfig(file) {
      if (!file) return;
      this.dataOps.importing = true;
      try {
        const text = await file.text();
        const obj = JSON.parse(text);
        const cfg = obj.config || obj;
        const r = await fetch('/api/admin/import/config', {
          method: 'POST', headers: { 'content-type': 'application/json' },
          body: JSON.stringify({ config: cfg })
        });
        if (!r.ok) throw new Error((await r.text()).slice(0, 200));
        await Promise.all([this.loadProviders(), this.loadWebSearch(), this.loadPrefs()]);
        this.applyTheme();
        this.applyFontSize();
        // Keys live in macOS Keychain, not in the exported JSON. After
        // import, the user has to re-fill each provider's key. Surface this
        // by jumping to the first one that needs a key and popping its edit
        // form pre-targeted.
        const KEY_KINDS = ['anthropic_api', 'openai_api', 'gemini_api', 'openai_compat'];
        const needsKey = (this.providers || []).filter(p => KEY_KINDS.includes(p.kind));
        if (!this.providers.length) {
          this.toast(this.t('toast.configImported', '✓ 配置已导入'), 'info');
        } else if (needsKey.length === 0) {
          this.toast(this.t('toast.configImported', '✓ 配置已导入'), 'info');
        } else {
          const first = needsKey[0];
          this.openSettings();
          this.settingsTab = 'providers';
          this.toast(this.tFmt('toast.configImportedNeedKeysTpl', `配置已导入,请补 ${needsKey.length} 个 provider 的 key`, { n: needsKey.length }), 'info');
          // Wait for the settings modal to render before scrolling/popping
          // the edit form, otherwise the target nodes don't exist yet.
          this.$nextTick(() => {
            const card = document.querySelector(`[data-provider-id="${first.id}"]`);
            if (card && card.scrollIntoView) {
              card.scrollIntoView({ behavior: 'smooth', block: 'center' });
            }
            this.openEditProvider(first);
          });
        }
      } catch (e) { const err = e.message || String(e); this.toast(this.tFmt('toast.importFailedTpl', `导入失败: ${err}`, { err }), 'error', String(e)); }
      finally { this.dataOps.importing = false; }
    },
    async importSessions(file, merge) {
      if (!file) return;
      this.dataOps.importing = true;
      try {
        const text = await file.text();
        const obj = JSON.parse(text);
        const sessions = obj.sessions || [];
        const r = await fetch('/api/admin/import/sessions', {
          method: 'POST', headers: { 'content-type': 'application/json' },
          body: JSON.stringify({ sessions, merge: !!merge })
        });
        if (!r.ok) throw new Error((await r.text()).slice(0, 200));
        const j = await r.json();
        const mode = merge ? this.t('toast.merge', '合并') : this.t('toast.overwrite', '覆盖');
        this.toast(this.tFmt('toast.sessionsImportedTpl', `✓ 已导入 ${j.imported} 个会话(${mode})`, { n: j.imported, mode }), 'info');
        await this.loadSessions();
      } catch (e) { const err = e.message || String(e); this.toast(this.tFmt('toast.importFailedTpl', `导入失败: ${err}`, { err }), 'error', String(e)); }
      finally { this.dataOps.importing = false; }
    },
    confirmWipeSessions() {
      this.confirmModal = {
        show: true, title: this.t('confirm.wipe.title', '清空所有会话?'),
        body: this.t('confirm.wipe.body', '这会删除全部会话和消息(provider 和设置保留)。无法恢复 — 想留底就先导出。'),
        onYes: async () => {
          this.dataOps.wiping = true;
          try {
            const r = await fetch('/api/admin/wipe/sessions', { method: 'POST' }).then(r => r.json());
            this.toast(this.tFmt('toast.wipedTpl', `已清空 ${r.deleted} 个会话`, { n: r.deleted }), 'info');
            this.currentSession = null;
            this.messages = [];
            await this.loadSessions();
          } catch (e) { this.toast(this.t('toast.wipeFailed', '清空失败'), 'error', String(e)); }
          finally { this.dataOps.wiping = false; }
        }
      };
    },
    _download(name, content) {
      const blob = new Blob([content], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url; a.download = name;
      document.body.appendChild(a); a.click();
      setTimeout(() => { document.body.removeChild(a); URL.revokeObjectURL(url); }, 0);
    },
    _stamp() {
      const d = new Date();
      const p = (n) => String(n).padStart(2, '0');
      return `${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}-${p(d.getHours())}${p(d.getMinutes())}`;
    },

    // ---- composer ----
    composerPlaceholder() {
      if (this.mode === 'chat') return this.t('composer.placeholder.chat', '说点什么…  (回车发送)');
      if (this.mode === 'compare') return this.t('composer.placeholder.compare', '同一个问题,两边并行回答…');
      if (this.mode === 'debate') return this.t('composer.placeholder.debate', '辩论议题,例如:AI 是否会取代程序员?');
      if (this.mode === 'discuss') return this.t('composer.placeholder.discuss', '一件不确定的事,让两个模型一起讨论求共识…');
      return this.t('composer.placeholder.fallback', '说点什么…');
    },
    canSend() {
      if (this.streaming) return false;
      if (!this.draft.trim()) return false;
      if (!this.providers.length) return false;
      if (this.mode === 'chat') return !!(this.chatPick.providerId && this.chatPick.modelId);
      if (this.mode === 'compare' || this.mode === 'debate' || this.mode === 'discuss') {
        return !!(this.pairPick.aProviderId && this.pairPick.aModelId && this.pairPick.bProviderId && this.pairPick.bModelId);
      }
      return false;
    },
    readyHint() {
      if (this.mode === 'chat') return this.chatPick.modelId
        ? this.t('picker.ready', '已就绪')
        : this.t('picker.pickOne', '请选一个模型');
      const ok = this.pairPick.aModelId && this.pairPick.bModelId;
      const n = [this.pairPick.aModelId, this.pairPick.bModelId].filter(Boolean).length;
      return ok
        ? this.t('picker.pairReady', '已就绪(两个模型)')
        : this.tFmt('picker.pairProgressTpl', `选满 2 个模型(${n}/2)`, { n });
    },

    async ensureSession() {
      if (this.currentSession) {
        if (this.currentSession.mode !== this.mode) {
          await fetch(`/api/sessions/${this.currentSession.id}`, {
            method: 'PUT', headers: { 'content-type': 'application/json' },
            body: JSON.stringify({ mode: this.mode })
          });
          this.currentSession.mode = this.mode;
        }
        return this.currentSession;
      }
      const initialTitle = (this.draft || this.t('session.defaultTitle', '新会话')).slice(0, 40);
      const r = await fetch('/api/sessions', {
        method: 'POST', headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ title: initialTitle, mode: this.mode })
      }).then(r => r.json());
      await this.loadSessions();
      await this.selectSession(r.session.id);
      return this.currentSession;
    },

    async send() {
      if (!this.canSend()) return;
      const text = this.draft.trim();
      const sess = await this.ensureSession();
      // Title: if session title is the default placeholder (zh or en) and this
      // is the first user msg, rename to first 40 chars of the message.
      const defaultTitleZh = '新会话';
      const defaultTitleLocal = this.t('session.defaultTitle', defaultTitleZh);
      if (sess && (sess.title === defaultTitleZh || sess.title === defaultTitleLocal || !sess.title) && this.messages.length === 0) {
        const title = text.slice(0, 40);
        await fetch(`/api/sessions/${sess.id}`, {
          method: 'PUT', headers: { 'content-type': 'application/json' },
          body: JSON.stringify({ title })
        });
        sess.title = title;
        this.loadSessions();
      }

      // All four modes now accept attachments — backend bodies were
      // extended in wave 3 to take optional `attachments` and inline them
      // into the persisted user turn.
      const atts = this.attachments.length ? this.attachments.slice() : null;
      this.attachments = [];
      this.draft = '';
      this.streaming = true;

      const ws = !!this.webSearchOn;
      let url, body;
      if (this.mode === 'chat') {
        url = `/api/sessions/${sess.id}/chat`;
        body = { text, provider_id: this.chatPick.providerId, model_id: this.chatPick.modelId, web_search: ws, attachments: atts };
      } else if (this.mode === 'compare') {
        url = `/api/sessions/${sess.id}/compare`;
        body = {
          text,
          web_search: ws,
          attachments: atts,
          tracks: [
            { track_id: 'left', provider_id: this.pairPick.aProviderId, model_id: this.pairPick.aModelId },
            { track_id: 'right', provider_id: this.pairPick.bProviderId, model_id: this.pairPick.bModelId },
          ],
        };
      } else if (this.mode === 'debate') {
        url = `/api/sessions/${sess.id}/debate`;
        body = {
          topic: text,
          sub_mode: this.debate.subMode,
          rounds: this.debate.rounds,
          web_search: ws,
          attachments: atts,
          side_a: {
            provider_id: this.pairPick.aProviderId,
            model_id: this.pairPick.aModelId,
            label: this.debate.subMode === 'lead_critic' ? 'Lead' : this.t('picker.debate.debaterA', '辩手 A'),
            stance: (this.debate.stanceA || '').trim() || undefined,
          },
          side_b: {
            provider_id: this.pairPick.bProviderId,
            model_id: this.pairPick.bModelId,
            label: this.debate.subMode === 'lead_critic' ? 'Critic' : this.t('picker.debate.debaterB', '辩手 B'),
            stance: (this.debate.stanceB || '').trim() || undefined,
          },
        };
      } else {
        // discuss mode: collaborative consensus, no stance, no sub_mode
        url = `/api/sessions/${sess.id}/discuss`;
        body = {
          topic: text,
          max_rounds: this.discuss.maxRounds,
          web_search: ws,
          attachments: atts,
          side_a: { provider_id: this.pairPick.aProviderId, model_id: this.pairPick.aModelId, label: this.t('picker.sideA', 'A 方') },
          side_b: { provider_id: this.pairPick.bProviderId, model_id: this.pairPick.bModelId, label: this.t('picker.sideB', 'B 方') },
        };
      }

      try {
        await this.streamSSE(url, body, sess.id);
      } catch (e) {
        const err = e.message || String(e);
        this.toast(this.tFmt('toast.sendFailedTpl', `发送失败: ${err}`, { err }), 'error');
      } finally {
        this.streaming = false;
        this.streamCtrl = null;
        this._dockBadge(false);
        this._maybeNotifyStreamDone();
      }
    },

    async streamSSE(url, body, sid) {
      const ctrl = new AbortController();
      this.streamCtrl = ctrl;
      const resp = await fetch(url, {
        method: 'POST', headers: { 'content-type': 'application/json' },
        body: JSON.stringify(body), signal: ctrl.signal
      });
      if (!resp.ok) {
        const txt = await resp.text();
        throw new Error(txt.slice(0, 200));
      }
      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buf = '';
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        // SSE frames separated by \n\n
        let idx;
        while ((idx = buf.indexOf('\n\n')) >= 0) {
          const frame = buf.slice(0, idx);
          buf = buf.slice(idx + 2);
          this.handleSSE(frame, sid);
        }
      }
    },

    handleSSE(frame, sid) {
      const lines = frame.split('\n');
      let event = '', data = '';
      for (const l of lines) {
        if (l.startsWith('event:')) event = l.slice(6).trim();
        else if (l.startsWith('data:')) data += l.slice(5).trim();
      }
      if (!event) return;
      let payload = {};
      try { payload = data ? JSON.parse(data) : {}; } catch (e) { return; }
      const ev = payload.event || event;
      const d = payload.data || payload;
      if (ev === 'search_start') {
        const prov = d.provider ? ` · ${d.provider}` : '';
        const q = (d.query || '').slice(0, 50);
        this.toast(this.tFmt('toast.searchStartTpl', `🔍 联网搜索${prov}:${q}`, { prov, q }), 'info');
      } else if (ev === 'search_done') {
        const ms = d.elapsed_ms ? ` (${d.elapsed_ms}ms)` : '';
        const prov = d.provider ? ` · ${d.provider}` : '';
        this.toast(this.tFmt('toast.searchDoneTpl', `✓ ${d.count} 条结果${prov}${ms}`, { n: d.count, prov, ms }), 'info');
        this._lastSearchResults = d.results || [];
        this._lastSearchProvider = d.provider;
      } else if (ev === 'search_error') {
        this.toast(this.tFmt('toast.searchErrTpl', `❌ 联网搜索失败:${d.error}`, { err: d.error }), 'error');
      } else if (ev === 'user_message') {
        if (sid === this.currentSession?.id) this.messages.push(d);
      } else if (ev === 'assistant_start') {
        if (sid === this.currentSession?.id) {
          // Backend now ships search_sources directly with assistant_start; fall
          // back to _lastSearchResults for backwards compat if not present.
          const sources = (d.search_sources && d.search_sources.length)
            ? d.search_sources
            : this._lastSearchResults;
          this.messages.push({
            id: d.message_id, role: 'assistant', content: '',
            speaker: d.speaker || d.label, model_id: d.model,
            meta: {
              track: d.track,
              speaker_role: d.speaker_role,
              label: d.label,
              web_search: !!d.web_search,
              search_sources: sources && sources.length ? sources : null,
              // Derive consensus/checkpoint flags from speaker_role so the
              // banner + action buttons render immediately after streaming.
              // Server-side meta will mirror these on next session reload.
              consensus: d.speaker_role === 'consensus',
              checkpoint: d.speaker_role === 'checkpoint',
            }
          });
        }
      } else if (ev === 'stream_end') {
        // Reset per-turn search cache only at the very end.
        this._lastSearchResults = null;
        this._lastSearchProvider = null;
      } else if (ev === 'assistant_delta') {
        if (sid === this.currentSession?.id) {
          const m = this.messages.find(x => x.id === d.message_id);
          if (m) m.content += d.delta;
        }
      } else if (ev === 'assistant_error') {
        if (sid === this.currentSession?.id) {
          const m = this.messages.find(x => x.id === d.message_id);
          if (m) m.meta = { ...(m.meta || {}), error: d.error };
          const err = (d.error || '').slice(0, 80);
          this.toast(this.tFmt('toast.modelErrTpl', `模型报错: ${err}`, { err }), 'error', d.error || '');
        }
      } else if (ev === 'assistant_end') {
        if (sid === this.currentSession?.id) {
          const m = this.messages.find(x => x.id === d.message_id);
          if (m) m.content = d.content;
        }
      } else if (ev === 'fatal') {
        const err = String(d.error || '');
        this.toast(this.tFmt('toast.fatalTpl', `内部错误: ${err}`, { err }).slice(0, 200), 'error');
      }
      this.scrollToBottom();
    },

    cancelStream() {
      if (!this.currentSession) return;
      if (this.streamCtrl) try { this.streamCtrl.abort(); } catch (e) {}
      fetch(`/api/sessions/${this.currentSession.id}/cancel`, { method: 'POST' });
    },

    // ---- compare grouping ----
    get compareGrouped() {
      const rows = [];
      let curr = null;
      for (const m of this.messages) {
        if (m.role === 'user') {
          if (curr) rows.push(curr);
          curr = { key: m.id, user: m, left: null, right: null };
        } else if (m.role === 'assistant') {
          if (!curr) { curr = { key: m.id, user: null, left: null, right: null }; }
          const side = m.meta?.track || (curr.left ? 'right' : 'left');
          if (side === 'left' || side === 'right') curr[side] = m;
          else if (!curr.left) curr.left = m; else curr.right = m;
        }
      }
      if (curr) rows.push(curr);
      return rows;
    },

    // ---- bubble formatting ----
    bubbleWrapper(role) { return role === 'user' ? 'bubble-wrap-user' : 'bubble-wrap-asst'; }
    ,
    bubbleBox(m) {
      const cls = ['bubble'];
      if (m.role === 'user') cls.push('bubble-user');
      else cls.push('bubble-asst');
      if (m.meta?.error) cls.push('bubble-asst-error');
      if (m.meta?.speaker_role === 'lead') cls.push('bubble-asst-debate-lead');
      if (m.meta?.speaker_role === 'critic') cls.push('bubble-asst-debate-critic');
      // Discuss mode: visually distinguish A vs B sides with cool/warm tints.
      // checkpoint and consensus already have their own variants and don't
      // collide because they're checked AFTER the side colors below.
      if (m.meta?.speaker_role === 'a' && !m.meta?.consensus && !m.meta?.checkpoint) cls.push('bubble-asst-discuss-a');
      if (m.meta?.speaker_role === 'b') cls.push('bubble-asst-discuss-b');
      if (m.meta?.consensus) cls.push('bubble-asst-consensus');
      if (m.meta?.checkpoint) cls.push('bubble-asst-checkpoint');
      return cls.join(' ');
    },
    extractSummary(content) {
      // Pull the【概要】section out of a discuss checkpoint/consensus body
      // so the banner subtitle can show the model's own one-liner instead
      // of a generic placeholder. Returns trimmed text or '' if missing.
      if (!content) return '';
      const m = content.match(/【概要】([\s\S]*?)(?=\n【|$)/);
      if (!m) return '';
      return m[1].trim().replace(/\n+/g, ' ').slice(0, 120);
    },
    bubbleLabel(m) {
      if (m.role === 'user') return this.t('bubble.you', '你');
      const who = m.speaker || (m.meta && m.meta.label) || this.t('bubble.assistant', '助手');
      return m.model_id ? `${who} · ${m.model_id}` : who;
    },

    // ---- adopt ----
    async adopt(m) {
      await fetch(`/api/sessions/${this.currentSession.id}/adopt`, {
        method: 'POST', headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ message_id: m.id })
      });
      m.meta = { ...(m.meta || {}), adopted: true };
      this.toast(this.t('toast.adopted', '已采纳'), 'info');
    },

    // ---- settings ----
    openSettings() { this.showSettings = true; },
    closeSettings() { this.showSettings = false; },

    // ---- welcome flow ----
    // Shown when welcome_done === false AND no providers configured, OR
    // when the user manually replays it from settings. Tracks step 1-4
    // locally; persists welcome_done=true on completion.
    get welcomeVisible() {
      if (this.showWelcomeForce) return true;
      if (this.welcomeDone) return false;
      // If user already has providers, treat onboarding as silently done.
      if (this.providers.length > 0) return false;
      return true;
    },
    welcomeNext() {
      if (this.welcomeStep < 4) this.welcomeStep += 1;
    },
    welcomeBack() {
      if (this.welcomeStep > 1) this.welcomeStep -= 1;
    },
    async welcomeFinish() {
      try {
        await fetch('/api/ui-prefs', {
          method: 'PUT', headers: { 'content-type': 'application/json' },
          body: JSON.stringify({ welcome_done: true })
        });
      } catch (e) {}
      this.welcomeDone = true;
      this.showWelcomeForce = false;
      this.welcomeStep = 1;
    },
    async welcomeSkip() {
      // Same as finish — once skipped we don't nag again.
      await this.welcomeFinish();
    },
    replayWelcome() {
      this.welcomeStep = 1;
      this.showWelcomeForce = true;
      this.showSettings = false;
    },
    welcomeOpenAddProvider() {
      // Open the existing add-provider flow without closing the wizard.
      // Settings dialog is opened so the form has its surrounding context.
      this.openSettings();
      this.openAddProvider();
    },
    async maybeAutoFinishWelcome() {
      // Called after providers load: if user already has providers and the
      // backend hasn't yet recorded welcome_done, mark it done silently.
      if (!this.welcomeDone && this.providers.length > 0 && !this.showWelcomeForce) {
        await this.welcomeFinish();
      }
    },
    openAddProvider() {
      this.providerForm = emptyProviderForm();
      this.showProviderForm = true;
    },
    openEditProvider(p) {
      this.providerForm = {
        id: p.id, name: p.name, kind: p.kind,
        template_key: p.template_key || null,
        template_hint: '',
        fields: ['api_key'].concat(p.kind === 'openai_compat' ? ['base_url'] : []),
        config: { ...(p.config || {}), api_key: '' }, // blank means preserve
        modelsText: (p.models || []).join('\n'),
        testing: false, testResult: null,
        get canSave() { return !!this.name && !!this.kind; }
      };
      this.showProviderForm = true;
    },
    closeProviderForm() { this.showProviderForm = false; },
    pickTemplate(t) {
      this.providerForm = {
        id: null,
        template_key: t.key,
        template_hint: t.hint || '',
        kind: t.kind,
        name: t.label,
        fields: (t.fields || []).slice(),
        config: { ...(t.config || {}) },
        modelsText: (t.default_models || []).join('\n'),
        testing: false, testResult: null,
        get canSave() { return !!this.name && !!this.kind; }
      };
    },
    async testProvider() {
      this.providerForm.testing = true;
      this.providerForm.testResult = null;
      try {
        const models = (this.providerForm.modelsText || '').split('\n').map(s => s.trim()).filter(Boolean);
        const body = this.providerForm.id
          ? { pid: this.providerForm.id, config: this.providerForm.config, model_id: models[0] }
          : { template_key: this.providerForm.template_key, kind: this.providerForm.kind, config: this.providerForm.config, model_id: models[0] };
        const r = await fetch('/api/providers/test', {
          method: 'POST', headers: { 'content-type': 'application/json' },
          body: JSON.stringify(body)
        }).then(r => r.json());
        this.providerForm.testResult = r;
      } catch (e) {
        this.providerForm.testResult = { ok: false, message: this.t('toast.testRequestFailed', '请求失败'), detail: String(e) };
      } finally {
        this.providerForm.testing = false;
      }
    },
    async saveProvider() {
      const models = (this.providerForm.modelsText || '').split('\n').map(s => s.trim()).filter(Boolean);
      const body = {
        name: this.providerForm.name,
        template_key: this.providerForm.template_key,
        kind: this.providerForm.kind,
        models,
        config: this.providerForm.config,
        enabled: true,
      };
      if (this.providerForm.id) {
        await fetch(`/api/providers/${this.providerForm.id}`, {
          method: 'PUT', headers: { 'content-type': 'application/json' },
          body: JSON.stringify(body)
        });
        this.toast(this.t('toast.providerUpdated', '已更新'), 'info');
      } else {
        await fetch('/api/providers', {
          method: 'POST', headers: { 'content-type': 'application/json' },
          body: JSON.stringify(body)
        });
        this.toast(this.t('toast.providerAdded', '已添加'), 'info');
      }
      this.showProviderForm = false;
      await this.loadProviders();
    },
    async toggleProvider(p, enabled) {
      await fetch(`/api/providers/${p.id}`, {
        method: 'PUT', headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ enabled })
      });
      await this.loadProviders();
    },
    confirmDeleteProvider(p) {
      this.confirmModal = {
        show: true, title: this.t('confirm.provider.title', '确认删除 provider?'),
        body: this.tFmt('confirm.provider.bodyTpl', `“${p.name}” 删除后,引用它的会话将无法继续聊天。`, { name: p.name }),
        onYes: async () => {
          await fetch(`/api/providers/${p.id}`, { method: 'DELETE' });
          await this.loadProviders();
          this.toast(this.t('toast.providerDeleted', '已删除'), 'info');
        }
      };
    },

    kindLabel(k) {
      return ({
        anthropic_api: 'Anthropic API',
        claude_cli: 'Claude CLI',
        openai_api: 'OpenAI API',
        codex_cli: 'Codex CLI',
        gemini_api: 'Gemini',
        openai_compat: this.t('kind.openai_compat', 'OpenAI 兼容'),
      })[k] || k;
    },
    modeLabel(m) {
      const map = {
        chat: this.t('header.mode.chat', '单聊'),
        compare: this.t('header.mode.compare', '对比'),
        debate: this.t('header.mode.debate', '辩论'),
        discuss: this.t('header.mode.discuss', '讨论'),
      };
      return map[m] || m;
    },
    formatTime(ts) {
      const d = new Date(ts * 1000);
      const now = new Date();
      if (d.toDateString() === now.toDateString()) {
        return d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });
      }
      return d.toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit' });
    },

    // ---- toast ----
    // Pass a third arg `detail` (long string, traceback, etc) to show a
    // 查看详情 button that opens errorDetail modal — keeps the toast lane
    // tidy for one-line errors while still letting the user dig into a bug.
    toast(text, kind = 'info', detail = '') {
      const id = Math.random().toString(36).slice(2);
      this.toasts.push({ id, text, kind, detail });
      setTimeout(() => { this.toasts = this.toasts.filter(t => t.id !== id); }, kind === 'error' ? 7000 : 3500);
    },
    openErrorDetail(t) {
      this.errorDetail = { open: true, title: t.text || this.t('toast.errorTitleDefault', '错误详情'), detail: t.detail || '' };
    },

    scrollToBottom() {
      this.$nextTick(() => {
        const c = this.$refs.msgContainer;
        if (c) c.scrollTop = c.scrollHeight;
      });
    },
  };
}

function emptyProviderForm() {
  return {
    id: null,
    template_key: null,
    template_hint: '',
    kind: '',
    name: '',
    fields: [],
    config: {},
    modelsText: '',
    testing: false,
    testResult: null,
    get canSave() { return !!this.name && !!this.kind; }
  };
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

document.addEventListener('alpine:init', () => {
  // Esc closes modals
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      const root = Alpine.$data(document.getElementById('app'));
      if (root) {
        if (root.showProviderForm) root.showProviderForm = false;
        else if (root.showSettings) root.showSettings = false;
        else if (root.confirmModal?.show) root.confirmModal.show = false;
      }
    }
  });
});
