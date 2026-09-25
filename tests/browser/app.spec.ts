import { test, expect, chromium, type BrowserContext } from '@playwright/test';
import path from 'node:path';
import fs from 'node:fs';
import type { Task } from '@ergou/contracts';

const SERVICE = 'http://127.0.0.1:17894';
const MEDIA = 'http://127.0.0.1:17895';
const TOKEN = 'ergou-browser-test-token-ephemeral';
let context: BrowserContext;
let extensionId: string;

test.beforeAll(async () => {
  context = await chromium.launchPersistentContext('', {
    channel: 'chromium',
    headless: true,
    viewport: { width: 1440, height: 1000 },
    executablePath: process.env.ERGOU_CHROMIUM_EXE || undefined,
    args: [
      `--disable-extensions-except=${path.resolve('apps/extension/.output/chrome-mv3')}`,
      `--load-extension=${path.resolve('apps/extension/.output/chrome-mv3')}`,
    ],
  });
  let [worker] = context.serviceWorkers();
  if (!worker) worker = await context.waitForEvent('serviceworker');
  extensionId = new URL(worker.url()).hostname;
});
test.afterAll(async () => {
  await context?.close();
});

test.beforeEach(async ({}, info) => {
  if (info.title === 'web connects, downloads a video and retains history after reload') return;
  await context.addInitScript(
    ({ service, token }) => {
      if (location.origin === service) localStorage.setItem('ergou.token', token);
    },
    { service: SERVICE, token: TOKEN },
  );
});

test('web connects, downloads a video and retains history after reload', async () => {
  const page = await context.newPage();
  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(e.message));
  await page.goto(SERVICE);
  await page.getByLabel('访问令牌').fill(TOKEN);
  await page.getByRole('button', { name: '连接工作空间' }).click();
  await expect(page.getByRole('heading', { name: '下载管理', exact: true })).toBeVisible();
  await page.getByRole('button', { name: '新建下载', exact: true }).click();
  await page.getByLabel('视频地址').fill(`${MEDIA}/sample.mp4`);
  await page.getByRole('button', { name: '开始下载', exact: true }).click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(page.getByRole('article').first()).toContainText('已完成');
  await expect(page.getByRole('article').first()).toContainText('180p');
  await page.reload();
  await expect(page.getByRole('article').first()).toContainText('已完成');
  await page.getByRole('button', { name: '偏好设置', exact: true }).click();
  await page.getByLabel('默认清晰度').selectOption('720');
  await page.getByRole('button', { name: '保存设置', exact: true }).click();
  await expect(page.getByRole('button', { name: '已保存', exact: true })).toBeVisible();
  await page.reload();
  await page.getByRole('button', { name: '偏好设置', exact: true }).click();
  await expect(page.getByLabel('默认清晰度')).toHaveValue('720');
  await page.getByRole('button', { name: '下载管理', exact: true }).click();
  const taskResponse = await context.request.get(`${SERVICE}/api/v1/tasks?limit=50`, {
    headers: { Authorization: `Bearer ${TOKEN}` },
  });
  const downloadedTask = (await taskResponse.json()).items.find(
    (item: { source: { url: string } }) => item.source.url === `${MEDIA}/sample.mp4`,
  );
  expect(downloadedTask).toBeTruthy();
  await expect(page.getByRole('button', { name: '任务详情' }).locator('svg')).toHaveClass(/lucide-eye/);
  fs.mkdirSync('.local/screenshots', { recursive: true });
  await page.screenshot({ path: '.local/screenshots/web-desktop.png', fullPage: true });
  await page.setViewportSize({ width: 700, height: 1000 });
  await page.screenshot({ path: '.local/screenshots/web-narrow.png', fullPage: true });
  await page.getByRole('button', { name: '删除任务' }).click();
  await expect(page.getByRole('dialog', { name: '删除任务' })).toContainText(downloadedTask.title);
  await expect(page.getByLabel('同时删除视频文件')).not.toBeChecked();
  await page.getByRole('button', { name: '确认删除' }).click();
  await expect(page.getByRole('article')).toHaveCount(0);
  expect(fs.existsSync(downloadedTask.output_path)).toBe(true);
  expect(errors).toEqual([]);
  await page.close();
});

test('web resolution permits quality selection before downloading HLS', async () => {
  const page = await context.newPage();
  const sourceUrl = `${MEDIA}/multi.m3u8?tls-option-browser-test=1`;
  await page.goto(SERVICE);
  await page.getByRole('button', { name: '新建下载', exact: true }).click();
  await page.getByLabel('视频地址').fill(sourceUrl);
  await page.getByRole('button', { name: '解析清晰度', exact: true }).click();
  await expect(page.getByRole('combobox', { name: '清晰度', exact: true })).toBeVisible();
  await page.getByLabel('允许无效 HTTPS 证书').check();
  await page.getByRole('button', { name: '开始下载', exact: true }).click();
  await expect
    .poll(async () => {
      const response = await context.request.get(`${SERVICE}/api/v1/tasks?limit=50`, {
        headers: { Authorization: `Bearer ${TOKEN}` },
      });
      const task = (await response.json()).items.find(
        (item: { source: { url: string } }) => item.source.url === sourceUrl,
      );
      return { status: task?.status, allowInvalidTls: task?.allow_invalid_tls };
    })
    .toEqual({ status: 'completed', allowInvalidTls: true });
  await page.reload();
  await page.getByRole('article').first().getByRole('button').first().click();
  await expect(page.getByRole('dialog')).toContainText('HTTPS 证书兼容');
  await expect(page.getByRole('dialog')).toContainText('已开启');
  const response = await context.request.get(`${SERVICE}/api/v1/tasks?limit=50`, {
    headers: { Authorization: `Bearer ${TOKEN}` },
  });
  const downloadedTask = (await response.json()).items.find(
    (item: { source: { url: string } }) => item.source.url === sourceUrl,
  );
  await page.getByRole('button', { name: '关闭' }).click();
  await page.getByRole('button', { name: '删除任务' }).click();
  await page.getByLabel('同时删除视频文件').check();
  await page.getByRole('button', { name: '确认删除' }).click();
  await expect(page.getByRole('article')).toHaveCount(0);
  expect(fs.existsSync(downloadedTask.output_path)).toBe(false);
  await page.close();
});

test('web plays a local video, restores progress and explains unsupported media', async () => {
  const sourceUrl = `${MEDIA}/playback.mp4?player-browser-test=1`;
  const created = await context.request.post(`${SERVICE}/api/v1/tasks`, {
    headers: { Authorization: `Bearer ${TOKEN}` },
    data: {
      request_id: crypto.randomUUID(),
      source: { url: sourceUrl, title: '本地播放与进度验证', kind: 'direct' },
    },
  });
  expect(created.ok()).toBe(true);
  const taskId = (await created.json()).id;
  await expect
    .poll(async () => {
      const response = await context.request.get(`${SERVICE}/api/v1/tasks/${taskId}`, {
        headers: { Authorization: `Bearer ${TOKEN}` },
      });
      const current = await response.json();
      return current.status === 'completed' ? current : null;
    })
    .not.toBeNull();
  const response = await context.request.get(`${SERVICE}/api/v1/tasks/${taskId}`, {
    headers: { Authorization: `Bearer ${TOKEN}` },
  });
  const completed = await response.json();

  const page = await context.newPage();
  await page.goto(SERVICE);
  if (await page.getByLabel('访问令牌').isVisible()) {
    await page.getByLabel('访问令牌').fill(TOKEN);
    await page.getByRole('button', { name: '连接工作空间' }).click();
  }
  const card = page.getByRole('article').filter({ hasText: '本地播放与进度验证' });
  await card.getByRole('button', { name: '播放视频' }).click();
  await expect(page).toHaveURL(new RegExp(`#\/play\/${taskId}$`));
  const player = page.getByLabel('播放 本地播放与进度验证');
  await expect(player).toBeVisible();
  await expect.poll(() => player.evaluate((video: HTMLVideoElement) => video.readyState)).toBeGreaterThan(0);
  await player.evaluate(async (video: HTMLVideoElement) => {
    video.currentTime = 7;
    await new Promise<void>((resolve) => video.addEventListener('seeked', () => resolve(), { once: true }));
    video.pause();
  });
  await expect
    .poll(() =>
      page.evaluate((id) => {
        const value = JSON.parse(localStorage.getItem(`ergou.playback.${id}`) || 'null');
        return value?.currentTime;
      }, taskId),
    )
    .toBeGreaterThanOrEqual(6.5);

  await page.reload();
  await expect
    .poll(() => player.evaluate((video: HTMLVideoElement) => video.currentTime))
    .toBeGreaterThanOrEqual(6.5);
  await page.goBack();
  await expect(page.getByRole('heading', { name: '下载管理', exact: true })).toBeVisible();
  await card.getByRole('button', { name: '任务详情' }).click();
  await expect(page.getByRole('dialog').getByRole('button', { name: '播放视频' })).toBeVisible();
  await page.getByRole('button', { name: '关闭' }).click();

  await page.goto('about:blank');
  fs.writeFileSync(completed.output_path, 'not a supported media file');
  await page.goto(SERVICE);
  await card.getByRole('button', { name: '播放视频' }).click();
  await expect(page.getByRole('alert')).toContainText('原视频文件已发生变化');
  await page.goto(`${SERVICE}/#/play/${taskId}?source=original`);
  await expect(page.getByRole('alert')).toContainText('当前浏览器无法播放此文件');
  await expect(page.getByRole('button', { name: '系统播放器打开' })).toBeVisible();
  await page.close();
  await expect
    .poll(async () =>
      (
        await context.request.delete(`${SERVICE}/api/v1/tasks/${taskId}?delete_file=true`, {
          headers: { Authorization: `Bearer ${TOKEN}` },
        })
      ).status(),
    )
    .toBe(200);
});

test('web plays a downloaded WebM file natively', async () => {
  const sourceUrl = `${MEDIA}/sample.webm?webm-player-browser-test=1`;
  const created = await context.request.post(`${SERVICE}/api/v1/tasks`, {
    headers: { Authorization: `Bearer ${TOKEN}` },
    data: {
      request_id: crypto.randomUUID(),
      source: { url: sourceUrl, title: 'WebM 本地播放验证', kind: 'direct' },
    },
  });
  const taskId = (await created.json()).id;
  await expect
    .poll(async () => {
      const response = await context.request.get(`${SERVICE}/api/v1/tasks/${taskId}`, {
        headers: { Authorization: `Bearer ${TOKEN}` },
      });
      return (await response.json()).status;
    })
    .toBe('completed');

  const page = await context.newPage();
  await page.goto(SERVICE);
  if (await page.getByLabel('访问令牌').isVisible()) {
    await page.getByLabel('访问令牌').fill(TOKEN);
    await page.getByRole('button', { name: '连接工作空间' }).click();
  }
  const card = page.getByRole('article').filter({ hasText: 'WebM 本地播放验证' });
  await card.getByRole('button', { name: '播放视频' }).click();
  const player = page.getByLabel('播放 WebM 本地播放验证');
  await expect.poll(() => player.evaluate((video: HTMLVideoElement) => video.readyState)).toBeGreaterThan(0);
  expect(await player.evaluate((video: HTMLVideoElement) => video.error)).toBeNull();
  await page.close();
  const removed = await context.request.delete(`${SERVICE}/api/v1/tasks/${taskId}?delete_file=true`, {
    headers: { Authorization: `Bearer ${TOKEN}` },
  });
  expect(removed.ok()).toBe(true);
});

test('web deletion stops an active task before removing it', async () => {
  const page = await context.newPage();
  const sourceUrl = `${MEDIA}/slow.mp4?active-delete-browser-test=1`;
  await page.goto(SERVICE);
  await page.getByRole('button', { name: '新建下载', exact: true }).click();
  await page.getByLabel('视频地址').fill(sourceUrl);
  await page.getByRole('button', { name: '开始下载', exact: true }).click();
  await expect(page.getByRole('article').first()).toBeVisible();
  await page.getByRole('button', { name: '删除任务' }).click();
  await expect(page.getByRole('dialog', { name: '删除任务' })).toContainText('会先停止下载');
  await page.getByRole('button', { name: '确认删除' }).click();
  await expect(page.getByRole('article')).toHaveCount(0);
  const response = await context.request.get(`${SERVICE}/api/v1/tasks?limit=50`, {
    headers: { Authorization: `Bearer ${TOKEN}` },
  });
  expect(
    (await response.json()).items.some((item: { source: { url: string } }) => item.source.url === sourceUrl),
  ).toBe(false);
  await page.close();
});

test('extension shows connection fields before first use', async () => {
  const worker = context.serviceWorkers().find((w) => w.url().includes(extensionId))!;
  await worker.evaluate(async () => chrome.storage.local.remove(['service', 'token']));
  const popup = await context.newPage();
  await popup.goto(`chrome-extension://${extensionId}/popup.html`);
  await expect(popup.getByRole('heading', { name: '连接本地服务' })).toBeVisible();
  await expect(popup.getByLabel('本地服务地址')).toHaveValue('http://127.0.0.1:17890');
  await expect(popup.getByLabel('访问令牌')).toBeVisible();
  await expect(popup.getByRole('button', { name: '保存并连接' })).toBeVisible();
  await popup.close();
});

test('extension discovers direct, dynamic, frame and manifest resources, then submits download', async () => {
  const watch = await context.newPage();
  await watch.goto(`${MEDIA}/watch.html`);
  await watch.locator('#add-video').click();
  await watch.locator('#load-hls').click();
  const worker = context.serviceWorkers().find((w) => w.url().includes(extensionId))!;
  await worker.evaluate(
    async ({ service, token }) => {
      await chrome.storage.local.set({ service, token });
    },
    { service: SERVICE, token: TOKEN },
  );
  // Keep the video tab selected. The popup is inspected as an extension page in a second tab.
  const tabId = await worker.evaluate(async (media) => {
    const tabs = await chrome.tabs.query({});
    return tabs.find((t) => t.url?.includes(media + '/watch.html'))!.id!;
  }, MEDIA);
  await expect
    .poll(async () =>
      worker.evaluate(async (id) => {
        const state = (await chrome.storage.session.get(`tab:${id}`))[`tab:${id}`];
        return state?.candidates?.length || 0;
      }, tabId),
    )
    .toBeGreaterThanOrEqual(4);
  const state = await worker.evaluate(
    async (id) => (await chrome.storage.session.get(`tab:${id}`))[`tab:${id}`],
    tabId,
  );
  expect(state.candidates.some((c: { source: { kind: string } }) => c.source.kind === 'hls')).toBe(true);
  expect(state.candidates.some((c: { frameId: number }) => c.frameId > 0)).toBe(true);
  await expect
    .poll(async () =>
      worker.evaluate(async (id) => {
        const state = (await chrome.storage.session.get(`tab:${id}`))[`tab:${id}`];
        return state?.candidates.filter((c: { source: { kind: string } }) => c.source.kind === 'hls').length;
      }, tabId),
    )
    .toBe(1);
  // Chrome's activeTab query in the popup must point to the original video tab.
  const popup = await context.newPage();
  await popup.goto(`chrome-extension://${extensionId}/popup.html`);
  await watch.bringToFront();
  await popup.reload();
  await expect(popup.getByRole('heading', { name: 'MP4 完整下载验证' })).toBeVisible();
  await expect(popup.locator('.status')).toContainText('本地服务已连接');
  const card = popup
    .locator('article')
    .filter({ has: popup.getByRole('heading', { name: 'MP4 完整下载验证' }) });
  await card.getByRole('button', { name: '选择清晰度', exact: true }).click();
  await expect(popup.getByLabel('允许无效 HTTPS 证书')).toBeChecked();
  await expect(popup.getByRole('button', { name: '下载所选画质' })).toBeVisible();
  await popup.getByRole('button', { name: '下载所选画质' }).click();
  await expect(popup.locator('.notice')).toContainText('已提交');
  await expect
    .poll(async () => {
      const response = await context.request.get(
        `${SERVICE}/api/v1/tasks?search=${encodeURIComponent('MP4 完整下载验证')}`,
        { headers: { Authorization: `Bearer ${TOKEN}` } },
      );
      const task = (await response.json()).items[0];
      return { status: task?.status, allowInvalidTls: task?.allow_invalid_tls };
    })
    .toEqual({ status: 'completed', allowInvalidTls: true });
  await popup.setViewportSize({ width: 390, height: 650 });
  await popup.screenshot({ path: '.local/screenshots/extension.png', fullPage: true });
  await popup.close();
  await watch.evaluate(() => {
    document.querySelectorAll('video,iframe').forEach((element) => element.remove());
    history.pushState({}, '', '/watch.html?next');
  });
  await expect
    .poll(async () =>
      worker.evaluate(async (id) => {
        const state = (await chrome.storage.session.get(`tab:${id}`))[`tab:${id}`];
        return { next: state?.pageUrl.endsWith('?next'), count: state?.candidates.length };
      }, tabId),
    )
    .toEqual({ next: true, count: 0 });
  await watch.close();
});

test('extension forwards only the selected task session to download a protected video', async () => {
  const unauthorized = await context.request.get(`${MEDIA}/auth/sample.mp4`);
  expect(unauthorized.status()).toBe(403);
  await context.addCookies([{ name: 'session', value: 'browser-valid', url: MEDIA, httpOnly: true }]);
  const watch = await context.newPage();
  await watch.goto(`${MEDIA}/auth.html`);
  await expect
    .poll(() => watch.locator('video').evaluate((video: HTMLVideoElement) => video.readyState))
    .toBeGreaterThan(0);
  const popup = await context.newPage();
  await popup.goto(`chrome-extension://${extensionId}/popup.html`);
  await watch.bringToFront();
  await popup.reload();
  const card = popup
    .locator('article')
    .filter({ has: popup.getByRole('heading', { name: '登录态完整下载验证' }) });
  await card.getByRole('button', { name: '下载', exact: true }).click();
  await expect(popup.locator('.notice')).toContainText('已提交');
  await expect
    .poll(async () => {
      const response = await context.request.get(
        `${SERVICE}/api/v1/tasks?search=${encodeURIComponent('登录态完整下载验证')}`,
        {
          headers: { Authorization: `Bearer ${TOKEN}` },
        },
      );
      const task = (await response.json()).items[0];
      return {
        status: task?.status,
        session: task?.source.requires_session,
        allowInvalidTls: task?.allow_invalid_tls,
      };
    })
    .toEqual({ status: 'completed', session: true, allowInvalidTls: true });
  await popup.close();
  await watch.close();
  await context.clearCookies();
});

async function downloadForPlayback(file: string, title: string): Promise<Task> {
  const response = await context.request.post(`${SERVICE}/api/v1/tasks`, {
    headers: { Authorization: `Bearer ${TOKEN}` },
    data: { request_id: crypto.randomUUID(), source: { url: `${MEDIA}/${file}`, kind: 'direct', title } },
  });
  expect(response.ok()).toBe(true);
  const { id } = await response.json();
  let task: Task;
  await expect
    .poll(
      async () => {
        const result = await context.request.get(`${SERVICE}/api/v1/tasks/${id}`, {
          headers: { Authorization: `Bearer ${TOKEN}` },
        });
        task = await result.json();
        return ['ready_original', 'ready_compatible', 'transcode_required'].includes(task.playback_status);
      },
      { timeout: 45_000 },
    )
    .toBe(true);
  return task!;
}

test('automatic remux preserves original, plays MP4 ranges and removes internal copy on deletion', async () => {
  const task = await downloadForPlayback('remux.mkv', '自动换封装验证');
  expect(task.playback_status).toBe('ready_compatible');
  expect(task.playback_method).toBe('remux');
  const page = await context.newPage();
  await page.goto(SERVICE);
  const card = page.getByRole('article').filter({ hasText: task.title });
  await expect(card).toContainText('无损换封装');
  await card.getByRole('button', { name: '播放视频', exact: true }).click();
  const player = page.getByLabel(`播放 ${task.title}`);
  await expect.poll(() => player.evaluate((v: HTMLVideoElement) => v.readyState)).toBeGreaterThan(0);
  await player.evaluate(async (v: HTMLVideoElement) => {
    await v.play();
    v.currentTime = 3;
  });
  await expect.poll(() => player.evaluate((v: HTMLVideoElement) => v.currentTime)).toBeGreaterThanOrEqual(3);
  const response = await context.request.get(`${SERVICE}/api/v1/playback/${task.id}`, {
    headers: { Range: 'bytes=0-99' },
  });
  expect(response.status()).toBe(206);
  expect(response.headers()['content-type']).toBe('video/mp4');
  await page.getByRole('button', { name: '返回任务列表' }).click();
  await card.getByRole('button', { name: '删除任务' }).click();
  await expect(page.getByLabel('同时删除视频文件')).not.toBeChecked();
  await page.getByRole('button', { name: '确认删除' }).click();
  await expect(card).toHaveCount(0);
  expect(fs.existsSync(task.output_path!)).toBe(true);
  expect(fs.existsSync(path.join('.local/browser-tests/data/playback', task.id))).toBe(false);
  await page.close();
});

test('incompatible video requires confirmation then automatically loads the real transcode', async () => {
  const task = await downloadForPlayback('incompatible.mkv', '手动兼容转码验证');
  expect(task.playback_status).toBe('transcode_required');
  const page = await context.newPage();
  await page.goto(`${SERVICE}/#/play/${task.id}`);
  await expect(page.getByRole('button', { name: '尝试直接播放' })).toBeVisible();
  await page.getByRole('button', { name: '生成兼容播放版本', exact: true }).click();
  await expect(page.getByRole('dialog', { name: '生成兼容播放版本' })).toContainText('保留原视频');
  await page.getByRole('button', { name: '暂不生成' }).click();
  expect(fs.existsSync(path.join('.local/browser-tests/data/playback', task.id))).toBe(false);
  await page.getByRole('button', { name: '生成兼容播放版本', exact: true }).click();
  await page.getByRole('button', { name: '开始生成' }).click();
  await expect(page.getByText('兼容版本已就绪 · 转码', { exact: true })).toBeVisible({ timeout: 60_000 });
  const player = page.getByLabel(`播放 ${task.title}`);
  await expect.poll(() => player.evaluate((v: HTMLVideoElement) => v.readyState)).toBeGreaterThan(0);
  await player.evaluate((v: HTMLVideoElement) => v.play());
  await expect.poll(() => player.evaluate((v: HTMLVideoElement) => v.currentTime)).toBeGreaterThan(0);
  await page.screenshot({ path: '.local/screenshots/playback-compatible.png', fullPage: true });
  await page.close();
  const removed = await context.request.delete(`${SERVICE}/api/v1/tasks/${task.id}?delete_file=true`, {
    headers: { Authorization: `Bearer ${TOKEN}` },
  });
  expect(removed.ok()).toBe(true);
  expect(fs.existsSync(task.output_path!)).toBe(false);
});

test('playback controls show checking, delayed remux progress, queue, cancel and retry states', async () => {
  const task = await downloadForPlayback('sample.mp4', '播放准备状态验证');
  const page = await context.newPage();
  let current: Task = { ...task, playback_status: 'checking' };
  const modes: string[] = [];
  await page.route(`**/api/v1/tasks/${task.id}`, (route) => route.fulfill({ json: current }));
  await page.route(`**/api/v1/tasks/${task.id}/playback/cancel`, (route) => {
    current = { ...current, playback_status: 'canceled' };
    return route.fulfill({ json: current });
  });
  await page.route(`**/api/v1/tasks/${task.id}/playback/prepare`, (route) => {
    const mode = route.request().postDataJSON().mode;
    modes.push(mode);
    current = {
      ...current,
      playback_status: mode === 'transcode' ? 'transcode_queued' : 'checking',
      playback_error: null,
    };
    return route.fulfill({ json: current });
  });
  await page.goto(`${SERVICE}/#/play/${task.id}`);
  await expect(page.getByRole('button', { name: '检查格式…' })).toBeDisabled();
  current = {
    ...current,
    playback_status: 'remuxing',
    playback_progress: 45,
    playback_speed: 3,
    playback_eta: 8,
  };
  await expect(page.getByRole('progressbar', { name: '播放处理进度' })).toHaveAttribute(
    'aria-valuenow',
    '45',
  );
  await expect(page.getByText('3.0× · 剩余 8 秒')).toBeVisible();
  await page.getByRole('button', { name: '取消准备' }).click();
  await expect(page.getByText('播放准备已取消', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: '重新准备' }).click();
  expect(modes).toEqual(['recommended']);
  current = { ...current, playback_status: 'transcode_required' };
  await page.getByRole('button', { name: '生成兼容播放版本' }).click();
  await page.getByRole('button', { name: '开始生成' }).click();
  await expect(page.getByRole('button', { name: '等待转码…' })).toBeDisabled();
  expect(modes).toEqual(['recommended', 'transcode']);
  current = {
    ...current,
    playback_status: 'transcoding',
    playback_progress: null,
    playback_eta: null,
    playback_speed: null,
  };
  await expect(page.getByRole('button', { name: '转码中' })).toBeDisabled();
  await expect(page.getByRole('progressbar')).not.toHaveAttribute('aria-valuenow');
  current = { ...current, playback_progress: 32, playback_speed: 1.2, playback_eta: 11 };
  await expect(page.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '32');
  current = {
    ...current,
    playback_status: 'failed',
    playback_error: { code: 'DISK_ERROR', message: '磁盘空间不足', action: '清理后重试' },
  };
  await expect(page.getByRole('alert')).toContainText('磁盘空间不足');
  await page.getByRole('button', { name: '重新准备' }).click();
  current = { ...current, playback_status: 'interrupted' };
  await expect(page.getByText('播放准备已中断', { exact: true })).toBeVisible();
  current = { ...task };
  await expect(page.getByLabel(`播放 ${task.title}`)).toBeVisible();
  await page.close();
  await context.request.delete(`${SERVICE}/api/v1/tasks/${task.id}?delete_file=true`, {
    headers: { Authorization: `Bearer ${TOKEN}` },
  });
});

test('player explains missing source files', async () => {
  const task = await downloadForPlayback('sample.mp4', '文件缺失验证');
  fs.unlinkSync(task.output_path!);
  const page = await context.newPage();
  await page.goto(`${SERVICE}/#/play/${task.id}`);
  await expect(page.getByRole('alert')).toContainText('本地视频文件不存在');
  await expect(page.getByRole('button', { name: '系统播放器打开' })).toBeVisible();
  await page.close();
  await context.request.delete(`${SERVICE}/api/v1/tasks/${task.id}`, {
    headers: { Authorization: `Bearer ${TOKEN}` },
  });
});
