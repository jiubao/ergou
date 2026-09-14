import type { Source } from '@ergou/contracts';
export interface Candidate {
  id: string;
  source: Source;
  poster?: string;
  duration?: number;
  frameId: number;
  detectedAt: number;
  from: 'dom' | 'network' | 'page';
}
export interface TabState {
  pageUrl: string;
  title: string;
  candidates: Candidate[];
  requests: { url: string; headers: Record<string, string> }[];
  parents?: Record<string, string>;
}
export function manifestChildren(text: string, base: string): string[] {
  if (!text.trimStart().startsWith('#EXTM3U')) return [];
  const children = new Set<string>();
  let variant = false;
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.trim();
    if (line.startsWith('#EXT-X-STREAM-INF:')) variant = true;
    else if (line.startsWith('#EXT-X-MEDIA:')) {
      const uri = line.match(/URI="([^"]+)"/);
      if (uri) children.add(new URL(uri[1], base).href);
    } else if (line && !line.startsWith('#')) {
      if (variant) children.add(new URL(line, base).href);
      variant = false;
    }
  }
  return [...children].filter(isHttp);
}
export function classify(url: string, mime = ''): Source['kind'] | null {
  let path: string;
  try {
    path = new URL(url).pathname.toLowerCase();
  } catch {
    return null;
  }
  if (mime.toLowerCase().includes('mpegurl') || path.endsWith('.m3u8')) return 'hls';
  if (mime.toLowerCase().includes('dash+xml') || path.endsWith('.mpd')) return 'dash';
  if (/\.(m4s|ts|aac)$/.test(path) || mime.toLowerCase().includes('mp2t')) return null;
  if (mime.toLowerCase().startsWith('video/') || /\.(mp4|webm|mov|mkv)$/.test(path)) return 'direct';
  return null;
}
export function mergeCandidate(items: Candidate[], candidate: Candidate): Candidate[] {
  const old = items.findIndex((i) => i.source.url === candidate.source.url);
  if (old >= 0) {
    const existing = items[old];
    const updated = {
      ...existing,
      ...candidate,
      id: existing.id,
      source: { ...existing.source, ...candidate.source },
      poster: candidate.poster || existing.poster,
      duration: candidate.duration || existing.duration,
    };
    if (existing.from === 'dom' && candidate.from === 'network') updated.source.title = existing.source.title;
    return items.map((item, index) => (index === old ? updated : item));
  }
  return [...items, candidate].slice(-200);
}
export function isHttp(url: string) {
  try {
    return ['http:', 'https:'].includes(new URL(url).protocol);
  } catch {
    return false;
  }
}
