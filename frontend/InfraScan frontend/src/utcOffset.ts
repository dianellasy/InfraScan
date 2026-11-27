export const MIN_UTC_OFFSET = -720;
export const MAX_UTC_OFFSET = 840;

export const UTC_OFFSET_OPTIONS = Array.from(
  { length: (MAX_UTC_OFFSET - MIN_UTC_OFFSET) / 15 + 1 },
  (_, index) => MIN_UTC_OFFSET + index * 15,
);

export function formatUtcOffset(minutes: number): string {
  const sign = minutes < 0 ? "−" : "+";
  const absolute = Math.abs(minutes);
  const hours = String(Math.floor(absolute / 60)).padStart(2, "0");
  return `UTC${sign}${hours}:${String(absolute % 60).padStart(2, "0")}`;
}

/** The browser's current offset, as a default for the camera's time-zone setting. */
export function browserUtcOffset(date = new Date()): number {
  const offset = Math.round(-date.getTimezoneOffset() / 15) * 15;
  return Math.min(MAX_UTC_OFFSET, Math.max(MIN_UTC_OFFSET, offset));
}
