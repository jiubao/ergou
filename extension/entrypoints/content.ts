import { defineContentScript } from 'wxt/utils/define-content-script';
import { isHttp } from '../lib/media';
export default defineContentScript({
  matches: ['http://*/*', 'https://*/*'],
  allFrames: true,
  runAt: 'document_start',
  main(ctx) {
    let timer: ReturnType<typeof setTimeout>;
    const scan = () => {
      if (!isHttp(location.href)) return;
      const roots: (Document | ShadowRoot)[] = [document];
      const media: { url: string; poster: string; duration: number | null; title: string }[] = [];
      // Include open shadow roots, without instrumenting page JavaScript or MediaSource buffers.
      for (let i = 0; i < roots.length && i < 100; i++) {
        const root = roots[i];
        for (const el of root.querySelectorAll('*')) if (el.shadowRoot) roots.push(el.shadowRoot);
        for (const video of root.querySelectorAll('video')) {
          const urls = [
            video.currentSrc,
            video.src,
            ...[...video.querySelectorAll('source')].map((s) => s.src),
          ].filter(Boolean);
          for (const url of new Set(urls))
            media.push({
              url,
              poster: video.poster,
              duration: Number.isFinite(video.duration) ? video.duration : null,
              title: video.getAttribute('aria-label') || video.title || document.title,
            });
          if (!urls.length)
            media.push({ url: '', poster: video.poster, duration: null, title: document.title });
        }
      }
      void chrome.runtime
        .sendMessage({ type: 'observed', pageUrl: location.href, title: document.title, media })
        .catch(() => {});
    };
    const schedule = () => {
      clearTimeout(timer);
      timer = setTimeout(scan, 250);
    };
    const observer = new MutationObserver(schedule);
    observer.observe(document, {
      subtree: true,
      childList: true,
      attributes: true,
      attributeFilter: ['src', 'poster'],
    });
    document.addEventListener('play', schedule, true);
    document.addEventListener('loadedmetadata', schedule, true);
    chrome.runtime.onMessage.addListener((msg) => {
      if (msg.type === 'scan') schedule();
    });
    ctx.onInvalidated(() => {
      observer.disconnect();
      clearTimeout(timer);
      document.removeEventListener('play', schedule, true);
      document.removeEventListener('loadedmetadata', schedule, true);
    });
    schedule();
  },
});
