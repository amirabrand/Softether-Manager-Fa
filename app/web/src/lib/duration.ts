/**
 * Subscription duration groups.
 *
 * VPN operators sell time: a month, three, a year. These presets give that
 * habit a name on the server — creating the standard group set on a hub in
 * one action — and let the user sheet fill an expiry the moment the group is
 * picked, so "add a 3-month user" is one choice instead of a date math
 * exercise.
 *
 * Group names are fixed ASCII (SoftEther handles any string, but these end up
 * typed into consoles and CSV exports) while the stored realname is Persian,
 * the edition's primary audience. The localized labels below are for the
 * interface, not the stored record.
 */

export interface DurationPreset {
  /** Fixed group name created on the hub. */
  name: string;
  /** Subscription length in whole months (a year is 12). */
  months: number;
  /** Persian realname stored on the server record. */
  fa: string;
  /** English label key for the interface (goes through t()). */
  labelKey: string;
}

export const DURATION_GROUPS: DurationPreset[] = [
  { name: "1month", months: 1, fa: "اشتراک ۱ ماهه", labelKey: "1 month" },
  { name: "2months", months: 2, fa: "اشتراک ۲ ماهه", labelKey: "2 months" },
  { name: "3months", months: 3, fa: "اشتراک ۳ ماهه", labelKey: "3 months" },
  { name: "6months", months: 6, fa: "اشتراک ۶ ماهه", labelKey: "6 months" },
  { name: "9months", months: 9, fa: "اشتراک ۹ ماهه", labelKey: "9 months" },
  { name: "1year", months: 12, fa: "اشتراک ۱ ساله", labelKey: "1 year" },
];

/** The preset a group name stands for, or null when it is an ordinary group. */
export function durationOfGroup(name: string): DurationPreset | null {
  return DURATION_GROUPS.find((d) => d.name === name) ?? null;
}

/**
 * The expiry a duration implies: the length added to now, on the same clock
 * time. Adding months lands on the same day-of-month; the 31st plus one
 * month rolls to the 1st of the next month in JS Dates, which is the
 * behaviour an operator expects from a calendar, not a subtraction exercise.
 */
export function expiryFromMonths(months: number, from: Date = new Date()): Date {
  const d = new Date(from.getTime());
  d.setMonth(d.getMonth() + months);
  return d;
}

/** A Date as the value a datetime-local input expects (local wall clock). */
export function toLocalInput(d: Date): string {
  const copy = new Date(d.getTime());
  copy.setMinutes(copy.getMinutes() - copy.getTimezoneOffset());
  return copy.toISOString().slice(0, 16);
}
