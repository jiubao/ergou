import { describe, it, expect } from 'vitest';
import { classify, manifestChildren, mergeCandidate, type Candidate } from './media';
const candidate = (url: string): Candidate => ({
  id: url,
  source: { url, kind: 'direct' },
  frameId: 0,
  from: 'network',
  detectedAt: 1,
});
describe('media detection', () => {
  it('uses MIME for signed URLs without suffixes', () => {
    expect(classify('https://cdn.test/play?t=a', 'application/vnd.apple.mpegurl')).toBe('hls');
  });
  it('does not expose fragments as separate videos', () => {
    expect(classify('https://cdn.test/1.m4s', 'video/mp4')).toBe(null);
    expect(classify('https://cdn.test/1.ts', 'video/mp2t')).toBe(null);
  });
  it('retains meaningful query parameters during deduplication', () => {
    const a = candidate('https://cdn.test/v.mp4?t=1');
    const b = candidate('https://cdn.test/v.mp4?t=2');
    expect(mergeCandidate([a], a)).toHaveLength(1);
    expect(mergeCandidate([a], b)).toHaveLength(2);
  });
  it('groups variant playlists and external audio, not media segments', () => {
    const text =
      '#EXTM3U\n#EXT-X-MEDIA:TYPE=AUDIO,URI="audio.m3u8?token=a"\n#EXT-X-STREAM-INF:BANDWIDTH=1000\n720.m3u8?token=b\n#EXTINF:2,\nsegment.ts';
    expect(manifestChildren(text, 'https://cdn.test/master.m3u8')).toEqual([
      'https://cdn.test/audio.m3u8?token=a',
      'https://cdn.test/720.m3u8?token=b',
    ]);
  });
});
