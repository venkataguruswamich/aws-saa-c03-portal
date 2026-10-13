# AWS SAA-C03 Smart Adaptive Portal

This is a single-file SPA (`index.html`). Your logo is embedded inside it, so it needs no build step and no database. Progress is stored in your browser's `localStorage`. One small serverless function, `api/track-session.js`, sends onboarding and progress notifications to a webhook.

## Run

```bash
python -m http.server 8090 --directory saa-c03-smart-portal
```

Then open http://localhost:8090.

You can also double-click `index.html`. Everything works, **except** YouTube embeds: YouTube refuses to play inside pages opened from `file://`. Serve the file over HTTP when you want the masterclass videos.

## Learning flow

1. **Identity (Phase 1, required).** Full name, AWS experience level and target exam date. Until this is submitted, nothing else renders. See *Onboarding gate* below.
2. **Diagnostic (Phase 2).** A 20-question pool in four styles: multiple choice, choose-two multi-select, true/false, and fill in the blank. Question order and answer choices are reshuffled (Fisher-Yates, Web Crypto RNG) on every page load. Answers are stored against the original option indices, so scoring never drifts. You get no feedback until you submit.
3. **Analytics Hub.** Domain tracking table, a heatmap of domain × skill area, the Blind Spot Report (any concept below 72%, which is roughly 720/1000), a concept tracking table and an answer log.
4. **Remediation Zone (Phase 3).** The diagnostic is scored across six categories: Compute, Storage, Networking, Databases & Analytics, Security & Identity, and Management & Messaging. Each category below 72% is flagged **Must Master**. Every category covers each blueprint service in plain language (what it is, when to use it, an exam tip), step-by-step rules and a production example. Each also has an **animated serverless web application diagram**: an SVG whose data flows move using CSS `stroke-dashoffset` keyframes, with the category's components highlighted. Click a component to see what it does. The diagram covers:
   - Users
   - CloudFront and the S3 static website
   - Cognito
   - API Gateway and the Lambda functions, inside the IAM security scope with the execution role
   - DynamoDB and the S3 assets bucket
   - CloudWatch, pulsing alerts to SNS

   **Practice exams stay locked until every weak topic is marked "Mastered".**
5. **Coaching Core.** Adaptive drills with the Instant Tutor and the **Master It** masterclass drawer.
6. **Practice Exams (Phase 4).** Six exam-style sets of 10 questions each, in a YouTube-style player:
   - a centered question and large choices;
   - a 2:00 countdown per question that turns red with overtime;
   - a question timeline and "Lock answer";
   - a slide-out explanation canvas marking every option correct or incorrect.

   Finishing a set sends a `practice_complete` event.
7. **Final Result (Phase 5).** Certification Readiness Score = diagnostic 20% + remediation 10% + practice sets 70% (unfinished sets count as 0%). It shows:
   - each of the **4 exam domains** with a READY / NOT READY indicator;
   - a binary **"Highly ready to clear the live exam: YES / NOT YET"**;
   - strengths and weaknesses by category.

   The full learning-history report is sent automatically when all six sets are done, and can be re-sent with a button. It includes domains, set scores, mastered topics, strengths, weak areas, answers and time.

Rendering runs inside an error boundary: if a view fails, the learner sees a recovery card instead of a blank page. Tracking never throws into the UI.

Mastery counts only your **first** answer each time a scenario is served. Retries are logged but not scored.

## Customize

Edit the `CONFIG` block at the top of the `<script>`:

| Setting | Purpose |
|---|---|
| `ownerName` | `Venkataguruswami`. Shown beside the logo and pre-filled as the learner name. |
| `logo` | Embedded default logo (the transparent VG cloud logo, as a data URI). You can also replace it per browser in **Settings**. |
| `stuckThreshold` | Default `0.72`. |
| `masterItAfterWrong` | Default `2`. The number of misses before Master It is highlighted as recommended. |
| `trainingSets` | Hard-code a YouTube video or playlist URL for each concept. Links pasted in the drawer override these. |

To add questions, append to `DIAGNOSTIC` or `DRILLS`. Each question needs a `concept` key that exists in `CONCEPTS`.

## Deploy

The portal is one static file. This repo is connected to Vercel, so every push to `main` deploys to production automatically. There is no build step.

| File | Purpose |
|---|---|
| `index.html` | The whole portal. |
| `vercel.json` | Static hosting with no framework or build, and security headers. |
| `sw.js` | Cleans up after the previous version: it removes that version's offline service worker and its caches for returning visitors. |
| `api/track-session.js` | Serverless function. It relays onboarding and progress events to your Discord or Telegram webhook and stores nothing (see below). |
| `package.json` | Marks the project as ES modules so Vercel can run `api/track-session.js`. It has no dependencies. |

## Onboarding gate and progress stream

Until a visitor submits **Step 1 · Identity identification**, the portal renders nothing else: no header, no navigation and no views. The Step 1 card asks for a full name, an AWS experience level and a target exam date. Submitting it posts a `session_start` event to `/api/track-session`, unlocks the portal and opens the diagnostic test.

The session token lives in `sessionStorage`, so a new browser session shows Step 1 again. Nothing is pre-filled. Entering the same name resumes the saved progress; a different name starts a fresh profile, so one learner never sees another's data.

The portal then streams these progress events to the same endpoint:

| Event | When |
|---|---|
| `diagnostic_complete` | On each diagnostic submission. It includes the score and the weak areas. |
| `module_complete` | The first time every drill in a skill area (Decoupling, High Availability, …) has been answered. |
| `readiness` | When weighted mastery first reaches 72%, and again at 85%, after at least 10 scored answers. |
| `practice_complete` | Each finished practice set: "User X completed Practice Set 3/6. Score: 82%". |
| `final_report` | Readiness score, verdict, diagnostic and practice averages, mastered topics, strengths and weak areas. |

If the network or the webhook fails, nothing breaks. The visitor is let in anyway, and events wait in a `localStorage` outbox that retries with back-off (and when the browser comes back online) for up to 7 days.

The gate is a client-side funnel, not access control. The question bank still ships inside `index.html`.

Set these in **Vercel → Project → Settings → Environment Variables**, then redeploy:

| Variable | Required | Value |
|---|---|---|
| `PROCESS_WEBHOOK_URL` | yes | Discord: `https://discord.com/api/webhooks/<id>/<token>`<br>Telegram: `https://api.telegram.org/bot<token>/sendMessage?chat_id=<chat id>` |
| `TELEGRAM_CHAT_ID` | no | Telegram chat id, used if it isn't already in the URL. |

The webhook URL is a secret. Keep it only in Vercel and never commit it.

It also works on S3 + CloudFront, Amplify Hosting, Netlify or GitHub Pages.

> Note: AWS App Mesh (bootcamp scenario 5) reached end of support on 30 Sep 2026. The scenario is kept because it appears in exam material. The tutor's explanation names the current alternatives.
