import { defineConfig } from 'wxt';
export default defineConfig({
  modules: ['@wxt-dev/module-react'],
  manifest: {
    name: 'Ergou · 网页视频下载',
    description: '发现网页视频，并交给本机 Ergou 服务下载。',
    permissions: ['storage', 'webRequest', 'webNavigation', 'cookies'],
    host_permissions: ['http://*/*', 'https://*/*'],
    action: { default_title: 'Ergou · 发现网页视频' },
  },
});
