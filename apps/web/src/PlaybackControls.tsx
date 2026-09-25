import { useEffect, useState } from 'react';
import { Loader2, Play, RotateCcw, X } from 'lucide-react';
import { ApiClient, type Task } from '@ergou/contracts';
import s from './App.module.css';

export const playbackLabels: Record<Task['playback_status'], string> = {
  pending: '尚未检查格式',
  checking: '检查格式…',
  ready_original: '可直接播放',
  remuxing: '准备播放',
  transcode_required: '需要兼容播放版本',
  transcode_queued: '等待转码…',
  transcoding: '转码中',
  ready_compatible: '兼容版本已就绪',
  canceled: '播放准备已取消',
  interrupted: '播放准备已中断',
  failed: '播放准备失败',
};
export const playbackReady = (task: Task) =>
  ['ready_original', 'ready_compatible'].includes(task.playback_status);
export const playbackActive = (task: Task) =>
  ['checking', 'remuxing', 'transcode_queued', 'transcoding'].includes(task.playback_status);

export function PlaybackControls({
  task,
  client,
  onPlay,
  onTask,
  onError,
  forceTranscode = false,
}: {
  task: Task;
  client: ApiClient;
  onPlay: (source: 'preferred' | 'original') => void;
  onTask?: (task: Task) => void;
  onError: (message: string) => void;
  forceTranscode?: boolean;
}) {
  const [busy, setBusy] = useState(false);
  const [confirm, setConfirm] = useState(false);
  const [showRemux, setShowRemux] = useState(false);
  useEffect(() => {
    setShowRemux(false);
    if (task.playback_status !== 'remuxing') return;
    const timer = setTimeout(() => setShowRemux(true), 500);
    return () => clearTimeout(timer);
  }, [task.playback_status]);
  if (task.status !== 'completed') return null;
  const run = async (operation: 'recommended' | 'transcode' | 'cancel') => {
    setBusy(true);
    setConfirm(false);
    try {
      const updated =
        operation === 'cancel'
          ? await client.cancelPlayback(task.id)
          : await client.preparePlayback(task.id, operation);
      onTask?.(updated);
    } catch (error) {
      onError(error instanceof Error ? error.message : '播放准备失败');
    } finally {
      setBusy(false);
    }
  };
  const active = playbackActive(task);
  const ready = playbackReady(task);
  const percent = task.playback_progress;
  const progressing =
    task.playback_status === 'transcoding' || (task.playback_status === 'remuxing' && showRemux);
  return (
    <div className={s.playbackControls}>
      <div className={s.progressText} aria-live="polite">
        <span>
          {playbackLabels[task.playback_status]}
          {progressing && percent != null ? ` ${Math.round(percent)}%` : ''}
          {task.playback_status === 'ready_compatible' &&
            ` · ${task.playback_method === 'remux' ? '无损换封装' : '转码'}`}
        </span>
        {progressing && (
          <span>
            {task.playback_speed ? `${task.playback_speed.toFixed(1)}× · ` : ''}
            {task.playback_eta == null ? '计算剩余时间…' : `剩余 ${Math.ceil(task.playback_eta)} 秒`}
          </span>
        )}
      </div>
      {progressing && (
        <div
          className={s.progress}
          role="progressbar"
          aria-label="播放处理进度"
          aria-valuenow={percent ?? undefined}
          aria-valuemin={0}
          aria-valuemax={100}
        >
          <div
            className={`${s.progressBar} ${percent == null ? s.indeterminate : ''}`}
            style={percent != null ? { width: `${percent}%` } : undefined}
          />
        </div>
      )}
      {task.playback_error && (
        <p className={s.errorText} role="alert">
          {task.playback_error.message}。{task.playback_error.action}
        </p>
      )}
      <div className={s.playbackButtons}>
        {ready && (
          <button className={s.secondary} onClick={() => onPlay('preferred')}>
            <Play size={14} />
            播放视频
          </button>
        )}
        {task.playback_status === 'pending' && (
          <button className={s.secondary} onClick={() => onPlay('preferred')}>
            <Play size={14} />
            检查并播放
          </button>
        )}
        {active && (
          <>
            <button className={s.secondary} disabled>
              <Loader2 size={14} className={s.spin} />
              {playbackLabels[task.playback_status]}
            </button>
            <button className={s.secondary} disabled={busy} onClick={() => void run('cancel')}>
              <X size={14} />
              取消准备
            </button>
          </>
        )}
        {(task.playback_status === 'transcode_required' || forceTranscode) && !active && (
          <button className={s.primary} disabled={busy} onClick={() => setConfirm(true)}>
            生成兼容播放版本
          </button>
        )}
        {['failed', 'canceled', 'interrupted'].includes(task.playback_status) && (
          <button className={s.secondary} disabled={busy} onClick={() => void run('recommended')}>
            <RotateCcw size={14} />
            重新准备
          </button>
        )}
        {(!ready || task.playback_status === 'ready_compatible') && (
          <button className={s.secondary} onClick={() => onPlay('original')}>
            尝试直接播放
          </button>
        )}
      </div>
      {confirm && (
        <div className={s.overlay}>
          <section className={s.modal} role="dialog" aria-modal="true" aria-label="生成兼容播放版本">
            <h2>生成兼容播放版本</h2>
            <p>{task.title}</p>
            <p className={s.subtitle}>
              保留原视频，按原分辨率生成 H.264 + AAC MP4。优先使用显卡，失败时自动改用
              CPU；会额外占用磁盘空间。
            </p>
            <div className={s.modalActions}>
              <button className={s.secondary} onClick={() => setConfirm(false)}>
                暂不生成
              </button>
              <button className={s.primary} onClick={() => void run('transcode')}>
                开始生成
              </button>
            </div>
          </section>
        </div>
      )}
    </div>
  );
}
