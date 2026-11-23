// PixelRelay on Cloudflare: a Worker in front of one Container running the Python app (the Dockerfile at
// the repo root). The Worker has no logic of its own: every request goes to the single container instance,
// which starts on the first request and sleeps after 15 idle minutes.
//
// State never lives in the container (its disk is wiped when it sleeps):
//   data  -> Postgres schema accurova_pixelrelay in the shared Supabase project (DATABASE_URL)
//   files -> R2 bucket accurova-pixelrelay through its S3 API (R2_* secrets)
// Jobs interrupted by a sleep or restart are leased in Postgres and retried on the next start.
//
// Secrets (wrangler secret put, never in git): SECRET_KEY DATABASE_URL R2_ACCESS_KEY_ID R2_SECRET_ACCESS_KEY
//   SUPABASE_URL SUPABASE_ANON_KEY OWNER_EMAILS. Everything else is in wrangler.jsonc "vars".
import { Container } from '@cloudflare/containers';

const PORT = 8000;
const PASS_THROUGH = [
  'APP_ENV', 'BASE_URL', 'AUTH_MODE', 'STORAGE_PROVIDER', 'R2_ENDPOINT', 'R2_BUCKET', 'DB_SCHEMA', 'TRUST_PROXY',
  'WORKER_MODE', 'MAX_UPLOAD_MB', 'MAX_IMAGE_MEGAPIXELS',
  'SECRET_KEY', 'DATABASE_URL', 'R2_ACCESS_KEY_ID', 'R2_SECRET_ACCESS_KEY', 'SUPABASE_URL', 'SUPABASE_ANON_KEY',
  'OWNER_EMAILS',
];

export class PixelRelayContainer extends Container {
  defaultPort = PORT;
  sleepAfter = '15m';
  enableInternet = true; // Supabase (Postgres + Auth) and the R2 S3 endpoint

  constructor(ctx, env) {
    super(ctx, env);
    // This account only allows Durable Object-managed containers, which must be told the image and the
    // instance size on every start; @cloudflare/containers 0.3.7 passes neither (same fix as
    // thebooleanjulian-llm). standard-1 = 4 GiB / 1/2 vCPU: a 24 MP Smart Merge peaks near 400 MB.
    const real = ctx.container;
    this.container = new Proxy(real, {
      get(target, key) {
        if (key === 'start') {
          return (opts = {}) => target.start({ image: target.images.pixelrelay, instance: 'standard-1', ...opts });
        }
        const value = target[key];
        return typeof value === 'function' ? value.bind(target) : value;
      },
    });
    this.envVars = Object.fromEntries(
      PASS_THROUGH.filter((k) => typeof env[k] === 'string' && env[k] !== '').map((k) => [k, env[k]]),
    );
  }

  // Python + numpy + the first Postgres connection can take longer than the library's 20 s default.
  async fetch(request) {
    const state = await this.state.getState();
    if (!this.container.running || state.status !== 'healthy') {
      try {
        await this.startAndWaitForPorts({
          ports: PORT,
          cancellationOptions: { portReadyTimeoutMS: 90_000, abort: request.signal },
        });
      } catch (err) {
        console.error(JSON.stringify({ pixelrelay: 'start failed', error: String(err?.message || err) }));
        return new Response('PixelRelay is starting up. Refresh in a few seconds.', {
          status: 503,
          headers: { 'retry-after': '5', 'content-type': 'text/plain; charset=utf-8' },
        });
      }
    }
    return this.containerFetch(request, PORT);
  }

  onError(error) {
    console.error(JSON.stringify({ pixelrelay: 'container error', error: String(error?.message || error) }));
  }
}

export default {
  async fetch(request, env) {
    return env.APP.getByName('main').fetch(request);
  },
};
