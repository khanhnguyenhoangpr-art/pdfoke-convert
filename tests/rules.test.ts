import { describe, it, expect } from 'vitest';
import { dayKey } from '../src/rules';

describe('dayKey (quota resets at Vietnam midnight)', () => {
  it('uses UTC+7', () => {
    expect(dayKey(new Date('2026-11-15T16:59:59Z'))).toBe('2026-11-15'); // 23:59:59 in VN
    expect(dayKey(new Date('2026-11-15T17:00:00Z'))).toBe('2026-11-16'); // 00:00 in VN
  });
});
