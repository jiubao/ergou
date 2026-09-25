import { useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import {
  ArrowDownToLine,
  ArrowUpRight,
  Check,
  Download,
  Film,
  Link2,
  Loader2,
  RefreshCw,
  Settings2,
  X,
} from 'lucide-react';
import {
  bytes,
  canRetry,
  DEFAULT_SERVICE,
  validateService,
  type Resolution,
  type SessionContext,
  type Source,
  type Task,
  type TaskPage,
} from '@ergou/contracts';
import type { Candidate, TabState } from '../../lib/media';
import './style.css';

async function send<T>(msg: unknown): Promise<T> {
  const reply = await chrome.runtime.sendMessage(msg);
  if (!reply?.ok) throw new Error(reply?.error || '插件连接中断');
  return reply.value;
}
const api = <T,>(operation: string, body?: unknown, id?: string) =>
  send<T>({ type: 'api', operation, body, id });
function Popup() {
  const [tabId, setTabId] = useState<number>();
  const [state, setState] = useState<TabState | null>(null);
  const [service, setService] = useState(DEFAULT_SERVICE);
  const [token, setToken] = useState('');
  const [connected, setConnected] = useState(false);
  const [options, setOptions] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false);
  const [candidate, setCandidate] = useState<Candidate | null>(null);
  const [resolution, setResolution] = useState<Resolution | null>(null);
  const [format, setFormat] = useState('');
  const [allowInvalidTls, setAllowInvalidTls] = useState(true);
  const [retryTasks, setRetryTasks] = useState<Task[]>([]);
  const [retryId, setRetryId] = useState('');
  const prepared = useRef<{ source: Source; context: SessionContext } | null>(null);
  const alive = useRef(true);
  const requestIds = useRef(new Map<string, string>());
  useEffect(() => {
    void (async () => {
      const settings = await chrome.storage.local.get(['service', 'token']);
      setService(settings.service || DEFAULT_SERVICE);
      setToken(settings.token || '');
      const hasToken = Boolean(settings.token);
      if (!hasToken) setOptions(true);
      const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
      setTabId(tab?.id);
      if (tab?.id !== undefined) {
        try {
          await chrome.tabs.sendMessage(tab.id, { type: 'scan' }).catch(() => {});
          setState(await send<TabState>({ type: 'candidates', tabId: tab.id }));
        } catch {
          setState(null);
        }
      }
      if (!hasToken) return;
      try {
        await api('settings');
        setConnected(true);
      } catch {
        setConnected(false);
        setOptions(true);
      }
    })().catch((e) => setError(e.message));
    return () => {
      alive.current = false;
    };
  }, []);
  useEffect(() => {
    if (tabId === undefined) return;
    const listener = (changes: Record<string, chrome.storage.StorageChange>, area: string) => {
      if (area === 'session' && changes[`tab:${tabId}`]) setState(changes[`tab:${tabId}`].newValue || null);
    };
    chrome.storage.onChanged.addListener(listener);
    return () => chrome.storage.onChanged.removeListener(listener);
  }, [tabId]);
  const handle = async (fn: () => Promise<void>) => {
    setBusy(true);
    setError('');
    setNotice('');
    try {
      await fn();
    } catch (e) {
      setError(e instanceof Error ? e.message : '操作失败');
    } finally {
      setBusy(false);
    }
  };
  const prepare = (c: Candidate) =>
    send<{ source: Source; context: SessionContext }>({ type: 'prepare', tabId, candidateId: c.id });
  const submit = async (c: Candidate) =>
    handle(async () => {
      const p = candidate?.id === c.id && prepared.current ? prepared.current : await prepare(c);
      const selectedFormat = candidate?.id === c.id ? format : '';
      const useInvalidTls = candidate?.id === c.id ? allowInvalidTls : undefined;
      const submissionKey = `${c.id}:${selectedFormat}:${useInvalidTls ?? 'auto'}`;
      let task: Task;
      const body = {
        ...p,
        ...(useInvalidTls === undefined ? {} : { allow_invalid_tls: useInvalidTls }),
      };
      if (retryId) task = await api<Task>('retry', body, retryId);
      else {
        let requestId = requestIds.current.get(submissionKey);
        if (!requestId) {
          requestId = crypto.randomUUID();
          requestIds.current.set(submissionKey, requestId);
        }
        task = await api<Task>('create', {
          ...body,
          request_id: requestId,
          format_id: selectedFormat || undefined,
        });
        requestIds.current.delete(submissionKey);
      }
      setNotice(`已提交：${task.title}`);
      setCandidate(null);
      setResolution(null);
      setFormat('');
      setAllowInvalidTls(true);
      prepared.current = null;
      setRetryId('');
    });
  const inspect = async (c: Candidate) =>
    handle(async () => {
      const sameCandidate = candidate?.id === c.id;
      setCandidate(c);
      setFormat('');
      setResolution(null);
      prepared.current = await prepare(c);
      const useInvalidTls = sameCandidate ? allowInvalidTls : !prepared.current.source.requires_session;
      setAllowInvalidTls(useInvalidTls);
      let r = await api<Resolution>('resolve', {
        ...prepared.current,
        allow_invalid_tls: useInvalidTls,
      });
      setResolution(r);
      while (r.status === 'resolving' && alive.current) {
        await new Promise((resolve) => setTimeout(resolve, 750));
        if (!alive.current) return;
        r = await api<Resolution>('resolution', undefined, r.id);
        setResolution(r);
      }
      if (r.error) throw new Error(`${r.error.message}。${r.error.action}`);
    });
  const candidates = state?.candidates.filter((c) => c.from !== 'page') || [];
  const fallback = state?.candidates.find((c) => c.from === 'page');
  return (
    <div className="popup">
      <header>
        <div className="brand">
          <span className="logo">
            <ArrowDownToLine size={20} />
          </span>
          ergou<span className="dot">.</span>
        </div>
        <button
          className="settings-toggle"
          aria-label={options ? '收起连接设置' : '打开连接设置'}
          onClick={() => setOptions(!options)}
        >
          <Settings2 size={17} />
          {options ? '收起设置' : '连接设置'}
        </button>
      </header>
      <div className="status">
        <i className={connected ? 'online' : ''} />
        {connected ? '本地服务已连接' : '本地服务未连接'}
        <button onClick={() => void chrome.tabs.create({ url: service })}>
          下载管理 <ArrowUpRight size={12} />
        </button>
      </div>
      {options && (
        <form
          className="settings"
          onSubmit={(e) => {
            e.preventDefault();
            void handle(async () => {
              const url = validateService(service);
              await chrome.storage.local.set({ service: url, token: token.trim() });
              await api('settings');
              setService(url);
              setConnected(true);
              setOptions(false);
            });
          }}
        >
          <h2>连接本地服务</h2>
          <label>
            本地服务地址
            <input value={service} onChange={(e) => setService(e.target.value)} required />
          </label>
          <label>
            访问令牌
            <input
              type="password"
              value={token}
              onChange={(e) => setToken(e.target.value)}
              placeholder="从启动终端复制"
              required
            />
          </label>
          <button className="primary" disabled={busy}>
            保存并连接
          </button>
        </form>
      )}
      {error && (
        <div className="alert" role="alert">
          {error}
          <button className="icon" onClick={() => setError('')} aria-label="关闭提示">
            <X size={12} />
          </button>
        </div>
      )}
      {notice && (
        <div className="notice">
          <Check size={14} />
          {notice}
        </div>
      )}
      {!connected && !options && (
        <div className="help">请先运行本地服务启动脚本，再在连接设置中输入访问令牌。</div>
      )}
      <div className="section">
        <h1>
          当前页面的视频 <span>{candidates.length}</span>
        </h1>
        <button
          className="icon"
          title="重新识别"
          aria-label="重新识别"
          onClick={() => {
            if (tabId !== undefined)
              void chrome.tabs.sendMessage(tabId, { type: 'scan' }).catch(() => setError('请刷新网页后重试'));
          }}
        >
          <RefreshCw size={14} />
        </button>
      </div>
      {!candidates.length && (
        <div className="empty">
          <Film size={30} />
          <h2>等待发现视频</h2>
          <p>
            在网页上播放一下视频，
            <br />
            加载后会自动出现在这里。
          </p>
          {fallback && (
            <button className="secondary" disabled={!connected || busy} onClick={() => inspect(fallback)}>
              <Link2 size={13} />
              尝试解析原页面
            </button>
          )}
        </div>
      )}
      <div className="candidates">
        {candidates.map((c) => (
          <article className="card" key={c.id}>
            <div className="video-icon">
              <Film size={20} />
            </div>
            <div className="card-body">
              <h2 title={c.source.title || c.source.url}>{c.source.title || '网页视频'}</h2>
              <p>
                {c.source.kind?.toUpperCase()} · {new URL(c.source.url).hostname}
                {c.duration ? ` · ${Math.round(c.duration)} 秒` : ''}
              </p>
              <div className="card-actions">
                <button className="text-button" disabled={!connected || busy} onClick={() => inspect(c)}>
                  选择清晰度
                </button>
                <button className="small-primary" disabled={!connected || busy} onClick={() => submit(c)}>
                  <Download size={12} />
                  下载
                </button>
              </div>
            </div>
          </article>
        ))}
      </div>
      {candidate && (
        <div className="resolution">
          <div className="section">
            <h2>{resolution?.media?.title || '正在解析视频…'}</h2>
            <button
              className="icon"
              onClick={() => {
                setCandidate(null);
                setResolution(null);
                setAllowInvalidTls(true);
              }}
              aria-label="关闭解析结果"
            >
              <X size={14} />
            </button>
          </div>
          {busy && (
            <p>
              <Loader2 size={14} className="spin" /> 正在获取媒体信息…
            </p>
          )}
          {resolution?.media?.formats?.length ? (
            <label>
              清晰度
              <select value={format} onChange={(e) => setFormat(e.target.value)}>
                <option value="">默认画质</option>
                {resolution.media.formats.map((f, i) => (
                  <option key={f.id + i} value={f.id}>
                    {f.label} · {bytes(f.filesize)}
                  </option>
                ))}
              </select>
            </label>
          ) : null}
          <label className="tls-option">
            <span>
              <input
                type="checkbox"
                checked={allowInvalidTls}
                disabled={busy}
                onChange={(e) => {
                  setAllowInvalidTls(e.target.checked);
                  setResolution(null);
                  setFormat('');
                }}
              />
              允许无效 HTTPS 证书
            </span>
            <small>仅用于当前任务，包含关联媒体站点；开启后无法验证服务器身份。</small>
          </label>
          {(!resolution || resolution.status === 'failed') && (
            <button className="secondary" disabled={busy} onClick={() => inspect(candidate)}>
              <RefreshCw size={13} />
              使用当前选项重新解析
            </button>
          )}
          {resolution?.media?.entries?.map((entry, i) => (
            <button
              className="entry"
              key={entry.url + i}
              disabled={busy}
              onClick={() =>
                void handle(async () => {
                  const task = await api<Task>('create', {
                    source: entry,
                    context: prepared.current?.context,
                    allow_invalid_tls: allowInvalidTls,
                    request_id: crypto.randomUUID(),
                  });
                  setNotice(`已提交：${task.title}`);
                })
              }
            >
              {entry.title || entry.url}
              <Download size={13} />
            </button>
          ))}
          {!resolution?.media?.entries?.length && resolution?.status === 'completed' && (
            <button className="primary" disabled={busy} onClick={() => submit(candidate)}>
              下载所选画质
            </button>
          )}
        </div>
      )}
      {connected && (
        <details className="update">
          <summary
            onClick={() =>
              void api<TaskPage>('tasks')
                .then((p) => setRetryTasks(p.items.filter((t) => canRetry(t.status))))
                .catch(() => {})
            }
          >
            更新已有任务的来源
          </summary>
          <p>选择一个失败或中断的任务，再点击上方对应视频的下载按钮。</p>
          <select
            aria-label="选择需要更新的任务"
            value={retryId}
            onChange={(e) => setRetryId(e.target.value)}
          >
            <option value="">新建任务（默认）</option>
            {retryTasks.map((t) => (
              <option key={t.id} value={t.id}>
                {t.title}
              </option>
            ))}
          </select>
        </details>
      )}
      <footer>视频在本机下载 · 关闭弹窗后任务继续</footer>
    </div>
  );
}
createRoot(document.getElementById('root')!).render(<Popup />);
