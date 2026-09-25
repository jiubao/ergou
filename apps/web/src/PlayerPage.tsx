import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { ArrowLeft, ExternalLink, Film, FolderOpen, Loader2 } from 'lucide-react';
import { ApiClient, bytes, type Task } from '@ergou/contracts';
import { PlaybackControls, playbackActive, playbackReady } from './PlaybackControls';
import s from './App.module.css';

const message = (e: unknown) => (e instanceof Error ? e.message : '播放失败');
type SavedPlayback = { identity: string; currentTime: number };

export function PlayerPage({
  client,
  taskId,
  back,
}: {
  client: ApiClient;
  taskId: string;
  back: () => void;
}) {
  const [task, setTask] = useState<Task | null>(null);
  const [media, setMedia] = useState<{ url: string; identity: string } | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [mediaFailed, setMediaFailed] = useState(false);
  const video = useRef<HTMLVideoElement>(null);
  const selected = useRef<'preferred' | 'original'>(
    location.hash.includes('source=original') ? 'original' : 'preferred',
  );
  const retried = useRef(false);
  const lastSaved = useRef(0);
  const loadedIdentity = useRef('');
  const requestVersion = useRef(0);
  const progressKey = `ergou.playback.${taskId}`;

  const requestMedia = useCallback(
    async (source: 'preferred' | 'original' = selected.current) => {
      selected.current = source;
      const version = ++requestVersion.current;
      const session = await client.createPlaybackSession(taskId, source);
      if (version !== requestVersion.current) return;
      const url = new URL(session.url, client.base);
      url.searchParams.set('session', session.expires_at);
      loadedIdentity.current = session.playback_identity;
      setMedia({ url: url.href, identity: session.playback_identity });
      setMediaFailed(false);
      setError('');
    },
    [client, taskId],
  );

  useEffect(() => {
    let active = true;
    let fetching = false;
    let first = true;
    let wasProcessing = false;
    const refresh = async () => {
      if (fetching) return;
      fetching = true;
      try {
        let next = await client.task(taskId);
        if (!active) return;
        if (next.status !== 'completed' || !next.output_path) throw new Error('视频尚未下载完成');
        if (first && next.playback_status === 'pending') next = await client.preparePlayback(taskId);
        if (!active) return;
        setTask(next);
        const finished = wasProcessing && playbackReady(next);
        if (finished) selected.current = 'preferred';
        if (first && selected.current === 'original') await requestMedia('original');
        else if (
          playbackReady(next) &&
          (first ||
            finished ||
            (selected.current === 'preferred' && loadedIdentity.current !== next.playback_identity))
        ) {
          retried.current = false;
          await requestMedia();
        }
        wasProcessing = playbackActive(next);
        first = false;
      } catch (reason) {
        if (active) setError(message(reason));
        first = false;
      } finally {
        fetching = false;
        if (active) setLoading(false);
      }
    };
    void refresh();
    // Player has its own snapshot recovery even when its task is not on the current list page.
    const timer = setInterval(() => void refresh(), 1000);
    return () => {
      active = false;
      ++requestVersion.current;
      clearInterval(timer);
    };
  }, [client, taskId, requestMedia]);

  const saveProgress = useCallback(
    (element = video.current) => {
      if (!media || !element || !Number.isFinite(element.currentTime)) return;
      if (element.ended) localStorage.removeItem(progressKey);
      else if (element.currentTime >= 5) {
        localStorage.setItem(
          progressKey,
          JSON.stringify({
            identity: media.identity,
            currentTime: element.currentTime,
          } satisfies SavedPlayback),
        );
      }
    },
    [media, progressKey],
  );
  useLayoutEffect(() => {
    const element = video.current;
    // Keep the actual old element and identity while React removes or replaces the player.
    return () => {
      saveProgress(element);
      if (element) {
        element.pause();
        element.removeAttribute('src');
        element.load();
      }
    };
  }, [saveProgress]);
  const saveRef = useRef(saveProgress);
  saveRef.current = saveProgress;
  useEffect(() => {
    const save = () => saveRef.current();
    addEventListener('pagehide', save);
    const timer = setInterval(save, 5000);
    return () => {
      save();
      clearInterval(timer);
      removeEventListener('pagehide', save);
    };
  }, []);

  const play = (source: 'preferred' | 'original') => {
    saveProgress();
    retried.current = false;
    void requestMedia(source).catch((e) => setError(message(e)));
  };
  const fileAction = (operation: 'open' | 'reveal') => {
    void client.fileAction(taskId, operation).catch((e) => setError(message(e)));
  };
  return (
    <section className={s.playerPage} aria-label="视频播放页">
      <div className={s.playerToolbar}>
        <button
          className={s.secondary}
          onClick={() => {
            saveProgress();
            back();
          }}
        >
          <ArrowLeft size={15} />
          返回任务列表
        </button>
        {task && (
          <div className={s.playerActions}>
            <button className={s.secondary} onClick={() => fileAction('open')}>
              <ExternalLink size={14} />
              系统播放器打开
            </button>
            <button className={s.secondary} onClick={() => fileAction('reveal')}>
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
            {new URL(task.source.page_url || task.source.url).hostname} ·{' '}
            {task.height ? `${task.height}p` : '原画质'} · {bytes(task.total_bytes)}
          </p>
        </div>
      )}
      {task && (
        <PlaybackControls
          task={task}
          client={client}
          onPlay={play}
          onTask={(next) => {
            setTask(next);
            if (playbackActive(next) || next.playback_status === 'canceled') {
              saveProgress();
              setMedia(null);
              loadedIdentity.current = '';
              selected.current = 'preferred';
            }
          }}
          onError={setError}
          forceTranscode={mediaFailed}
        />
      )}
      <div className={s.playerFrame}>
        {loading && (
          <div className={s.playerPlaceholder}>
            <Loader2 className={s.spin} size={24} />
            正在准备本地视频…
          </div>
        )}
        {!loading && media && (
          <video
            key={media.url}
            ref={video}
            aria-label={task ? `播放 ${task.title}` : '本地视频播放器'}
            controls
            playsInline
            preload="metadata"
            src={media.url}
            onLoadedMetadata={(event) => {
              setMediaFailed(false);
              try {
                const saved = JSON.parse(localStorage.getItem(progressKey) || 'null') as SavedPlayback | null;
                if (
                  saved?.identity === media.identity &&
                  saved.currentTime >= 5 &&
                  saved.currentTime < event.currentTarget.duration - 10
                )
                  event.currentTarget.currentTime = saved.currentTime;
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
            onPause={() => saveProgress()}
            onEnded={() => localStorage.removeItem(progressKey)}
            onError={() => {
              if (!retried.current) {
                retried.current = true;
                void requestMedia().catch((reason) => {
                  setError(message(reason));
                  setMediaFailed(true);
                });
              } else setMediaFailed(true);
            }}
          />
        )}
        {!loading && !media && (
          <div className={s.playerPlaceholder}>
            <Film size={30} />
            {task && playbackActive(task) ? '正在准备播放，完成后将自动载入视频' : '请选择播放或准备兼容版本'}
          </div>
        )}
      </div>
      {mediaFailed && (
        <div className={s.alert} role="alert">
          当前浏览器无法播放此文件的封装或编码。可以生成兼容版本，或使用系统播放器打开。
        </div>
      )}
      {error && (
        <div className={s.alert} role="alert">
          {error}
        </div>
      )}
      {error && task && !playbackActive(task) && (
        <button
          className={s.secondary}
          onClick={() => {
            void client
              .preparePlayback(taskId)
              .then((next) => {
                setTask(next);
                setError('');
                loadedIdentity.current = '';
                selected.current = 'preferred';
              })
              .catch((e) => setError(message(e)));
          }}
        >
          重新检查播放文件
        </button>
      )}
      <p className={s.subtitle}>观看位置保存在当前浏览器中；播放结束后会自动清除。</p>
    </section>
  );
}
