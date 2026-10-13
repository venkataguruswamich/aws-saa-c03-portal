// Password-protects the whole portal with HTTP Basic Auth, enforced on Vercel's servers
// before any page is sent. The password never appears in the code or on GitHub.
//
// Configure in Vercel → Project → Settings → Environment Variables:
//   SITE_PASSWORD  (required) the password you type at the browser prompt
//   SITE_USER      (optional) if set, the username must match too; otherwise any username works
//
// If SITE_PASSWORD is missing, the site stays closed (fails safe) instead of opening to everyone.

export const config = {
  // Everything except sw.js, which browsers that still have the old offline worker must be able
  // to fetch so it can remove itself. It contains no portal content.
  matcher: ['/((?!sw\\.js).*)'],
};

const REALM = 'SAA-C03 Portal';
const NO_STORE = { 'Cache-Control': 'no-store', 'Content-Type': 'text/plain; charset=utf-8' };

// Constant-time comparison so response timing doesn't leak how much of the password matched.
function safeEqual(a, b) {
  const x = new TextEncoder().encode(a);
  const y = new TextEncoder().encode(b);
  let diff = x.length ^ y.length;
  for (let i = 0; i < Math.max(x.length, y.length); i++) diff |= (x[i] ?? 0) ^ (y[i] ?? 0);
  return diff === 0;
}

function readBasicAuth(header) {
  const [scheme, encoded] = (header || '').split(' ');
  if (!encoded || scheme.toLowerCase() !== 'basic') return null;
  try {
    const decoded = new TextDecoder().decode(Uint8Array.from(atob(encoded), (c) => c.charCodeAt(0)));
    const i = decoded.indexOf(':');
    return i < 0 ? null : { user: decoded.slice(0, i), pass: decoded.slice(i + 1) };
  } catch {
    return null;
  }
}

export default function middleware(request) {
  const password = process.env.SITE_PASSWORD;
  if (!password) {
    return new Response('This portal is private and its password is not configured yet.', { status: 503, headers: NO_STORE });
  }

  const creds = readBasicAuth(request.headers.get('authorization'));
  const expectedUser = process.env.SITE_USER;
  if (creds && safeEqual(creds.pass, password) && (!expectedUser || safeEqual(creds.user, expectedUser))) {
    return; // authorized: continue to the requested page
  }

  return new Response('Authentication required.', {
    status: 401,
    headers: { ...NO_STORE, 'WWW-Authenticate': `Basic realm="${REALM}", charset="UTF-8"` },
  });
}
