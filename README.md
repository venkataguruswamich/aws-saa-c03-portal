# AWS SAA-C03 Smart Adaptive Portal

This is a single-file SPA (`index.html`). Your logo is embedded inside it, so it needs no build step and no backend. Progress is stored in your browser's `localStorage`.

## Run

```bash
python -m http.server 8090 --directory saa-c03-smart-portal
```

Then open http://localhost:8090.

You can also double-click `index.html`. Everything works, **except** YouTube embeds: YouTube refuses to play inside pages opened from `file://`. Serve the file over HTTP when you want the masterclass videos.

## Learning flow

1. **Identity.** Enter your name. Until you do, every other phase stays locked.
2. **Diagnostic.** A forced 10-question baseline covering Decoupling, High Availability, Block vs File Storage and Private Network Routing. You get no feedback until you submit.
3. **Analytics Hub.** Domain tracking table, a heatmap of domain × skill area, the Blind Spot Report (any concept below 72%, which is roughly 720/1000), a concept tracking table and an answer log.
4. **Coaching Core.** Split screen: adaptive drills on the left, the Instant Tutor on the right. The **Master It** drawer unlocks when you miss a scenario twice or flag it as *Confused*.

Mastery counts only your **first** answer each time a scenario is served. Retries are logged but not scored.

## Customize

Edit the `CONFIG` block at the top of the `<script>`:

| Setting | Purpose |
|---|---|
| `ownerName` | `Venkataguruswami`. Shown beside the logo and pre-filled as the learner name. |
| `logo` | Embedded default logo (the transparent VG cloud logo, as a data URI). You can also replace it per browser in **Settings**. |
| `stuckThreshold` | Default `0.72`. |
| `masterItAfterWrong` | Default `2`. |
| `trainingSets` | Hard-code a YouTube video or playlist URL for each concept. Links pasted in the drawer override these. |

To add questions, append to `DIAGNOSTIC` or `DRILLS`. Each question needs a `concept` key that exists in `CONCEPTS`.

## Deploy

The portal is one static file. This repo is connected to Vercel, so every push to `main` deploys to production automatically. There is no build step.

| File | Purpose |
|---|---|
| `index.html` | The whole portal. |
| `vercel.json` | Static hosting with no framework or build, and security headers. |
| `sw.js` | Cleans up after the previous version: it removes that version's offline service worker and its caches for returning visitors. |

It also works on S3 + CloudFront, Amplify Hosting, Netlify or GitHub Pages.

> Note: AWS App Mesh (bootcamp scenario 5) reached end of support on 30 Sep 2026. The scenario is kept because it appears in exam material. The tutor's explanation names the current alternatives.
