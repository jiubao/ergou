import { it, expect } from 'vitest';
import { canRetry, isActive } from '@ergou/contracts';
it('exposes retry for recoverable terminal tasks without exposing a fake pause', () => {
  expect(canRetry('interrupted')).toBe(true);
  expect(canRetry('completed')).toBe(false);
  expect(isActive('merging')).toBe(true);
  expect(isActive('canceled')).toBe(false);
});
