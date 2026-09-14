import { describe, it, expect } from 'vitest';
import { bytes, validateService } from './index';
describe('formatting and local endpoint boundary', () => {
  it('keeps unknown lengths distinct from zero', () => {
    expect(bytes(null)).toBe('未知');
    expect(bytes(0)).toBe('0 B');
  });
  it('accepts loopback services and rejects remote/token URLs', () => {
    expect(validateService('http://127.0.0.1:17890/')).toBe('http://127.0.0.1:17890');
    expect(() => validateService('https://example.com')).toThrow();
    expect(() => validateService('http://user:secret@localhost')).toThrow();
  });
});
