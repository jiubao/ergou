import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  ArrowLeft,
  ArrowDownToLine,
  ArrowUpRight,
  Check,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  Clock3,
  Download,
  ExternalLink,
  Eye,
  Film,
  FolderOpen,
  History,
  Link2,
  Loader2,
  Plus,
  Play,
  RotateCcw,
  Search,
  Settings2,
  ShieldCheck,
  Unplug,
  X,
  Trash2,
} from 'lucide-react';
import {
  ApiClient,
  bytes,
  canRetry,
  isActive,
  labels,
  type Health,
  type Resolution,
  type Settings,
  type Source,
  type Task,
  type TaskPage,
} from '@ergou/contracts';
import s from './App.module.css';

const savedToken = () => localStorage.getItem('ergou.token') || '';
const host = (url: string) => {
  try {
    return new URL(url).hostname;
  } catch {
    return '网页视频';
  }
};
const qualityName = (value: string) => (value === 'best' ? '最高可用画质' : `${value}p`);
const taskQuality = (task: Task) => (task.height ? `${task.height}p` : `目标：${qualityName(task.quality)}`);
const message = (error: unknown) => (error instanceof Error ? error.message : '操作失败，请重试');
const tlsCertificateLabel = (status: Task['tls_certificate_status']) =>
  ({
    unchecked: '尚未检测',
    checking: '正在检测…',
    valid: '证书有效',
    invalid: '验证失败（不影响兼容下载）',
    unavailable: '无法检测（不影响兼容下载）',
    not_applicable: 'HTTP 地址，无需验证',
  })[status];
const timeLabel = (seconds?: number | null) =>
  seconds == null
    ? '计算剩余时间…'
    : seconds < 60
      ? `剩余 ${Math.ceil(seconds)} 秒`
      : `剩余 ${Math.ceil(seconds / 60)} 分钟`;
const playerIdFromHash = () => {
  const match = location.hash.match(/^#\/play\/([^/?#]+)$/);
  if (!match) return null;
  try {
    return decodeURIComponent(match[1]);
  } catch {
    return null;
  }
};

export function App() {
  const [token, setToken] = useState(savedToken);
  const [view, setView] = useState('tasks');
  const [filter, setFilter] = useState('all');
  const [search, setSearch] = useState('');
  const [page, setPage] = useState(0);
  const [data, setData] = useState<TaskPage>({ items: [], total: 0 });
  const [counts, setCounts] = useState({ all: 0, active: 0, completed: 0 });
  const [health, setHealth] = useState<Health | null>(null);
  const [connected, setConnected] = useState(false);
  const [error, setError] = useState('');
  const [newTask, setNewTask] = useState(false);
  const [detail, setDetail] = useState<Task | null>(null);
  const [detailTls, setDetailTls] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<Task | null>(null);
  const [deleteFile, setDeleteFile] = useState(false);
  const [deleteError, setDeleteError] = useState('');
  const [busy, setBusy] = useState<string | null>(null);
  const [playerId, setPlayerId] = useState<string | null>(playerIdFromHash);
  const client = useMemo(() => new ApiClient(location.origin, token), [token]);
  const query = useMemo(() => {
    const q = new URLSearchParams({ offset: String(page * 20), limit: '20' });
    if (view === 'history') q.set('group', 'history');
    else if (filter === 'active') q.set('group', 'active');
    else if (filter === 'completed') q.set('status', 'completed');
    if (search.trim()) q.set('search', search.trim());
    return '?' + q;
  }, [view, filter, search, page]);
  const reload = useCallback(async () => {
    try {
      const [list, all, active, complete, service] = await Promise.all([
        client.tasks(query),
        client.tasks('?limit=1'),
        client.tasks('?limit=1&group=active'),
        client.tasks('?limit=1&status=completed'),
        client.health(),
      ]);
      setData(list);
      setCounts({ all: all.total, active: active.total, completed: complete.total });
      setHealth(service);
      setConnected(true);
    } catch (e) {
      setConnected(false);
      setError(message(e));
    }
  }, [client, query]);
  const reloadRef = useRef(reload);
  useEffect(() => {
    const update = () => setPlayerId(playerIdFromHash());
    addEventListener('hashchange', update);
    return () => removeEventListener('hashchange', update);
  }, []);
  useEffect(() => {
    reloadRef.current = reload;
  }, [reload]);
  useEffect(() => {
    if (token) {
      const id = setTimeout(reload, 150);
      return () => clearTimeout(id);
    }
  }, [reload, token]);
  useEffect(() => {
    if (!token) return;
    let stopped = false;
    let socket: WebSocket;
    let timer: ReturnType<typeof setTimeout>;
    let refresh: ReturnType<typeof setTimeout> | undefined;
    const connect = () => {
      socket = new WebSocket(`${location.origin.replace(/^http/, 'ws')}/api/v1/events`);
      socket.onopen = () => socket.send(JSON.stringify({ token }));
      socket.onmessage = (event) => {
        const msg = JSON.parse(event.data);
        if (msg.type === 'ready') {
          setConnected(true);
          void reloadRef.current();
        }
        if (msg.type === 'task') {
          const task = msg.task as Task;
          setData((old) => ({ ...old, items: old.items.map((t) => (t.id === task.id ? task : t)) }));
          setDetail((old) => (old?.id === task.id ? task : old));
          if (!refresh)
            refresh = setTimeout(() => {
              refresh = undefined;
              void reloadRef.current();
            }, 800);
        }
      };
      socket.onclose = (event) => {
        setConnected(false);
        if (!stopped && event.code !== 1008) timer = setTimeout(connect, 2000);
        if (event.code === 1008) setError('访问令牌无效，请重新连接');
      };
      socket.onerror = () => setConnected(false);
    };
    connect();
    // Snapshot recovery also covers events dropped while a browser tab was suspended.
    const interval = setInterval(() => void reloadRef.current(), 15000);
    return () => {
      stopped = true;
      clearTimeout(timer);
      clearTimeout(refresh);
      clearInterval(interval);
      socket?.close();
    };
  }, [token]);

  const action = async (task: Task, operation: string) => {
    setBusy(task.id);
    try {
      if (operation === 'cancel') await client.cancel(task.id);
      else if (operation === 'retry') await client.retry(task.id);
      else await client.fileAction(task.id, operation as 'open' | 'reveal');
      await reload();
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(null);
    }
  };
  const openDelete = (task: Task) => {
    setDetail(null);
    setDeleteTarget(task);
    setDeleteFile(false);
    setDeleteError('');
  };
  const confirmDelete = async () => {
    if (!deleteTarget) return;
    setBusy(deleteTarget.id);
    setDeleteError('');
    try {
      await client.deleteTask(deleteTarget.id, deleteFile);
      setDeleteTarget(null);
      setDeleteFile(false);
      await reload();
    } catch (e) {
      setDeleteError(message(e));
    } finally {
      setBusy(null);
    }
  };
  const logout = () => {
    localStorage.removeItem('ergou.token');
    setToken('');
    setError('');
  };
  const openPlayer = (task: Task) => {
    setDetail(null);
    location.hash = `/play/${encodeURIComponent(task.id)}`;
  };
  const leavePlayer = () => {
    history.replaceState(null, '', location.pathname + location.search);
    setPlayerId(null);
  };
  if (!token)
    return (
      <Connect
        onConnect={(value) => {
          localStorage.setItem('ergou.token', value);
          setToken(value);
        }}
      />
    );
  const title = playerId
    ? '视频播放'
    : view === 'settings'
      ? '偏好设置'
      : view === 'history'
        ? '下载历史'
        : '下载管理';
  return (
    <div className={s.app}>
      <aside className={s.sidebar}>
        <div className={s.brand}>
          <div className={s.logo}>
            <ArrowDownToLine size={23} />
          </div>
          <span>
            ergou<span style={{ color: '#bddf93' }}>.</span>
          </span>
        </div>
        <nav className={s.nav} aria-label="主要导航">
          {[
            ['tasks', '下载管理', Download],
            ['history', '下载历史', History],
            ['settings', '偏好设置', Settings2],
          ].map(([id, label, Icon]) => {
            const I = Icon as typeof Download;
            return (
              <button
                key={String(id)}
                className={!playerId && view === id ? s.selected : ''}
                onClick={() => {
                  history.replaceState(null, '', location.pathname + location.search);
                  setPlayerId(null);
                  setView(String(id));
                  setPage(0);
                  setFilter('all');
                  setSearch('');
                }}
              >
                <I size={18} />
                <span>{String(label)}</span>
              </button>
            );
          })}
        </nav>
        <div className={s.sideBottom}>
          <div className={s.local}>
            <i className={`${s.dot} ${!connected ? s.offline : ''}`} />
            {connected ? '本地服务已连接' : '正在连接本地服务'}
          </div>
          文件保存在你的电脑上
          <br />
          Ergou · v{health?.version || '0.1.0'}
        </div>
      </aside>
      <main className={s.main}>
        <div className={s.topbar}>
          <span>
            工作空间 <ChevronRight size={12} /> {title}
          </span>
          <button onClick={logout}>
            <Unplug size={13} style={{ verticalAlign: 'middle', marginRight: 7 }} />
            重新连接
          </button>
        </div>
        <div className={s.heading}>
          <div>
            <div className={s.eyebrow}>YOUR LOCAL VIDEO LIBRARY</div>
            <h1>{title}</h1>
            <p className={s.subtitle}>
              {playerId
                ? '直接播放保存在本机的视频。'
                : view === 'settings'
                  ? '按你的习惯，设置每一次下载。'
                  : view === 'history'
                    ? '每一次保存，都有迹可循。'
                    : '把值得留存的视频，留在身边。'}
            </p>
          </div>
          {!playerId && view !== 'settings' && (
            <button className={s.primary} onClick={() => setNewTask(true)}>
              <Plus size={16} />
              新建下载
            </button>
          )}
        </div>
        {error && (
          <div className={s.alert} role="alert">
            <span>{error}</span>
            <button aria-label="关闭提示" onClick={() => setError('')}>
              <X size={15} />
            </button>
          </div>
        )}
        {health && (!health.ffmpeg || !health.ffprobe) && (
          <div className={s.alert}>FFmpeg / ffprobe 尚未就绪。请运行依赖检查脚本，配置完成后再下载。</div>
        )}
        {!connected && (
          <div className={s.alert}>本地服务暂未连接。请确认启动脚本仍在运行；连接恢复后将自动更新任务。</div>
        )}
        {playerId ? (
          <PlayerPage client={client} taskId={playerId} back={leavePlayer} />
        ) : view === 'settings' ? (
          <SettingsPanel client={client} health={health} onError={setError} />
        ) : (
          <>
            <div className={s.stats}>
              {[
                ['正在进行', counts.active, ArrowDownToLine],
                ['已完成', counts.completed, CheckCircle2],
                ['全部任务', counts.all, Film],
              ].map(([label, value, Icon]) => {
                const I = Icon as typeof Film;
                return (
                  <div className={s.stat} key={String(label)}>
                    <div>
                      <div className={s.statLabel}>{String(label)}</div>
                      <div className={s.statValue}>{String(value).padStart(2, '0')}</div>
                    </div>
                    <div className={s.statIcon}>
                      <I size={21} />
                    </div>
                  </div>
                );
              })}
            </div>
            <div className={s.toolbar}>
              <div className={s.tabs}>
                {view === 'history' ? (
                  <button className={s.tabActive}>已结束的任务</button>
                ) : (
                  [
                    ['all', '全部任务'],
                    ['active', '进行中'],
                    ['completed', '已完成'],
                  ].map(([id, label]) => (
                    <button
                      key={id}
                      onClick={() => {
                        setFilter(id);
                        setPage(0);
                      }}
                      className={filter === id ? s.tabActive : ''}
                    >
                      {label}
                    </button>
                  ))
                )}
              </div>
              <label className={s.search}>
                <Search size={14} />
                <input
                  aria-label="搜索视频"
                  placeholder="搜索视频名称…"
                  value={search}
                  onChange={(e) => {
                    setSearch(e.target.value);
                    setPage(0);
                  }}
                />
              </label>
            </div>
            <div className={s.list}>
              {data.items.length ? (
                data.items.map((task) => (
                  <TaskCard
                    key={task.id}
                    task={task}
                    busy={busy === task.id}
                    onAction={(op) => void action(task, op)}
                    onDetail={() => {
                      setDetail(task);
                      setDetailTls(task.allow_invalid_tls);
                    }}
                    onPlay={() => openPlayer(task)}
                    onDelete={() => openDelete(task)}
                  />
                ))
              ) : (
                <div className={s.empty}>
                  <div className={s.emptyIcon}>
                    <Download size={28} />
                  </div>
                  <h2>{search ? '没有找到匹配的视频' : '你的下一段珍藏，从这里开始'}</h2>
                  <p>
                    {search
                      ? '试试其他名称，或清除搜索条件。'
                      : '用 Chrome 插件发现网页中的视频，或点击「新建下载」粘贴一个视频地址。'}
                  </p>
                  {!search && (
                    <button className={s.secondary} onClick={() => setNewTask(true)}>
                      <Link2 size={15} />
                      添加视频链接
                    </button>
                  )}
                </div>
              )}
            </div>
            {data.total > 20 && (
              <div className={s.pager}>
                <button
                  className={s.iconButton}
                  aria-label="上一页"
                  disabled={page === 0}
                  onClick={() => setPage(page - 1)}
                >
                  <ChevronLeft size={15} />
                </button>
                {page + 1} / {Math.ceil(data.total / 20)}
                <button
                  className={s.iconButton}
                  aria-label="下一页"
                  disabled={(page + 1) * 20 >= data.total}
                  onClick={() => setPage(page + 1)}
                >
                  <ChevronRight size={15} />
                </button>
              </div>
            )}
            <div className={s.footer}>
              <span className={s.hint}>
                <ShieldCheck size={13} />
                本地下载 · 文件由你掌管
              </span>
              <span>共 {data.total} 个任务</span>
            </div>
          </>
        )}
      </main>
      {newTask && (
        <NewTask
          client={client}
          close={() => setNewTask(false)}
          done={() => {
            setNewTask(false);
            void reload();
          }}
        />
      )}
      {detail && (
        <Modal title="任务详情" close={() => setDetail(null)}>
          <h3 style={{ fontSize: 16, lineHeight: 1.7, overflowWrap: 'anywhere' }}>{detail.title}</h3>
          <div className={s.divider} />
          {[
            ['状态', labels[detail.status]],
            ['清晰度', taskQuality(detail)],
            ['HTTPS 证书兼容', detail.allow_invalid_tls ? '已开启' : '关闭'],
            ['HTTPS 证书验证', tlsCertificateLabel(detail.tls_certificate_status)],
            ['已下载', bytes(detail.downloaded_bytes)],
            ['文件路径', detail.output_path || '下载完成后显示'],
            ['创建时间', new Date(detail.created_at).toLocaleString()],
          ].map(([k, v]) => (
            <div className={s.row} key={k}>
              <span>{k}</span>
              <strong>{v}</strong>
            </div>
          ))}
          {detail.error && (
            <div className={s.errorText}>
              {detail.error.message}
              <br />
              {detail.error.action}
            </div>
          )}
          {canRetry(detail.status) && (
            <>
              <label className={s.checkOption}>
                <span>
                  <input
                    type="checkbox"
                    checked={detailTls}
                    onChange={(e) => setDetailTls(e.target.checked)}
                  />
                  允许无效 HTTPS 证书
                </span>
                <small>默认开启并覆盖关联媒体站点；证书验证结果只作提示，不会关闭兼容模式。</small>
              </label>
              <div className={s.modalActions}>
                <button
                  className={s.primary}
                  disabled={busy === detail.id}
                  onClick={() => {
                    setBusy(detail.id);
                    void client
                      .retry(detail.id, { allow_invalid_tls: detailTls })
                      .then((task) => {
                        setDetail(task);
                        void reload();
                      })
                      .catch((e) => setError(message(e)))
                      .finally(() => setBusy(null));
                  }}
                >
                  <RotateCcw size={14} />
                  使用当前选项重试
                </button>
              </div>
            </>
          )}
          <div className={s.modalActions}>
            {detail.status === 'completed' && (
              <button className={s.primary} onClick={() => openPlayer(detail)}>
                <Play size={14} fill="currentColor" />
                播放视频
              </button>
            )}
            <a
              className={s.secondary}
              href={detail.source.page_url || detail.source.url}
              target="_blank"
              rel="noreferrer"
            >
              返回原网页
              <ExternalLink size={13} />
            </a>
            <button className={s.danger} onClick={() => openDelete(detail)}>
              <Trash2 size={13} />
              删除任务
            </button>
          </div>
          {canRetry(detail.status) && detail.source.requires_session && (
            <p className={s.subtitle}>
              可先直接重试捕获到的地址；若登录信息已经失效，再回到原网页通过插件更新来源。
            </p>
          )}
        </Modal>
      )}
      {deleteTarget && (
        <Modal
          title="删除任务"
          close={() => {
            if (busy !== deleteTarget.id) setDeleteTarget(null);
          }}
        >
          <p className={s.subtitle} style={{ marginTop: 0 }}>
            确定删除“{deleteTarget.title}”吗？任务记录和临时数据将被永久删除。
          </p>
          {isActive(deleteTarget.status) && (
            <div className={s.errorText}>该任务仍在进行中，删除时会先停止下载。</div>
          )}
          {deleteTarget.output_path && (
            <label className={s.checkOption}>
              <span>
                <input
                  type="checkbox"
                  checked={deleteFile}
                  onChange={(e) => setDeleteFile(e.target.checked)}
                  disabled={busy === deleteTarget.id}
                />
                同时删除视频文件
              </span>
              <small>默认只删除任务记录，保留下载目录中的视频。</small>
            </label>
          )}
          {deleteError && (
            <p className={s.errorText} role="alert">
              {deleteError}
            </p>
          )}
          <div className={s.modalActions}>
            <button
              className={s.secondary}
              disabled={busy === deleteTarget.id}
              onClick={() => setDeleteTarget(null)}
            >
              取消
            </button>
            <button
              className={s.danger}
              disabled={busy === deleteTarget.id}
              onClick={() => void confirmDelete()}
            >
              <Trash2 size={14} />
              确认删除
            </button>
          </div>
        </Modal>
      )}
    </div>
  );
}

function Connect({ onConnect }: { onConnect: (token: string) => void }) {
  const [token, setToken] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  return (
    <div className={s.connect}>
      <form
        className={s.connectCard}
        onSubmit={async (e) => {
          e.preventDefault();
          setBusy(true);
          try {
            await new ApiClient(location.origin, token.trim()).settings();
            onConnect(token.trim());
          } catch (e) {
            setError(message(e));
          } finally {
            setBusy(false);
          }
        }}
      >
        <div className={`${s.brand} ${s.connectBrand}`}>
          <div className={s.logo}>
            <ArrowDownToLine size={23} />
          </div>
          <span>ergou.</span>
        </div>
        <h1>连接你的本地工作空间</h1>
        <p>输入启动服务时显示的访问令牌，即可管理这台电脑上的视频下载。</p>
        <label className={s.field}>
          访问令牌
          <input
            type="password"
            autoFocus
            autoComplete="off"
            required
            value={token}
            onChange={(e) => setToken(e.target.value)}
            placeholder="粘贴本地服务访问令牌"
          />
        </label>
        {error && (
          <p className={s.errorText} role="alert">
            {error}
          </p>
        )}
        <button className={s.primary} style={{ width: '100%' }} disabled={busy}>
          {busy ? <Loader2 size={16} className={s.spin} /> : <ArrowUpRight size={16} />}连接工作空间
        </button>
      </form>
    </div>
  );
}

type SavedPlayback = {
  path: string;
  size: number | null;
  currentTime: number;
};

function PlayerPage({ client, taskId, back }: { client: ApiClient; taskId: string; back: () => void }) {
  const [task, setTask] = useState<Task | null>(null);
  const [mediaUrl, setMediaUrl] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [mediaFailed, setMediaFailed] = useState(false);
  const video = useRef<HTMLVideoElement>(null);
  const retried = useRef(false);
  const lastSaved = useRef(0);
  const progressKey = `ergou.playback.${taskId}`;

  const requestMedia = useCallback(async () => {
    const session = await client.createPlaybackSession(taskId);
    const url = new URL(session.url, client.base);
    url.searchParams.set('session', session.expires_at);
    setMediaUrl(url.href);
  }, [client, taskId]);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError('');
    setMediaFailed(false);
    retried.current = false;
    void client
      .task(taskId)
      .then(async (next) => {
        if (next.status !== 'completed' || !next.output_path) throw new Error('视频尚未下载完成');
        if (!active) return;
        setTask(next);
        await requestMedia();
      })
      .catch((reason) => {
        if (active) setError(message(reason));
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [client, requestMedia, taskId]);

  const saveProgress = useCallback(() => {
    const element = video.current;
    if (!task || !element || !Number.isFinite(element.currentTime)) return;
    if (element.ended) {
      localStorage.removeItem(progressKey);
      return;
    }
    if (element.currentTime >= 5) {
      const value: SavedPlayback = {
        path: task.output_path || '',
        size: task.total_bytes,
        currentTime: element.currentTime,
      };
      localStorage.setItem(progressKey, JSON.stringify(value));
    }
  }, [progressKey, task]);

  useEffect(() => {
    const save = () => saveProgress();
    addEventListener('pagehide', save);
    return () => {
      saveProgress();
      removeEventListener('pagehide', save);
    };
  }, [saveProgress]);

  const fileAction = async (operation: 'open' | 'reveal') => {
    if (!task) return;
    try {
      await client.fileAction(task.id, operation);
    } catch (reason) {
      setError(message(reason));
    }
  };

  return (
    <section className={s.playerPage} aria-label="视频播放页">
      <div className={s.playerToolbar}>
        <button className={s.secondary} onClick={back}>
          <ArrowLeft size={15} />
          返回任务列表
        </button>
        {task && (
          <div className={s.playerActions}>
            <button className={s.secondary} onClick={() => void fileAction('open')}>
              <ExternalLink size={14} />
              系统播放器打开
            </button>
            <button className={s.secondary} onClick={() => void fileAction('reveal')}>
              <FolderOpen size={14} />
              打开文件夹
            </button>
          </div>
        )}
      </div>
      {task && (
        <div className={s.playerTitle}>
          <h2>{task.title}</h2>
          <p>
            {host(task.source.page_url || task.source.url)} · {taskQuality(task)} · {bytes(task.total_bytes)}
          </p>
        </div>
      )}
      <div className={s.playerFrame}>
        {loading && (
          <div className={s.playerPlaceholder}>
            <Loader2 className={s.spin} size={24} />
            正在准备本地视频…
          </div>
        )}
        {!loading && mediaUrl && (
          <video
            key={mediaUrl}
            ref={video}
            aria-label={task ? `播放 ${task.title}` : '本地视频播放器'}
            controls
            playsInline
            preload="metadata"
            src={mediaUrl}
            onLoadedMetadata={(event) => {
              setMediaFailed(false);
              try {
                const saved = JSON.parse(localStorage.getItem(progressKey) || 'null') as SavedPlayback | null;
                const element = event.currentTarget;
                if (
                  saved &&
                  task &&
                  saved.path === task.output_path &&
                  saved.size === task.total_bytes &&
                  saved.currentTime >= 5 &&
                  saved.currentTime < element.duration - 10
                ) {
                  element.currentTime = saved.currentTime;
                }
              } catch {
                localStorage.removeItem(progressKey);
              }
            }}
            onTimeUpdate={() => {
              if (Date.now() - lastSaved.current >= 5000) {
                lastSaved.current = Date.now();
                saveProgress();
              }
            }}
            onPause={saveProgress}
            onEnded={() => localStorage.removeItem(progressKey)}
            onError={() => {
              if (!retried.current) {
                retried.current = true;
                void requestMedia().catch((reason) => setError(message(reason)));
              } else {
                setMediaFailed(true);
              }
            }}
          />
        )}
        {!loading && !mediaUrl && (
          <div className={s.playerPlaceholder}>
            <Film size={30} />
            无法载入本地视频
          </div>
        )}
      </div>
      {mediaFailed && (
        <div className={s.alert} role="alert">
          当前浏览器无法播放此文件的封装或编码。你可以使用系统播放器打开。
        </div>
      )}
      {error && (
        <div className={s.alert} role="alert">
          {error}
        </div>
      )}
      <p className={s.subtitle}>观看位置保存在当前浏览器中；播放结束后会自动清除。</p>
    </section>
  );
}

function TaskCard({
  task,
  busy,
  onAction,
  onDetail,
  onPlay,
  onDelete,
}: {
  task: Task;
  busy: boolean;
  onAction: (op: string) => void;
  onDetail: () => void;
  onPlay: () => void;
  onDelete: () => void;
}) {
  const active = isActive(task.status);
  const percent = task.total_bytes ? Math.min(99, (task.downloaded_bytes / task.total_bytes) * 100) : null;
  return (
    <article className={s.task}>
      <div className={s.preview}>
        <Film size={26} />
        <span className={s.previewLabel}>
          {task.source.kind === 'unknown' ? 'VIDEO' : task.source.kind.toUpperCase()}
        </span>
      </div>
      <div className={s.taskBody}>
        <button className={s.taskTitle} onClick={onDetail}>
          {task.title}
        </button>
        <div className={s.meta}>
          <span>{host(task.source.page_url || task.source.url)}</span>
          <span>·</span>
          <span>{taskQuality(task)}</span>
          <span>·</span>
          <span>{bytes(task.total_bytes)}</span>
        </div>
        {active && (
          <>
            <div
              className={s.progress}
              role="progressbar"
              aria-label="下载进度"
              aria-valuenow={percent ?? undefined}
              aria-valuemin={0}
              aria-valuemax={100}
            >
              <div
                className={`${s.progressBar} ${percent === null || task.status === 'merging' ? s.indeterminate : ''}`}
                style={percent !== null && task.status !== 'merging' ? { width: `${percent}%` } : undefined}
              />
            </div>
            <div className={s.progressText}>
              <span>
                {task.status === 'downloading'
                  ? `${bytes(task.downloaded_bytes)}${task.speed ? ` · ${bytes(task.speed)}/s` : ''}`
                  : labels[task.status]}
              </span>
              <span>{task.status === 'downloading' ? timeLabel(task.eta) : '请稍候…'}</span>
            </div>
          </>
        )}
        {task.error && <p className={s.errorText}>{task.error.message}</p>}
      </div>
      <span className={`${s.badge} ${['failed', 'interrupted'].includes(task.status) ? s.errorBadge : ''}`}>
        {task.status === 'completed' && <Check size={11} />} {labels[task.status]}
      </span>
      <div className={s.actions}>
        {active && (
          <button
            className={s.iconButton}
            disabled={busy}
            aria-label="取消下载"
            title="取消下载"
            onClick={() => onAction('cancel')}
          >
            <X size={15} />
          </button>
        )}
        {canRetry(task.status) && (
          <button
            className={s.iconButton}
            disabled={busy}
            aria-label="重试下载"
            title="重试下载"
            onClick={() => onAction('retry')}
          >
            <RotateCcw size={15} />
          </button>
        )}
        {task.status === 'completed' && (
          <>
            <button className={s.iconButton} title="播放视频" aria-label="播放视频" onClick={onPlay}>
              <Play size={15} fill="currentColor" />
            </button>
            <button
              className={s.iconButton}
              disabled={busy}
              title="打开文件"
              aria-label="打开文件"
              onClick={() => onAction('open')}
            >
              <ExternalLink size={15} />
            </button>
            <button
              className={s.iconButton}
              disabled={busy}
              title="打开文件夹"
              aria-label="打开文件夹"
              onClick={() => onAction('reveal')}
            >
              <FolderOpen size={15} />
            </button>
          </>
        )}
        <button className={s.iconButton} title="任务详情" aria-label="任务详情" onClick={onDetail}>
          <Eye size={15} />
        </button>
        <button className={s.iconButton} title="删除任务" aria-label="删除任务" onClick={onDelete}>
          <Trash2 size={15} />
        </button>
      </div>
    </article>
  );
}

function Modal({ title, close, children }: { title: string; close: () => void; children: React.ReactNode }) {
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') close();
    };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, [close]);
  return (
    <div
      className={s.overlay}
      onClick={(e) => {
        if (e.target === e.currentTarget) close();
      }}
    >
      <section className={s.modal} role="dialog" aria-modal="true" aria-label={title}>
        <div className={s.modalHead}>
          <h2>{title}</h2>
          <button className={s.iconButton} onClick={close} aria-label="关闭">
            <X size={17} />
          </button>
        </div>
        {children}
      </section>
    </div>
  );
}

function NewTask({ client, close, done }: { client: ApiClient; close: () => void; done: () => void }) {
  const [url, setUrl] = useState('');
  const [source, setSource] = useState<Source | null>(null);
  const [result, setResult] = useState<Resolution | null>(null);
  const [format, setFormat] = useState('');
  const [allowInvalidTls, setAllowInvalidTls] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const alive = useRef(true);
  const requestId = useRef(crypto.randomUUID());
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);
  const getSource = (): Source => {
    const parsed = new URL(url.trim());
    if (!['http:', 'https:'].includes(parsed.protocol)) throw new Error('请输入 HTTP/HTTPS 地址');
    return source || { url: parsed.href, kind: 'unknown' };
  };
  const resolve = async () => {
    setBusy(true);
    setError('');
    try {
      let r = await client.resolve(getSource(), undefined, allowInvalidTls);
      setResult(r);
      while (r.status === 'resolving' && alive.current) {
        await new Promise((r) => setTimeout(r, 750));
        if (!alive.current) return;
        r = await client.resolution(r.id);
        setResult(r);
      }
      if (r.error) throw new Error(`${r.error.message}。${r.error.action}`);
    } catch (e) {
      setError(message(e));
    } finally {
      if (alive.current) setBusy(false);
    }
  };
  const submit = async () => {
    setBusy(true);
    setError('');
    try {
      await client.create({
        source: getSource(),
        request_id: requestId.current,
        format_id: format || undefined,
        allow_invalid_tls: allowInvalidTls,
      });
      done();
    } catch (e) {
      setError(message(e));
    } finally {
      if (alive.current) setBusy(false);
    }
  };
  return (
    <Modal title="新建下载" close={close}>
      <p className={s.subtitle} style={{ marginTop: 0, marginBottom: 22 }}>
        粘贴网页或视频地址。需要登录的视频，请通过 Chrome 插件提交。
      </p>
      <label className={s.field}>
        视频地址
        <input
          autoFocus
          placeholder="https://…"
          value={url}
          onChange={(e) => {
            setUrl(e.target.value);
            setSource(null);
            setResult(null);
            setFormat('');
            setAllowInvalidTls(true);
            requestId.current = crypto.randomUUID();
          }}
          disabled={busy}
        />
      </label>
      {result?.media?.title && <p className={s.subtitle}>{result.media.title}</p>}
      {result?.media?.entries?.map((entry, index) => (
        <button
          className={s.entry}
          key={entry.url + index}
          onClick={() => {
            setSource(entry);
            setUrl(entry.url);
            setResult(null);
            setFormat('');
            setAllowInvalidTls(true);
            requestId.current = crypto.randomUUID();
          }}
        >
          {entry.title || entry.url} <ArrowUpRight size={13} />
        </button>
      ))}
      {!!result?.media?.formats?.length && (
        <label className={s.field} style={{ marginTop: 20 }}>
          清晰度
          <select value={format} onChange={(e) => setFormat(e.target.value)}>
            <option value="">使用默认画质</option>
            {result.media.formats.map((f, i) => (
              <option key={f.id + i} value={f.id}>
                {f.label} · {bytes(f.filesize)}
              </option>
            ))}
          </select>
        </label>
      )}
      <label className={s.checkOption}>
        <span>
          <input
            type="checkbox"
            checked={allowInvalidTls}
            onChange={(e) => {
              setAllowInvalidTls(e.target.checked);
              setResult(null);
              setFormat('');
              requestId.current = crypto.randomUUID();
            }}
          />
          允许无效 HTTPS 证书
        </span>
        <small>默认开启并覆盖关联媒体站点；证书验证会独立执行，结果不影响下载。</small>
      </label>
      {error && (
        <p className={s.errorText} role="alert">
          {error}
        </p>
      )}
      <div className={s.modalActions}>
        <button className={s.secondary} onClick={resolve} disabled={busy || !url}>
          <Search size={14} />
          解析清晰度
        </button>
        <button
          className={s.primary}
          onClick={submit}
          disabled={busy || !url || !!result?.media?.entries?.length}
        >
          {busy ? <Loader2 className={s.spin} size={15} /> : <Download size={15} />}开始下载
        </button>
      </div>
    </Modal>
  );
}

function SettingsPanel({
  client,
  health,
  onError,
}: {
  client: ApiClient;
  health: Health | null;
  onError: (s: string) => void;
}) {
  const [settings, setSettings] = useState<Settings | null>(null);
  const [saved, setSaved] = useState(false);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    client
      .settings()
      .then(setSettings)
      .catch((e) => onError(message(e)));
  }, [client, onError]);
  if (!settings) return <p className={s.subtitle}>正在读取设置…</p>;
  return (
    <form
      className={s.form}
      onSubmit={async (e) => {
        e.preventDefault();
        setBusy(true);
        try {
          setSettings(await client.saveSettings(settings));
          setSaved(true);
        } catch (e) {
          onError(message(e));
        } finally {
          setBusy(false);
        }
      }}
    >
      <h2>下载偏好</h2>
      <label className={s.field}>
        保存目录
        <input
          value={settings.download_dir}
          onChange={(e) => {
            setSettings({ ...settings, download_dir: e.target.value });
            setSaved(false);
          }}
        />
        <small>填写本机绝对路径。修改后对新建任务生效。</small>
      </label>
      <label className={s.field}>
        默认清晰度
        <select
          value={settings.quality}
          onChange={(e) => {
            setSettings({ ...settings, quality: e.target.value as Settings['quality'] });
            setSaved(false);
          }}
        >
          {['best', '1080', '720', '480'].map((q) => (
            <option key={q} value={q}>
              {qualityName(q)}
            </option>
          ))}
        </select>
      </label>
      <label className={s.field}>
        同时下载数量
        <select
          value={settings.concurrency}
          onChange={(e) => {
            setSettings({ ...settings, concurrency: Number(e.target.value) });
            setSaved(false);
          }}
        >
          {[1, 2, 3, 4].map((n) => (
            <option key={n} value={n}>
              {n} 个任务
            </option>
          ))}
        </select>
        <small>其余任务将排队等候。减少数量不会中止已经运行的任务。</small>
      </label>
      <button className={s.primary} disabled={busy}>
        {saved ? <Check size={15} /> : <Settings2 size={15} />} {saved ? '已保存' : '保存设置'}
      </button>
      <div className={s.divider} />
      <h2>本地服务</h2>
      {[
        ['FFmpeg', health?.ffmpeg],
        ['ffprobe', health?.ffprobe],
        ['Node.js', health?.node],
      ].map(([label, ok]) => (
        <div className={s.row} key={String(label)}>
          <span>{label}</span>
          <strong>{ok ? '已就绪' : '未找到'}</strong>
        </div>
      ))}
      <p className={s.subtitle}>
        <Clock3 size={12} /> 服务运行期间，关闭管理页面不会停止下载。
      </p>
    </form>
  );
}
