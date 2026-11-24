/* Talos bridge for the vendored OpenCowork renderer.
 *
 * The renderer expects the Electron preload API. This shim implements the
 * subset it uses and forwards chat turns to the local Talos room through the
 * bridge server. It never synthesizes audio, never stores credentials, and
 * keeps the room token on the server side.
 */
(function () {
  const CONFIG = {
    provider: 'ollama',
    apiKey: '',
    baseUrl: 'http://127.0.0.1:11434',
    customProtocol: 'openai',
    model: 'qwen2.5:1.5b',
    contextWindow: 8192,
    maxTokens: 1024,
    activeProfileKey: 'ollama',
    profiles: {
      ollama: {
        apiKey: '',
        baseUrl: 'http://127.0.0.1:11434',
        model: 'qwen2.5:1.5b',
      },
    },
    activeConfigSetId: 'talos',
    configSets: [
      {
        id: 'talos',
        name: 'Talos local',
        isSystem: true,
        provider: 'ollama',
        customProtocol: 'openai',
        activeProfileKey: 'ollama',
        profiles: {
          ollama: {
            apiKey: '',
            baseUrl: 'http://127.0.0.1:11434',
            model: 'qwen2.5:1.5b',
          },
        },
        enableThinking: false,
        updatedAt: new Date().toISOString(),
      },
    ],
    agentCliPath: '',
    defaultWorkdir: '',
    globalSkillsPath: '',
    enableDevLogs: false,
    theme: 'dark',
    sandboxEnabled: false,
    memoryEnabled: false,
    enableThinking: false,
    isConfigured: true,
  };

  const read = (key, fallback) => {
    try {
      const value = localStorage.getItem(key);
      return value ? JSON.parse(value) : fallback;
    } catch {
      return fallback;
    }
  };
  const write = (key, value) => {
    try {
      localStorage.setItem(key, JSON.stringify(value));
    } catch {
      /* storage can be unavailable */
    }
  };

  const sessions = read('talos.sessions', []);
  const messages = read('talos.messages', {});
  let listener = null;
  const emit = (event) => {
    try {
      if (listener) listener(event);
    } catch (error) {
      console.error('[Talos bridge] listener failed', error);
    }
  };
  const save = () => {
    write('talos.sessions', sessions);
    write('talos.messages', messages);
  };
  const titleFor = (prompt) => {
    const text = String(prompt || 'New chat').replace(/\s+/g, ' ').trim();
    return text.length > 48 ? text.slice(0, 48) + '…' : text || 'New chat';
  };
  const message = (sessionId, role, content) => ({
    id: 'msg-' + role + '-' + Date.now() + '-' + Math.random().toString(36).slice(2, 7),
    sessionId,
    role,
    content,
    timestamp: Date.now(),
  });

  async function respond(session, prompt) {
    const started = Date.now();
    try {
      const response = await fetch('/api/turn', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: prompt }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.error || 'Talos room returned ' + response.status);
      const text = data.reply || data.error || 'No reply.';
      const assistant = message(session.id, 'assistant', [{ type: 'text', text }]);
      assistant.executionTimeMs = data.elapsed_ms || Date.now() - started;
      (messages[session.id] = messages[session.id] || []).push(assistant);
      save();
      emit({ type: 'stream.message', payload: { sessionId: session.id, message: assistant } });
      session.status = 'idle';
      session.updatedAt = Date.now();
      save();
      emit({
        type: 'session.status',
        payload: { sessionId: session.id, status: 'idle', updatedAt: session.updatedAt },
      });
    } catch (error) {
      emit({ type: 'error', payload: { message: 'Talos bridge: ' + error.message } });
      session.status = 'error';
      save();
      emit({ type: 'session.status', payload: { sessionId: session.id, status: 'error' } });
    }
  }

  function startSession(payload) {
    const session = {
      id: 'talos-' + Date.now(),
      title: payload.title || titleFor(payload.prompt),
      status: 'running',
      cwd: payload.cwd || '',
      mountedPaths: [],
      allowedTools: [],
      memoryEnabled: false,
      model: CONFIG.model,
      createdAt: Date.now(),
      updatedAt: Date.now(),
    };
    sessions.unshift(session);
    messages[session.id] = [
      message(session.id, 'user', payload.content || [{ type: 'text', text: payload.prompt }]),
    ];
    save();
    emit({
      type: 'session.status',
      payload: { sessionId: session.id, status: 'running', updatedAt: session.updatedAt },
    });
    setTimeout(() => respond(session, payload.prompt), 0);
    return session;
  }

  function invokeClient(event) {
    const payload = event.payload || {};
    switch (event.type) {
      case 'session.start':
        return startSession(payload);
      case 'session.continue': {
        const session = sessions.find((item) => item.id === payload.sessionId);
        if (!session) return null;
        const user = message(session.id, 'user', payload.content || [
          { type: 'text', text: payload.prompt },
        ]);
        (messages[session.id] = messages[session.id] || []).push(user);
        session.status = 'running';
        save();
        setTimeout(() => respond(session, payload.prompt), 0);
        return session;
      }
      case 'session.stop': {
        const session = sessions.find((item) => item.id === payload.sessionId);
        if (session) session.status = 'idle';
        save();
        return session || null;
      }
      case 'session.delete':
        sessions.splice(0, sessions.length, ...sessions.filter((s) => s.id !== payload.sessionId));
        delete messages[payload.sessionId];
        save();
        return true;
      case 'session.rename': {
        const session = sessions.find((item) => item.id === payload.sessionId);
        if (session) session.title = payload.title;
        save();
        return session || null;
      }
      case 'session.batchDelete':
        for (const id of payload.sessionIds || []) delete messages[id];
        sessions.splice(
          0,
          sessions.length,
          ...sessions.filter((s) => !(payload.sessionIds || []).includes(s.id)),
        );
        save();
        return true;
      case 'session.list':
        emit({ type: 'session.list', payload: { sessions } });
        return sessions;
      case 'session.getMessages':
        return messages[payload.sessionId] || [];
      case 'session.getTraceSteps':
        return [];
      case 'session.getContextUsage':
        return { contextWindow: CONFIG.contextWindow, usedTokens: 0 };
      case 'folder.select':
        return null;
      case 'workdir.get':
        return '';
      default:
        return null;
    }
  }

  function sendClient(event) {
    if (!event || !event.type) return;
    if (event.type === 'session.start') {
      startSession(event.payload || {});
      return;
    }
    if (event.type === 'session.list') {
      emit({ type: 'session.list', payload: { sessions } });
      return;
    }
    invokeClient(event);
    if (event.type === 'settings.update') {
      emit({ type: 'config.status', payload: { isConfigured: true, config: CONFIG } });
    }
  }

  const stub = () => async () => null;
  const namespace = (methods) =>
    new Proxy(methods, {
      get(target, prop) {
        if (prop in target) return target[prop];
        if (prop === 'then') return undefined;
        return stub();
      },
    });

  const api = {
    on(callback) {
      listener = callback;
      return () => {
        if (listener === callback) listener = null;
      };
    },
    send: sendClient,
    invoke: async (event) => invokeClient(event),
    getVersion: async () => '0.2.0',
    getSystemTheme: async () => ({ shouldUseDarkColors: true }),
    openExternal: async (url) => {
      if (/^https?:/i.test(String(url))) window.open(url, '_blank', 'noopener');
    },
    showItemInFolder: async () => null,
    selectFiles: async () => [],
    session: namespace({
      start: async (payload) => startSession(payload),
      continue: async (payload) => invokeClient({ type: 'session.continue', payload }),
      stop: async (payload) => invokeClient({ type: 'session.stop', payload }),
      list: async () => sessions,
    }),
    config: namespace({
      get: async () => CONFIG,
      isConfigured: async () => true,
      save: async (next) => {
        Object.assign(CONFIG, next || {});
        emit({ type: 'config.status', payload: { isConfigured: true, config: CONFIG } });
        return { success: true, config: CONFIG };
      },
      switchSet: async () => ({ success: true, config: CONFIG }),
      getPresets: async () => ({}),
      test: async () => ({ ok: true }),
      listModels: async () => [{ id: CONFIG.model, name: CONFIG.model }],
    }),
    sandbox: namespace({
      getStatus: async () => ({ status: 'disabled', enabled: false, available: false }),
    }),
    mcp: namespace({
      getServerStatus: async () => [],
      getServers: async () => [],
      getTools: async () => [],
      getPresets: async () => ({}),
    }),
    remote: namespace({
      getStatus: async () => null,
      getConfig: async () => ({}),
      getPendingPairings: async () => [],
      getPairedUsers: async () => [],
      getWebhookUrl: async () => '',
      getTunnelStatus: async () => null,
    }),
    memory: namespace({
      getOverview: async () => ({}),
      listFiles: async () => [],
      read: async () => ({ content: '' }),
      readFile: async () => ({ content: '' }),
    }),
    skills: namespace({ getAll: async () => [] }),
    schedule: namespace({ list: async () => [] }),
    plugins: namespace({ listInstalled: async () => [], listCatalog: async () => [] }),
    logs: namespace({ write: async () => null, open: async () => null }),
    artifacts: namespace({ listRecentFiles: async () => [] }),
    workdir: namespace({ get: async () => '', set: async () => true }),
    folder: namespace({ select: async () => null }),
  };

  window.electronAPI = new Proxy(api, {
    get(target, prop) {
      if (prop in target) return target[prop];
      if (prop === 'then') return undefined;
      return stub();
    },
  });
})();
