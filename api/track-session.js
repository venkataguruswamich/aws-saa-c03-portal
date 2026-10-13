// POST /api/track-session: relays onboarding and progress events to a Discord or Telegram webhook.
// Nothing is stored. Each request becomes one chat message, and then it's gone.
//
// Configure in Vercel → Project → Settings → Environment Variables:
//   PROCESS_WEBHOOK_URL  (required) one of:
//     Discord:  https://discord.com/api/webhooks/<id>/<token>
//     Telegram: https://api.telegram.org/bot<token>/sendMessage?chat_id=<chat id>
//     Anything else receives a plain JSON body: { "text": "...", "content": "..." }
//   TELEGRAM_CHAT_ID     (optional) used when the Telegram URL has no chat_id parameter

const MAX_BODY = 4096;
const LEVELS = { beginner: 'Beginner', intermediate: 'Intermediate', senior: 'Senior Admin / DevOps' };
const EVENTS = new Set(['session_start', 'diagnostic_complete', 'module_complete', 'readiness', 'practice_complete', 'final_report']);
const COLORS = {
  session_start: 0xf59e0b, diagnostic_complete: 0x3b82f6, module_complete: 0x10b981,
  readiness: 0xa855f7, practice_complete: 0xef4444, final_report: 0x22c55e,
};

// Best-effort throttle per warm instance. It needs no database; a cold start resets it.
const RATE = { windowMs: 10 * 60 * 1000, max: 40 };
const hits = new Map();

function json(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store' },
  });
}

function rateLimited(ip) {
  const now = Date.now();
  if (hits.size > 5000) for (const [k, v] of hits) if (now - v.start > RATE.windowMs) hits.delete(k);
  const h = hits.get(ip);
  if (!h || now - h.start > RATE.windowMs) { hits.set(ip, { start: now, n: 1 }); return false; }
  return ++h.n > RATE.max;
}

// Replace control, zero-width and bidi-override characters, collapse whitespace, cap the length.
const unsafeChar = (c) => c < 32 || (c >= 127 && c <= 159) || (c >= 0x200b && c <= 0x200f) || (c >= 0x2028 && c <= 0x202e);
const clean = (v, max = 60) => Array.from(String(v ?? ''), (ch) => (unsafeChar(ch.codePointAt(0)) ? ' ' : ch))
  .join('').replace(/\s+/g, ' ').trim().slice(0, max);
const int = (v, min, max) => (Number.isInteger(v) && v >= min && v <= max ? v : null);
const list = (v, maxItems = 10) => (Array.isArray(v) ? v.slice(0, maxItems).map((x) => clean(x, 48)).filter(Boolean) : []);
const isDate = (s) => typeof s === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(s) && !Number.isNaN(Date.parse(`${s}T00:00:00Z`));

/** Validate the event and return the normalized fields, or { error }. */
function parseEvent(body) {
  if (!body || typeof body !== 'object' || !EVENTS.has(body.event)) return { error: 'Unknown event.' };
  const d = body.data && typeof body.data === 'object' ? body.data : {};
  const name = clean(d.name);
  if (!name) return { error: 'Name is required.' };
  const sessionId = typeof body.sessionId === 'string' && /^[A-Za-z0-9-]{8,64}$/.test(body.sessionId) ? body.sessionId : null;
  if (body.event !== 'session_start' && !sessionId) return { error: 'Session id is required.' };
  const base = { event: body.event, sessionId, name };

  switch (body.event) {
    case 'session_start': {
      if (!LEVELS[d.level]) return { error: 'Choose an experience level.' };
      if (!isDate(d.examDate)) return { error: 'Enter a valid target exam date.' };
      return { ...base, level: LEVELS[d.level], examDate: d.examDate };
    }
    case 'diagnostic_complete': {
      const scorePct = int(d.scorePct, 0, 100), correct = int(d.correct, 0, 500), total = int(d.total, 1, 500);
      if (scorePct == null || correct == null || total == null) return { error: 'Invalid score.' };
      return { ...base, scorePct, correct, total, run: int(d.run, 1, 999) || 1, weakAreas: list(d.weakAreas) };
    }
    case 'module_complete': {
      const masteryPct = int(d.masteryPct, 0, 100), answered = int(d.answered, 1, 10000), module = clean(d.module, 40);
      if (masteryPct == null || answered == null || !module) return { error: 'Invalid module result.' };
      return { ...base, module, masteryPct, answered };
    }
    case 'readiness': {
      const milestone = int(d.milestone, 1, 100), weightedPct = int(d.weightedPct, 0, 100), answered = int(d.answered, 1, 10000);
      if (milestone == null || weightedPct == null || answered == null) return { error: 'Invalid readiness metric.' };
      return { ...base, milestone, weightedPct, answered, weakAreas: list(d.weakAreas) };
    }
    case 'practice_complete': {
      const set = int(d.set, 1, 20), setsTotal = int(d.setsTotal, 1, 20), scorePct = int(d.scorePct, 0, 100);
      const correct = int(d.correct, 0, 500), total = int(d.total, 1, 500), durationSec = int(d.durationSec, 0, 86400);
      if ([set, setsTotal, scorePct, correct, total, durationSec].includes(null) || set > setsTotal) return { error: 'Invalid practice result.' };
      return { ...base, set, setsTotal, scorePct, correct, total, durationSec };
    }
    case 'final_report': {
      const readinessPct = int(d.readinessPct, 0, 100), diagnosticPct = int(d.diagnosticPct, 0, 100), practiceAvgPct = int(d.practiceAvgPct, 0, 100);
      const setsCompleted = int(d.setsCompleted, 0, 20), setsTotal = int(d.setsTotal, 1, 20), verdict = clean(d.verdict, 60);
      if ([readinessPct, diagnosticPct, practiceAvgPct, setsCompleted, setsTotal].includes(null) || !verdict) return { error: 'Invalid final report.' };
      return { ...base, readinessPct, verdict, diagnosticPct, practiceAvgPct, setsCompleted, setsTotal,
        topicsMastered: list(d.topicsMastered), strengths: list(d.strengths), weakAreas: list(d.weakAreas) };
    }
  }
  return { error: 'Unknown event.' };
}

const mmss = (sec) => `${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, '0')}`;

/** Platform-neutral message: a title and body lines. */
function compose(e, now) {
  const stamp = `${now.toISOString().slice(0, 19).replace('T', ' ')} UTC`;
  const join = (a, none) => (a.length ? a.join(', ') : none);
  const weak = (a) => join(a, 'None, every area is at or above the benchmark');
  let title, lines;
  switch (e.event) {
    case 'session_start':
      title = '📝 New User Training Session Initialized!';
      lines = [`• Name: ${e.name} | Experience: ${e.level} | Target Date: ${e.examDate} | Time: ${stamp}`];
      break;
    case 'diagnostic_complete':
      title = '📊 Diagnostic Test Completed';
      lines = [`• User ${e.name} scored ${e.scorePct}% (${e.correct}/${e.total}) on Diagnostic Test${e.run > 1 ? ` (attempt #${e.run})` : ''}.`,
        `• Weak areas: ${weak(e.weakAreas)}`, `• Time: ${stamp}`];
      break;
    case 'module_complete':
      title = '✅ Topic Module Finished';
      lines = [`• User ${e.name} finished the ${e.module} module with ${e.masteryPct}% mastery over ${e.answered} scored answers.`, `• Time: ${stamp}`];
      break;
    case 'readiness':
      title = '🏁 Exam Readiness Milestone';
      lines = [`• User ${e.name} reached ${e.weightedPct}% weighted mastery (milestone ${e.milestone}%) over ${e.answered} answers.`,
        `• Weak areas: ${weak(e.weakAreas)}`, `• Time: ${stamp}`];
      break;
    case 'practice_complete':
      title = `🎬 Practice Set ${e.set}/${e.setsTotal} Completed`;
      lines = [`• User ${e.name} completed Practice Set ${e.set}/${e.setsTotal}. Score: ${e.scorePct}% (${e.correct}/${e.total}) in ${mmss(e.durationSec)}.`,
        `• Time: ${stamp}`];
      break;
    case 'final_report':
      title = '🏆 Final Training Summary Report';
      lines = [
        `• Name: ${e.name}`,
        `• Certification Readiness Score: ${e.readinessPct}% (${e.verdict})`,
        `• Diagnostic: ${e.diagnosticPct}% | Practice sets: ${e.setsCompleted}/${e.setsTotal}, average ${e.practiceAvgPct}%`,
        `• Topics mastered: ${join(e.topicsMastered, 'None required')}`,
        `• Strengths: ${join(e.strengths, 'None above 85% yet')}`,
        `• Weak areas: ${weak(e.weakAreas)}`,
        `• Time: ${stamp}`,
      ];
      break;
  }
  return { title, lines, footer: e.sessionId ? `Session ${e.sessionId}` : '' };
}

const mdEscape = (s) => String(s).replace(/([\\*_~`|>#\[\]()<@])/g, '\\$1');
const htmlEscape = (s) => String(s).replace(/[&<>]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]));

/** Build the outgoing request for the configured webhook. */
function target(rawUrl, msg, e, now) {
  const url = new URL(rawUrl);
  const host = url.hostname.toLowerCase();

  if (/(^|\.)discord(app)?\.com$/.test(host) && url.pathname.startsWith('/api/webhooks/')) {
    return {
      url: url.href,
      body: {
        username: 'SAA-C03 Portal',
        allowed_mentions: { parse: [] },
        embeds: [{
          title: msg.title,
          description: msg.lines.map(mdEscape).join('\n'),
          color: COLORS[e.event],
          footer: msg.footer ? { text: msg.footer } : undefined,
          timestamp: now.toISOString(),
        }],
      },
    };
  }

  if (host === 'api.telegram.org') {
    const chatId = url.searchParams.get('chat_id') || process.env.TELEGRAM_CHAT_ID;
    if (!chatId) throw new Error('Telegram webhook is missing chat_id.');
    const text = [`<b>${htmlEscape(msg.title)}</b>`, ...msg.lines.map(htmlEscape), msg.footer ? `<i>${htmlEscape(msg.footer)}</i>` : '']
      .filter(Boolean).join('\n');
    url.search = '';
    return { url: url.href, body: { chat_id: chatId, text, parse_mode: 'HTML', disable_web_page_preview: true } };
  }

  const text = [msg.title, ...msg.lines, msg.footer].filter(Boolean).join('\n');
  return { url: url.href, body: { text, content: text, event: e.event } };
}

export async function POST(request) {
  const webhook = process.env.PROCESS_WEBHOOK_URL;
  if (!webhook) return json({ ok: false, error: 'Tracking is not configured yet.' }, 503);

  // Only accept calls from the portal's own pages.
  const origin = request.headers.get('origin');
  const host = request.headers.get('x-forwarded-host') || request.headers.get('host');
  if (origin) {
    let same = false;
    try { same = new URL(origin).host === host; } catch { /* malformed origin */ }
    if (!same) return json({ ok: false, error: 'Forbidden.' }, 403);
  }

  const ip = (request.headers.get('x-forwarded-for') || '').split(',')[0].trim() || request.headers.get('x-real-ip') || 'unknown';
  if (rateLimited(ip)) return json({ ok: false, error: 'Too many requests. Please try again in a few minutes.' }, 429);

  if (!(request.headers.get('content-type') || '').includes('application/json')) return json({ ok: false, error: 'Expected JSON.' }, 415);
  const raw = await request.text();
  if (raw.length > MAX_BODY) return json({ ok: false, error: 'Request too large.' }, 413);
  let body;
  try { body = JSON.parse(raw); } catch { return json({ ok: false, error: 'Invalid JSON.' }, 400); }

  const e = parseEvent(body);
  if (e.error) return json({ ok: false, error: e.error }, 400);
  if (e.event === 'session_start' && !e.sessionId) e.sessionId = crypto.randomUUID();

  const now = new Date();
  let out;
  try { out = target(webhook, compose(e, now), e, now); } catch (err) {
    console.error('track-session: bad webhook configuration:', err.message);
    return json({ ok: false, error: 'Tracking is misconfigured.' }, 503);
  }

  try {
    const res = await fetch(out.url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(out.body),
      signal: AbortSignal.timeout(6000),
    });
    if (!res.ok) {
      console.error(`track-session: webhook answered ${res.status}: ${(await res.text()).slice(0, 300)}`);
      return json({ ok: false, error: 'The notification service rejected the message.' }, 502);
    }
  } catch (err) {
    console.error('track-session: webhook unreachable:', err.name, err.message);
    return json({ ok: false, error: 'The notification service is unreachable.' }, 504);
  }

  return json({ ok: true, sessionId: e.sessionId });
}

export function GET() {
  return json({ ok: false, error: 'Use POST.' }, 405);
}
