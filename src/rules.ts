export const MAX_BYTES = 25 * 1024 * 1024; // D8
export const DAILY_LIMIT = 10; // D15
export const KINDS = ['pdf-to-word', 'word-to-pdf', 'excel-to-pdf', 'ppt-to-pdf', 'edit-text'];

/** Calendar day in Vietnam (UTC+7), so the quota resets at local midnight. */
export function dayKey(now: Date): string {
  return new Date(now.getTime() + 7 * 3_600_000).toISOString().slice(0, 10);
}

export function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json', 'cache-control': 'no-store' } });
}
