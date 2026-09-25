import type { components } from './api';
export type Source = Pick<components['schemas']['Source'], 'url'> &
  Partial<Omit<components['schemas']['Source'], 'url'>>;
export type SessionContext = components['schemas']['SessionContext'];
export type Task = components['schemas']['TaskView'];
export type Resolution = components['schemas']['Resolution'];
export type Settings = components['schemas']['Settings'];
export type Health = components['schemas']['Health'];
export type CreateTask = Omit<components['schemas']['CreateTask'], 'source' | 'allow_invalid_tls'> & {
  source: Source;
  allow_invalid_tls?: boolean;
};
export type TaskEvent = components['schemas']['TaskEvent'];
export type TaskPage = components['schemas']['TaskPage'];

export const DEFAULT_SERVICE = 'http://127.0.0.1:17890';
export const labels: Record<Task['status'], string> = {
  queued: '排队中',
  resolving: '解析中',
  downloading: '下载中',
  merging: '合并与检查',
  completed: '已完成',
  failed: '失败',
  canceled: '已取消',
  interrupted: '已中断',
};
export const isActive = (status: Task['status']) =>
  ['queued', 'resolving', 'downloading', 'merging'].includes(status);
export const canRetry = (status: Task['status']) => ['failed', 'canceled', 'interrupted'].includes(status);
export function bytes(value: number | null | undefined): string {
  if (value == null) return '未知';
  if (value < 1024) return `${Math.round(value)} B`;
  const i = Math.min(3, Math.floor(Math.log(value) / Math.log(1024)));
  return `${(value / 1024 ** i).toFixed(1)} ${['B', 'KB', 'MB', 'GB'][i]}`;
}
export function validateService(value: string): string {
  const u = new URL(value);
  if (u.protocol !== 'http:' || !['127.0.0.1', 'localhost'].includes(u.hostname) || u.username || u.password)
    throw new Error('服务地址必须是本机的 HTTP 地址');
  return u.origin;
}
export class ApiClient {
  constructor(
    public base: string,
    public token: string,
  ) {}
  async request<T>(path: string, method = 'GET', body?: unknown): Promise<T> {
    const response = await fetch(`${this.base}/api/v1${path}`, {
      method,
      headers: {
        Authorization: `Bearer ${this.token}`,
        ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}),
      },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: AbortSignal.timeout(15000),
    });
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      const detail = data.detail;
      const message = typeof detail === 'string' ? detail : detail?.message;
      throw new Error(
        response.status === 401 ? '访问令牌无效，请重新连接' : message || `请求失败 (${response.status})`,
      );
    }
    return response.json();
  }
  health() {
    return this.request<Health>('/health');
  }
  tasks(query = '') {
    return this.request<TaskPage>(`/tasks${query}`);
  }
  task(id: string) {
    return this.request<Task>(`/tasks/${encodeURIComponent(id)}`);
  }
  create(body: CreateTask) {
    return this.request<Task>('/tasks', 'POST', body);
  }
  cancel(id: string) {
    return this.request<Task>(`/tasks/${encodeURIComponent(id)}/cancel`, 'POST');
  }
  retry(id: string, body: { source?: Source; context?: SessionContext; allow_invalid_tls?: boolean } = {}) {
    return this.request<Task>(`/tasks/${encodeURIComponent(id)}/retry`, 'POST', body);
  }
  resolve(source: Source, context?: SessionContext, allowInvalidTls?: boolean) {
    return this.request<Resolution>('/resolutions', 'POST', {
      source,
      context,
      allow_invalid_tls: allowInvalidTls,
    });
  }
  resolution(id: string) {
    return this.request<Resolution>(`/resolutions/${encodeURIComponent(id)}`);
  }
  settings() {
    return this.request<Settings>('/settings');
  }
  saveSettings(settings: Settings) {
    return this.request<Settings>('/settings', 'PATCH', settings);
  }
  fileAction(id: string, action: 'open' | 'reveal') {
    return this.request(`/tasks/${encodeURIComponent(id)}/${action}`, 'POST');
  }
  deleteTask(id: string, deleteFile = false) {
    return this.request<{ ok: boolean }>(
      `/tasks/${encodeURIComponent(id)}?delete_file=${deleteFile}`,
      'DELETE',
    );
  }
}
