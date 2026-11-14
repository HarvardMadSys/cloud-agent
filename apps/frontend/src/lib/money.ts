// Money as the control plane keeps it, and as people read and type it.
//
// An amount is a whole number of millionths of a US dollar ("micro-dollars",
// in fields named `…_microusd`): `5000000` is $5.00. A price is the same unit
// per million tokens: `2800000` is $2.80 per million tokens. Both convert the
// same way, and never through a float: `1.005 * 1_000_000` is not `1005000`.

/** Micro-dollars in one US dollar. */
export const MICROUSD_PER_USD = 1_000_000;
/** How many decimal places of a dollar a micro-dollar keeps. */
const DECIMALS = 6;
const MICROUSD_PER_CENT = 10_000;

/** `12`, `12.`, `12.5` or `.5`: digits only, with no sign, exponent, separator or symbol. */
const DOLLARS = /^(?:(\d+)(?:\.(\d*))?|\.(\d+))$/;

/**
 * A dollars amount as typed, in micro-dollars: `"2.8"` is `2800000`.
 *
 * Exact, by its digits. `null` for anything that is not a plain amount: a
 * negative one, one with more than six decimal places (it is no whole number
 * of micro-dollars), or one too large to keep exactly.
 */
export function parseUsd(text: string): number | null {
  const match = DOLLARS.exec(text.trim());
  if (!match) return null;
  const fraction = match[2] ?? match[3] ?? '';
  if (fraction.length > DECIMALS) return null;
  const microusd =
    Number(match[1] ?? '0') * MICROUSD_PER_USD + Number(fraction.padEnd(DECIMALS, '0'));
  return Number.isSafeInteger(microusd) ? microusd : null;
}

/** How many whole `unit`s are in `amount`, and what is left over; exact for whole numbers. */
function divide(amount: number, unit: number): [whole: number, rest: number] {
  const rest = amount % unit;
  return [(amount - rest) / unit, rest];
}

/** An amount as a whole number of micro-dollars and its sign; `null` when it is not a number. */
function magnitude(microusd: number): { negative: boolean; amount: number } | null {
  if (!Number.isFinite(microusd)) return null;
  const whole = Math.round(microusd);
  return { negative: whole < 0, amount: Math.abs(whole) };
}

/** Every decimal the amount has, and at least two: `2.80`, `0.0042`, `1,000.00`. */
function exactDigits(amount: number, grouped: boolean): string {
  const [dollars, rest] = divide(amount, MICROUSD_PER_USD);
  const decimals = String(rest)
    .padStart(DECIMALS, '0')
    .replace(/0{1,4}$/, '');
  return `${grouped ? dollars.toLocaleString('en-US') : dollars}.${decimals}`;
}

/** Two decimals, to the nearest cent: `3.20`. */
function centDigits(amount: number): string {
  const [cents] = divide(amount + MICROUSD_PER_CENT / 2, MICROUSD_PER_CENT);
  const [dollars, rest] = divide(cents, 100);
  return `${dollars.toLocaleString('en-US')}.${String(rest).padStart(2, '0')}`;
}

/**
 * Micro-dollars for reading: `$3.20`.
 *
 * Two decimals, to the nearest cent. `precise` keeps every decimal the amount
 * has, and at least two, where the exact amount matters: a price (`$0.15`,
 * `$2.80`) and an administrator's tables (`$0.0042`).
 *
 * The symbol and the grouping are fixed, not the reader's locale's: an amount
 * here is always US dollars and reads the same to everyone.
 */
export function formatUsd(
  microusd: number,
  { precise = false }: { precise?: boolean } = {},
): string {
  const value = magnitude(microusd);
  if (value === null) return '—';
  const digits = precise ? exactDigits(value.amount, true) : centDigits(value.amount);
  // An amount that rounds to nothing has no sign.
  return `${value.negative && /[1-9]/.test(digits) ? '-' : ''}$${digits}`;
}

/** Micro-dollars in a ten-thousandth of a dollar: the last place a cost in a column shows. */
const MICROUSD_PER_COST_PLACE = 100;
/** Ten-thousandths in a dollar. */
const COST_PLACES_PER_USD = 10_000;

/** Four decimals, to the nearest ten-thousandth of a dollar: `1,234.5000`. */
function costDigits(amount: number): string {
  const [places] = divide(amount + MICROUSD_PER_COST_PLACE / 2, MICROUSD_PER_COST_PLACE);
  const [dollars, rest] = divide(places, COST_PLACES_PER_USD);
  return `${dollars.toLocaleString('en-US')}.${String(rest).padStart(4, '0')}`;
}

/**
 * A cost in a column of costs: `$3.3010`.
 *
 * Always four decimals, to the nearest ten-thousandth of a dollar, so the
 * digits of a column line up whatever each amount is. A cost that rounds to
 * nothing but is not nothing reads `<$0.0001`, never `$0.0000`.
 */
export function formatUsdCost(microusd: number): string {
  const value = magnitude(microusd);
  if (value === null) return '—';
  if (!value.negative && value.amount > 0 && value.amount < MICROUSD_PER_COST_PLACE / 2) {
    return '<$0.0001';
  }
  const digits = costDigits(value.amount);
  return `${value.negative && /[1-9]/.test(digits) ? '-' : ''}$${digits}`;
}

/**
 * What is left of a budget, for its owner: `$3.20`.
 *
 * Something left that is less than a cent reads `<$0.01`, never `$0.00`:
 * nothing left closes a model, and this has not.
 */
export function formatUsdLeft(microusd: number): string {
  return microusd > 0 && microusd < MICROUSD_PER_CENT ? '<$0.01' : formatUsd(microusd);
}

/**
 * Micro-dollars as a field holds them: `2.80`, `0.0042`, `1000.00`. Every
 * decimal, no symbol and no grouping, so `parseUsd` reads the same amount back.
 */
export function usdFieldValue(microusd: number): string {
  const value = magnitude(microusd);
  if (value === null) return '';
  return `${value.negative ? '-' : ''}${exactDigits(value.amount, false)}`;
}
