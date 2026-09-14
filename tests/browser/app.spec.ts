import { test, expect, chromium, type BrowserContext } from '@playwright/test';
import path from 'node:path';
import fs from 'node:fs';

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
  fs.mkdirSync('.local/screenshots', { recursive: true });
  await page.screenshot({ path: '.local/screenshots/web-desktop.png', fullPage: true });
  await page.setViewportSize({ width: 700, height: 1000 });
  await page.screenshot({ path: '.local/screenshots/web-narrow.png', fullPage: true });
  expect(errors).toEqual([]);
  await page.close();
});

test('web resolution permits quality selection before downloading HLS', async () => {
  const page = await context.newPage();
  await page.goto(SERVICE);
  await page.getByRole('button', { name: '新建下载', exact: true }).click();
  await page.getByLabel('视频地址').fill(`${MEDIA}/multi.m3u8`);
  await page.getByRole('button', { name: '解析清晰度', exact: true }).click();
  await expect(page.getByRole('combobox', { name: '清晰度', exact: true })).toBeVisible();
  await page.getByRole('button', { name: '开始下载', exact: true }).click();
  await expect(page.getByRole('article').first()).toContainText('已完成');
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
  await card.getByRole('button', { name: '下载', exact: true }).click();
  await expect(popup.locator('.notice')).toContainText('已提交');
  await expect
    .poll(async () => {
      const response = await context.request.get(
        `${SERVICE}/api/v1/tasks?search=${encodeURIComponent('MP4 完整下载验证')}`,
        { headers: { Authorization: `Bearer ${TOKEN}` } },
      );
      return (await response.json()).items[0]?.status;
    })
    .toBe('completed');
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
      return { status: task?.status, session: task?.source.requires_session };
    })
    .toEqual({ status: 'completed', session: true });
  await popup.close();
  await watch.close();
  await context.clearCookies();
});
