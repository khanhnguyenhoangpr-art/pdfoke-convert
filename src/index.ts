// pdfoke-convert Worker (AGPL-3.0). Fronts the conversion container:
//   POST /api/convert/:kind   (kind: pdf-to-word | word-to-pdf | excel-to-pdf | ppt-to-pdf)
//   GET  /api/convert/quota
// Guards: Cloudflare Turnstile token, 25 MB max, 10 successful conversions per IP per
// Vietnam calendar day (shared across kinds). Reached only through the pdfoke site's
// service binding (workers.dev is disabled). Files are never stored.
import { Container, getRandom } from '@cloudflare/containers';
import { DurableObject } from 'cloudflare:workers';
import { dayKey, KINDS, MAX_BYTES, DAILY_LIMIT, json } from './rules';

export interface Env {
  CONVERTER: DurableObjectNamespace<Converter>;
  LIMITER: DurableObjectNamespace<RateLimiter>;
  TURNSTILE_SECRET: string;
}

export class Converter extends Container<Env> {
  defaultPort = 8080;
  sleepAfter = '5m'; // D14: sleep after 5 idle minutes
}

/** One instance per IP per day; counts successful conversions. */
export class RateLimiter extends DurableObject<Env> {
  async used(): Promise<number> {
    return (await this.ctx.storage.get<number>('n')) ?? 0;
  }
  async add(): Promise<number> {
    const n = (await this.used()) + 1;
    await this.ctx.storage.put('n', n);
    if (!(await this.ctx.storage.getAlarm())) await this.ctx.storage.setAlarm(Date.now() + 2 * 86_400_000);
    return n;
  }
  async alarm() {
    await this.ctx.storage.deleteAll(); // old counters clean themselves up
  }
}

const INSTANCES = 2; // D14: at most 2 containers

async function verifyTurnstile(token: string | null, ip: string, secret: string): Promise<boolean> {
  if (!token) return false;
  const body = new FormData();
  body.append('secret', secret);
  body.append('response', token);
  body.append('remoteip', ip);
  const r = await fetch('https://challenges.cloudflare.com/turnstile/v0/siteverify', { method: 'POST', body });
  const data = (await r.json()) as { success?: boolean };
  return !!data.success;
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);
    const ip = request.headers.get('cf-connecting-ip') ?? 'unknown';
    const limiter = () => env.LIMITER.get(env.LIMITER.idFromName(`${ip}:${dayKey(new Date())}`));

    if (url.pathname === '/api/convert/quota' && request.method === 'GET') {
      const used = await limiter().used();
      return json({ limit: DAILY_LIMIT, remaining: Math.max(0, DAILY_LIMIT - used) });
    }

    const m = url.pathname.match(/^\/api\/convert\/([a-z-]+)$/);
    if (!m || request.method !== 'POST') return json({ code: 'not-found' }, 404);
    const kind = m[1];
    if (!KINDS.includes(kind)) return json({ code: 'unsupported' }, 404);

    const length = Number(request.headers.get('content-length') ?? 0);
    if (!length) return json({ code: 'empty' }, 400);
    if (length > MAX_BYTES) return json({ code: 'too-large' }, 413);

    if (!(await verifyTurnstile(request.headers.get('x-turnstile'), ip, env.TURNSTILE_SECRET))) {
      return json({ code: 'bot' }, 403);
    }

    const counter = limiter();
    const used = await counter.used();
    if (used >= DAILY_LIMIT) return json({ code: 'rate-limited', remaining: 0 }, 429);

    const container = await getRandom(env.CONVERTER, INSTANCES);
    const filename = request.headers.get('x-filename') ?? 'file';
    const res = await container.fetch(
      new Request(`http://container/convert/${kind}`, {
        method: 'POST',
        body: request.body,
        headers: { 'x-filename': filename, 'content-length': String(length) },
      }),
    );
    if (!res.ok) return new Response(res.body, { status: res.status, headers: { 'content-type': 'application/json' } });

    const n = await counter.add();
    const headers = new Headers(res.headers);
    headers.set('x-remaining', String(Math.max(0, DAILY_LIMIT - n)));
    headers.set('cache-control', 'no-store');
    return new Response(res.body, { status: 200, headers });
  },
};
