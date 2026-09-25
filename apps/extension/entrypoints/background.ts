import { defineBackground } from 'wxt/utils/define-background';
import { ApiClient, DEFAULT_SERVICE, type SessionContext, type Source } from '@ergou/contracts';
import {
  classify,
  isHttp,
  manifestChildren,
  mergeCandidate,
  type Candidate,
  type TabState,
} from '../lib/media';

export default defineBackground(() => {
  let queue = Promise.resolve();
  const pendingHeaders = new Map<string, { url: string; headers: Record<string, string> }>();
  const manifestFetches = new Set<string>();
  const serialize = (fn: () => Promise<void>) => {
    queue = queue.then(fn).catch(() => {});
  };
  const key = (id: number) => `tab:${id}`;
  const get = async (id: number): Promise<TabState> => {
    const data = await chrome.storage.session.get(key(id));
    if (data[key(id)]) return data[key(id)];
    const tab = await chrome.tabs.get(id);
    return { pageUrl: tab.url || '', title: tab.title || '', candidates: [], requests: [] };
  };
  const save = async (id: number, state: TabState) => {
    state.candidates = state.candidates.filter((c) => !state.parents?.[c.source.url]);
    await chrome.storage.session.set({ [key(id)]: state });
    await chrome.action.setBadgeBackgroundColor({ color: '#345642', tabId: id });
    const count = state.candidates.filter((c) => c.from !== 'page').length;
    await chrome.action.setBadgeText({
      tabId: id,
      text: count ? String(count) : state.candidates.length ? '?' : '',
    });
  };
  const make = (
    url: string,
    kind: Source['kind'],
    state: TabState,
    frameId: number,
    from: Candidate['from'],
  ): Candidate => ({
    id: crypto.randomUUID(),
    source: { url, page_url: state.pageUrl, title: state.title, kind },
    frameId,
    from,
    detectedAt: Date.now(),
  });
  const inspectManifest = async (tabId: number, url: string, pageUrl: string) => {
    const fetchKey = `${tabId}:${url}`;
    if (manifestFetches.has(fetchKey)) return;
    manifestFetches.add(fetchKey);
    try {
      const response = await fetch(url, { credentials: 'include', signal: AbortSignal.timeout(8000) });
      if (!response.ok || !response.body) return;
      const reader = response.body.getReader();
      const chunks: Uint8Array[] = [];
      let size = 0;
      try {
        for (;;) {
          const part = await reader.read();
          if (part.done) break;
          size += part.value.length;
          if (size > 1024 * 1024) return;
          chunks.push(part.value);
        }
      } finally {
        await reader.cancel().catch(() => {});
      }
      const buffer = new Uint8Array(size);
      let offset = 0;
      for (const chunk of chunks) {
        buffer.set(chunk, offset);
        offset += chunk.length;
      }
      const children = manifestChildren(new TextDecoder().decode(buffer), response.url);
      if (!children.length) return;
      serialize(async () => {
        const state = await get(tabId);
        if (state.pageUrl !== pageUrl) return;
        state.parents ??= {};
        for (const child of children) if (child !== url) state.parents[child] = url;
        await save(tabId, state);
      });
    } catch {
      /* The captured manifest remains usable even if background inspection is denied. */
    } finally {
      manifestFetches.delete(fetchKey);
    }
  };
  chrome.webNavigation.onCommitted.addListener((details) => {
    if (details.frameId !== 0) return;
    serialize(async () => {
      await chrome.storage.session.remove(key(details.tabId));
      await chrome.action.setBadgeText({ tabId: details.tabId, text: '' });
    });
  });
  chrome.webNavigation.onHistoryStateUpdated.addListener((details) => {
    if (details.frameId !== 0) return;
    serialize(async () => {
      await chrome.storage.session.remove(key(details.tabId));
      await chrome.action.setBadgeText({ tabId: details.tabId, text: '' });
      await chrome.tabs.sendMessage(details.tabId, { type: 'scan' }).catch(() => {});
    });
  });
  chrome.tabs.onRemoved.addListener((id) =>
    serialize(async () => {
      await chrome.storage.session.remove(key(id));
    }),
  );
  chrome.webRequest.onBeforeSendHeaders.addListener(
    (details) => {
      if (details.tabId < 0 || !['media', 'xmlhttprequest'].includes(details.type)) return;
      const headers: Record<string, string> = {};
      for (const h of details.requestHeaders || [])
        if (
          h.value &&
          ['referer', 'origin', 'user-agent', 'accept-language', 'authorization'].includes(
            h.name.toLowerCase(),
          )
        )
          headers[h.name.toLowerCase()] = h.value;
      pendingHeaders.set(details.requestId, { url: details.url, headers });
      if (pendingHeaders.size > 400) pendingHeaders.delete(pendingHeaders.keys().next().value!);
    },
    { urls: ['http://*/*', 'https://*/*'] },
    ['requestHeaders', 'extraHeaders'],
  );
  chrome.webRequest.onHeadersReceived.addListener(
    (details) => {
      if (details.tabId < 0 || details.statusCode >= 400) return;
      const mime = details.responseHeaders?.find((h) => h.name.toLowerCase() === 'content-type')?.value || '';
      const kind = classify(details.url, mime);
      if (!kind) return;
      const request = pendingHeaders.get(details.requestId);
      serialize(async () => {
        const state = await get(details.tabId);
        if (!isHttp(state.pageUrl)) return;
        if (request)
          state.requests = [...state.requests.filter((r) => r.url !== request.url), request].slice(-50);
        state.candidates = mergeCandidate(
          state.candidates,
          make(details.url, kind, state, details.frameId, 'network'),
        );
        await save(details.tabId, state);
        if (kind === 'hls') void inspectManifest(details.tabId, details.url, state.pageUrl);
      });
    },
    { urls: ['http://*/*', 'https://*/*'] },
    ['responseHeaders'],
  );
  chrome.webRequest.onCompleted.addListener((details) => pendingHeaders.delete(details.requestId), {
    urls: ['http://*/*', 'https://*/*'],
  });
  chrome.webRequest.onErrorOccurred.addListener((details) => pendingHeaders.delete(details.requestId), {
    urls: ['http://*/*', 'https://*/*'],
  });

  async function contextFor(source: Source, state: TabState, tabId: number): Promise<SessionContext> {
    const urls = new Set([source.url, source.page_url || source.url]);
    for (const [child, parent] of Object.entries(state.parents || {}))
      if (parent === source.url) urls.add(child);
    const rootOrigin = new URL(source.url).origin;
    const matching = state.requests.filter((r) => new URL(r.url).origin === rootOrigin || urls.has(r.url));
    for (const request of matching) urls.add(request.url);
    const requests = matching.slice(-45);
    if (!requests.some((r) => r.url === source.url))
      requests.push({ url: source.url, headers: { referer: source.page_url || state.pageUrl } });
    const cookieMap = new Map<string, NonNullable<SessionContext['cookies']>[number]>();
    // Ask Chrome for cookies in the initiating tab's store, never export all browser cookies.
    const stores = await chrome.cookies.getAllCookieStores();
    const storeId = stores.find((store) => store.tabIds.includes(tabId))?.id;
    let partition: chrome.cookies.CookiePartitionKey | undefined;
    try {
      const cookiesApi = chrome.cookies as typeof chrome.cookies & {
        getPartitionKey: (details: {
          tabId: number;
          frameId: number;
        }) => Promise<{ partitionKey: chrome.cookies.CookiePartitionKey }>;
      };
      partition = (await cookiesApi.getPartitionKey({ tabId, frameId: 0 })).partitionKey;
    } catch {
      /* Older Chrome or unavailable frame. */
    }
    for (const url of urls) {
      const regular = await chrome.cookies.getAll({ url, storeId });
      const partitioned = partition
        ? await chrome.cookies.getAll({ url, storeId, partitionKey: partition })
        : [];
      for (const cookie of [...regular, ...partitioned]) {
        if (cookie.partitionKey && cookie.partitionKey.topLevelSite !== partition?.topLevelSite) continue;
        cookieMap.set(`${cookie.domain}|${cookie.path}|${cookie.name}`, {
          name: cookie.name,
          value: cookie.value,
          domain: cookie.domain,
          path: cookie.path,
          secure: cookie.secure,
          host_only: cookie.hostOnly,
          expires: cookie.expirationDate,
        });
      }
    }
    return { cookies: [...cookieMap.values()].slice(0, 500), requests };
  }

  chrome.runtime.onMessage.addListener((msg, sender, respond) => {
    if (sender.id !== chrome.runtime.id) return;
    if (msg.type === 'observed' && sender.tab?.id !== undefined) {
      const tabId = sender.tab.id;
      serialize(async () => {
        const state = await get(tabId);
        if (sender.frameId === 0) {
          state.pageUrl = sender.tab?.url || msg.pageUrl;
          state.title = String(msg.title || '视频').slice(0, 512);
        }
        for (const item of (Array.isArray(msg.media) ? msg.media : []).slice(0, 100)) {
          if (typeof item.url !== 'string') continue;
          if (isHttp(item.url)) {
            const candidate = make(
              item.url,
              classify(item.url) || 'direct',
              state,
              sender.frameId || 0,
              'dom',
            );
            candidate.source.title = String(item.title || state.title).slice(0, 512);
            candidate.poster = item.poster;
            candidate.duration = item.duration;
            state.candidates = mergeCandidate(state.candidates, candidate);
          } else if (isHttp(state.pageUrl)) {
            state.candidates = mergeCandidate(
              state.candidates,
              make(state.pageUrl, 'page', state, sender.frameId || 0, 'page'),
            );
          }
        }
        await save(tabId, state);
      });
      return;
    }
    // Only extension UI can issue actions; web page/content-script messages cannot create downloads.
    if (sender.tab && sender.url !== chrome.runtime.getURL('popup.html')) return;
    (async () => {
      const settings = await chrome.storage.local.get(['service', 'token']);
      const client = new ApiClient(settings.service || DEFAULT_SERVICE, settings.token || '');
      if (msg.type === 'candidates') {
        await queue;
        return await get(msg.tabId);
      }
      if (msg.type === 'prepare') {
        await queue;
        const state = await get(msg.tabId);
        const candidate = state.candidates.find((c) => c.id === msg.candidateId);
        if (!candidate) throw new Error('页面已改变，请重新识别');
        const context = await contextFor(candidate.source, state, msg.tabId);
        const source = {
          ...candidate.source,
          requires_session:
            !!context.cookies?.length || !!context.requests?.some((r) => r.headers?.authorization),
        };
        return { source, context };
      }
      if (msg.type === 'api') {
        // Routes are fixed by the extension UI, not arbitrary web page requests.
        const { operation, body, id } = msg;
        if (operation === 'health') return await client.health();
        if (operation === 'settings') return await client.settings();
        if (operation === 'resolve')
          return await client.resolve(body.source, body.context, body.allow_invalid_tls);
        if (operation === 'resolution') return await client.resolution(id);
        if (operation === 'create') return await client.create(body);
        if (operation === 'retry') return await client.retry(id, body);
        if (operation === 'tasks') return await client.tasks('?group=history&limit=100');
      }
      throw new Error('不支持的操作');
    })().then(
      (value) => respond({ ok: true, value }),
      (error) => respond({ ok: false, error: error.message || '操作失败' }),
    );
    return true;
  });
});
