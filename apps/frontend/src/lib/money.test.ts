import { describe, expect, it } from 'vitest';

import {
  formatUsd,
  formatUsdCost,
  formatUsdLeft,
  MICROUSD_PER_USD,
  parseUsd,
  usdFieldValue,
} from './money';

describe('a dollars amount as typed', () => {
  it('is read as micro-dollars', () => {
    expect(parseUsd('5')).toBe(5_000_000);
    expect(parseUsd('2.8')).toBe(2_800_000);
    expect(parseUsd('2.80')).toBe(2_800_000);
    expect(parseUsd('0.15')).toBe(150_000);
    expect(parseUsd('0')).toBe(0);
    expect(parseUsd('0.00')).toBe(0);
    // A million dollars: the most a budget or a price is ever set to.
    expect(parseUsd('1000000')).toBe(1_000_000 * MICROUSD_PER_USD);
  });

  it('is read by its digits, so nothing is lost to a float', () => {
    // Each of these is off by one when multiplied as a float.
    expect(1.005 * MICROUSD_PER_USD).not.toBe(1_005_000);
    expect(parseUsd('1.005')).toBe(1_005_000);
    expect(parseUsd('4.35')).toBe(4_350_000);
    expect(parseUsd('0.000001')).toBe(1);
    expect(parseUsd('8.200001')).toBe(8_200_001);
    expect(parseUsd('1000000.000001')).toBe(1_000_000_000_001);
  });

  it('takes the forms an amount is typed in', () => {
    expect(parseUsd('  12.5  ')).toBe(12_500_000);
    expect(parseUsd('.5')).toBe(500_000);
    expect(parseUsd('5.')).toBe(5_000_000);
    expect(parseUsd('007.50')).toBe(7_500_000);
    expect(parseUsd('1.123456')).toBe(1_123_456);
  });

  it('is nothing when it has more than six decimal places', () => {
    expect(parseUsd('0.0000001')).toBeNull();
    expect(parseUsd('1.1234567')).toBeNull();
    // Not even when the extra places are zeros: the amount typed is not the amount kept.
    expect(parseUsd('1.1000000')).toBeNull();
  });

  it('is nothing when it is negative or not an amount at all', () => {
    for (const text of [
      '',
      '   ',
      '.',
      '-1',
      '-0.5',
      '+1',
      '$5',
      '5$',
      '1,000',
      '2,8',
      '1e3',
      '0x10',
      '1.2.3',
      '1 000',
      'five',
      'NaN',
      'Infinity',
    ]) {
      expect(parseUsd(text), text).toBeNull();
    }
  });

  it('is nothing when it is too large to keep exactly', () => {
    expect(parseUsd('9007199254.740991')).toBe(Number.MAX_SAFE_INTEGER);
    expect(parseUsd('9007199254.740992')).toBeNull();
    expect(parseUsd('9'.repeat(40))).toBeNull();
  });
});

describe('micro-dollars for reading', () => {
  it('are dollars with two decimals', () => {
    expect(formatUsd(3_200_000)).toBe('$3.20');
    expect(formatUsd(5_000_000)).toBe('$5.00');
    expect(formatUsd(0)).toBe('$0.00');
    expect(formatUsd(10_000)).toBe('$0.01');
    expect(formatUsd(1_234_567_890_000)).toBe('$1,234,567.89');
  });

  it('round to the nearest cent', () => {
    expect(formatUsd(3_194_999)).toBe('$3.19');
    expect(formatUsd(3_195_000)).toBe('$3.20');
    expect(formatUsd(4_999)).toBe('$0.00');
    expect(formatUsd(5_000)).toBe('$0.01');
    expect(formatUsd(999_995_000)).toBe('$1,000.00');
  });

  it('keep every decimal they have, and at least two, when asked to be precise', () => {
    const precise = (microusd: number) => formatUsd(microusd, { precise: true });
    expect(precise(4_200)).toBe('$0.0042');
    expect(precise(2_800_000)).toBe('$2.80');
    expect(precise(150_000)).toBe('$0.15');
    expect(precise(5_000_000)).toBe('$5.00');
    expect(precise(0)).toBe('$0.00');
    expect(precise(1)).toBe('$0.000001');
    expect(precise(1_250_042)).toBe('$1.250042');
    expect(precise(1_005_000)).toBe('$1.005');
    expect(precise(1_000_000_000_000)).toBe('$1,000,000.00');
  });

  it('read the same whatever the reader’s locale', () => {
    const original = Number.prototype.toLocaleString;
    // A locale that groups with a point and writes a decimal comma.
    Number.prototype.toLocaleString = function toLocaleString(locales, options) {
      return original.call(this, locales ?? 'de-DE', options);
    };
    try {
      expect((1234.5).toLocaleString()).toBe('1.234,5');
      expect(formatUsd(1_234_500_000)).toBe('$1,234.50');
      expect(formatUsd(1_234_500_000, { precise: true })).toBe('$1,234.50');
    } finally {
      Number.prototype.toLocaleString = original;
    }
  });

  it('say so for an amount below zero, and read as unknown for what is not a number', () => {
    expect(formatUsd(-1_200_000)).toBe('-$1.20');
    expect(formatUsd(-1_200_000, { precise: true })).toBe('-$1.20');
    // Nothing, however it got there, has no sign.
    expect(formatUsd(-4_000)).toBe('$0.00');
    expect(formatUsd(Number.NaN)).toBe('—');
    expect(formatUsd(Number.POSITIVE_INFINITY, { precise: true })).toBe('—');
  });
});

describe('a cost in a column of costs', () => {
  it('has exactly four decimals, so a column of them lines up', () => {
    expect(formatUsdCost(3_301_000)).toBe('$3.3010');
    expect(formatUsdCost(4_999_100)).toBe('$4.9991');
    expect(formatUsdCost(4_200)).toBe('$0.0042');
    expect(formatUsdCost(0)).toBe('$0.0000');
    expect(formatUsdCost(1_234_500_000)).toBe('$1,234.5000');
    expect(formatUsdCost(1_000_000_000_000)).toBe('$1,000,000.0000');
  });

  it('rounds half up to the nearest ten-thousandth of a dollar', () => {
    expect(formatUsdCost(116_250)).toBe('$0.1163');
    expect(formatUsdCost(116_249)).toBe('$0.1162');
    expect(formatUsdCost(1_250_042)).toBe('$1.2500');
    expect(formatUsdCost(999_950)).toBe('$1.0000');
    expect(formatUsdCost(50)).toBe('$0.0001');
    expect(formatUsdCost(149)).toBe('$0.0001');
    expect(formatUsdCost(150)).toBe('$0.0002');
  });

  it('reads as less than its last place, never as nothing, while it is something', () => {
    expect(formatUsdCost(1)).toBe('<$0.0001');
    expect(formatUsdCost(49)).toBe('<$0.0001');
  });

  it('reads the same whatever the reader’s locale', () => {
    const original = Number.prototype.toLocaleString;
    Number.prototype.toLocaleString = function toLocaleString(locales, options) {
      return original.call(this, locales ?? 'de-DE', options);
    };
    try {
      expect(formatUsdCost(1_234_500_000)).toBe('$1,234.5000');
    } finally {
      Number.prototype.toLocaleString = original;
    }
  });

  it('says so for an amount below zero, and reads as unknown for what is not a number', () => {
    expect(formatUsdCost(-1_200_000)).toBe('-$1.2000');
    expect(formatUsdCost(-40)).toBe('$0.0000');
    expect(formatUsdCost(Number.NaN)).toBe('—');
  });
});

describe('what is left of a budget', () => {
  it('reads as dollars and cents', () => {
    expect(formatUsdLeft(3_200_000)).toBe('$3.20');
    expect(formatUsdLeft(10_000)).toBe('$0.01');
    expect(formatUsdLeft(0)).toBe('$0.00');
  });

  it('reads as less than a cent, never as nothing, while anything is left', () => {
    expect(formatUsdLeft(1)).toBe('<$0.01');
    expect(formatUsdLeft(4_999)).toBe('<$0.01');
    expect(formatUsdLeft(9_999)).toBe('<$0.01');
  });
});

describe('micro-dollars as a field holds them', () => {
  it('are the precise amount with no symbol and no grouping', () => {
    expect(usdFieldValue(5_000_000)).toBe('5.00');
    expect(usdFieldValue(2_800_000)).toBe('2.80');
    expect(usdFieldValue(4_200)).toBe('0.0042');
    expect(usdFieldValue(0)).toBe('0.00');
    expect(usdFieldValue(1_000_000_000_000)).toBe('1000000.00');
    expect(usdFieldValue(Number.NaN)).toBe('');
  });

  it('are read back as the same amount', () => {
    for (const microusd of [0, 1, 4_200, 150_000, 2_800_000, 1_250_042, 1_000_000_000_000]) {
      expect(parseUsd(usdFieldValue(microusd)), String(microusd)).toBe(microusd);
    }
  });
});
