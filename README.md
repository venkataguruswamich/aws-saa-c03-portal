# SAA-C03 Mock Test & Cloud Lab Portal

This is an all-in-one study portal for the **AWS Certified Solutions Architect – Associate (SAA-C03)** exam. It includes:
- timed practice exams
- a study guide with AWS architecture diagrams
- hands-on AWS labs
- a Linux administration lab
- a technical interview simulator
- a live AWS dependency monitor

The portal is a single web page (`index.html`) that runs entirely in the browser. It can be installed on a phone and works **offline**. A small Flask backend (`app.py`) is needed only for the **Live Lab** tab.

---

## Table of contents
1. [Features](#1-features)
2. [Project structure](#2-project-structure)
3. [Prerequisites](#3-prerequisites)
4. [Local installation](#4-local-installation)
5. [Configuration (environment variables)](#5-configuration-environment-variables)
6. [Using the portal](#6-using-the-portal)
7. [Mobile and offline access](#7-mobile-and-offline-access)
8. [Deployment](#8-deployment)
9. [Troubleshooting](#9-troubleshooting)
10. [Updating the portal](#10-updating-the-portal)
11. [Command cheat sheet](#11-command-cheat-sheet)

---

## 1. Features

| Tab | What it does |
|---|---|
| **Dashboard** | Countdown to the exam date (18 Oct 2026), performance summary and score-trajectory chart for the six practice sets. |
| **Mock Exam** | Six practice sets plus a full-length simulation: 65 questions (50 scored + 15 unscored), 130 minutes and domain weights 30/26/24/20. **Timed mode** auto-submits and locks when time runs out. **Review mode** has "Check Answer" on every question. |
| **Study Guide** | 14 curriculum modules with core notes, 126 AWS services (each with a use case and a memory aid), 25 architecture diagrams drawn with the official AWS icons, and 11 hands-on labs with console and CLI steps. |
| **Linux Lab** | Two modules (Basic/Intermediate and Advanced) and five troubleshooting drills in a simulated terminal: permission denied, disk full, SSH timeout, broken network config and a crashing service. |
| **Interview** | Technical panel simulator for a Lead Infrastructure Engineer (4-year level). It asks one question at a time and scores each answer on STAR structure, Linux commands, AWS services, seniority signals and measurable results. A model answer is shown after each one. |
| **Performance Report** | Scaled score (100–1000, pass 720), per-domain and per-module results, and remediation for anything below 72%. Each missed question explains why the other options are wrong. |
| **Live Lab** | Real connectivity checks from the server to Amazon RDS, ElastiCache (Redis) and DynamoDB, plus EC2 instance metadata (IMDSv2). Needs `app.py`. |

It also has:
- a dark/light theme switch
- an exam Blueprint drawer
- a startup system test (storage, network and screen size)
- a terminal-style status log

All progress is saved in the browser's LocalStorage.

---

## 2. Project structure

```
saa-c03-portal/
├── index.html             # The complete portal (HTML + CSS + JavaScript, no build step)
├── manifest.webmanifest   # App name/icon/colours for "Add to Home screen"
├── sw.js                  # Service worker: caches the portal so it works offline
├── icons/                 # Logo and app icons
│   ├── logo.jpg
│   ├── icon-192.png
│   ├── icon-512.png
│   ├── icon-maskable-512.png
│   └── apple-touch-icon.png
├── app.py                 # Flask backend: serves the portal + Live Lab API
├── requirements.txt       # Python packages for app.py
├── Dockerfile             # Container image for the backend
├── .gitignore
└── README.md              # This file
```

| Endpoint (from `app.py`) | Purpose |
|---|---|
| `GET /` | The portal |
| `GET /manifest.webmanifest`, `/sw.js`, `/icons/*`, `/favicon.ico` | App-install and offline files |
| `GET /api/health` | Liveness check |
| `GET /api/lab/status` | Latest Live Lab check results |
| `POST /api/lab/refresh` | Run the checks again (rate limited) |

---

## 3. Prerequisites

| Software | Version | Needed for | Download |
|---|---|---|---|
| A modern browser | Chrome, Edge, Firefox or Safari (latest) | Everything | – |
| **Python** | 3.10 or newer (3.11 recommended) | Running `app.py` locally | https://www.python.org/downloads/ (tick **"Add python.exe to PATH"** on Windows) |
| pip | Comes with Python | Installing packages | – |
| Git *(optional)* | Any | Version control and GitHub Pages | https://git-scm.com/downloads |
| Docker Desktop *(optional)* | 24 or newer | Running in a container | https://www.docker.com/products/docker-desktop/ |
| AWS CLI v2 *(optional)* | 2.x | S3/CloudFront deployment, DynamoDB credentials, labs | https://aws.amazon.com/cli/ |

Python packages (installed from `requirements.txt`):
- `Flask`: the web server.
- `boto3`: the DynamoDB check.
- `gunicorn`: the production server. It is Linux/macOS only and is skipped automatically on Windows.

Check what you already have:
```bash
python --version
pip --version
git --version
docker --version
aws --version
```

---

## 4. Local installation

### Option 1: Open the file directly (no install)
Double-click `index.html`. Practice tests, study guide, Linux Lab and Interview all work. **Live Lab** and **offline install** do not work this way; use Option 2 for those.

### Option 2: Run with Python (recommended)

**Windows (PowerShell)**
```powershell
cd "D:\Guru Documents\saa-c03-portal"
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
$env:IMDS_DISABLED = "true"
python app.py
```

**macOS / Linux**
```bash
cd saa-c03-portal
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
export IMDS_DISABLED=true
python app.py
```

Open **http://localhost:5000** in your browser. Stop the server with `Ctrl + C`.

The next time, you only need to activate the environment and run the app:
```powershell
cd "D:\Guru Documents\saa-c03-portal"
.\.venv\Scripts\Activate.ps1
python app.py
```

`IMDS_DISABLED=true` skips the EC2 metadata lookup, which only works on an EC2 instance. Leave it out when running on EC2.

### Option 3: Run with Docker
```bash
cd saa-c03-portal
docker build -t saa-portal .
docker run --rm -p 5000:5000 -e IMDS_DISABLED=true saa-portal
```
Open **http://localhost:5000**.

### Check that it is running
```bash
curl http://localhost:5000/api/health
```
Expected output: `{"ok": true, "time": "..."}`

---

## 5. Configuration (environment variables)

All settings are optional. Without them, the portal works and the Live Lab shows each AWS tier as **UNCONFIGURED**.

| Variable | Default | Meaning |
|---|---|---|
| `HOST` | `127.0.0.1` | Address to listen on. Use `0.0.0.0` to allow other devices (e.g. your phone) |
| `PORT` | `5000` | Port to listen on |
| `RDS_HOST` / `RDS_PORT` | – / `5432` | RDS endpoint to test (use `3306` for MySQL) |
| `REDIS_HOST` / `REDIS_PORT` | – / `6379` | ElastiCache primary endpoint |
| `REDIS_TLS` | `false` | `true` if ElastiCache in-transit encryption is on |
| `AWS_REGION` | `us-east-1` | Region for DynamoDB |
| `DYNAMODB_TABLE` | – | Table to describe; if empty, a `ListTables` call is used |
| `DYNAMODB_ENDPOINT_URL` | – | e.g. `http://localhost:8000` for DynamoDB Local |
| `IMDS_DISABLED` | `false` | `true` when not running on EC2 |
| `CHECK_INTERVAL_SECONDS` | `10` | How often the background checks run |
| `SOCKET_TIMEOUT_SECONDS` | `3` | Timeout for each connection check |
| `MAX_CONCURRENT_SOCKETS` | `6` | Maximum simultaneous check connections |
| `PROXY_X_FOR_HOPS` | `2` | Trusted proxies in `X-Forwarded-For` (`2` = ALB + nginx, `1` = ALB only, `0` = none) |
| `CORS_ALLOW_ORIGIN` | `*` | Origin allowed to call the API when the portal is hosted elsewhere |
| `LOG_LEVEL` | `INFO` | `DEBUG` for more detail |

Example of setting variables for one run:
```powershell
$env:RDS_HOST = "mydb.abc123.us-east-1.rds.amazonaws.com"; $env:RDS_PORT = "5432"
$env:REDIS_HOST = "my-cache.abc123.use1.cache.amazonaws.com"
$env:DYNAMODB_TABLE = "Orders"; $env:AWS_REGION = "us-east-1"
python app.py
```
```bash
RDS_HOST=mydb.abc123.us-east-1.rds.amazonaws.com REDIS_HOST=my-cache.abc123.use1.cache.amazonaws.com \
DYNAMODB_TABLE=Orders AWS_REGION=us-east-1 python app.py
```

The DynamoDB check uses your normal AWS credentials: `aws configure`, environment variables, or an IAM role on EC2.

---

## 6. Using the portal

1. **First run:** the **System Test** banner checks storage, network and screen size. Click **Continue**.
2. **Exam:** open **Mock Exam**, pick a set and choose one of:
   - **Timed:** 130 minutes, locks when time is up.
   - **Review:** instant *Check Answer*.

   Answers, flags and the timer survive a page refresh.
3. **Report:** after submitting, **Performance Report** shows:
   - your scaled score
   - weak domains and modules (below 72%)
   - explanations
   - diagrams and labs for each weak area
4. **Study:** the left sidebar lists the 14 modules. Use the search box to find services.
5. **Linux Lab:** type real commands into the drill terminal. `hint` shows a hint, `answer` shows the expected command, and `clear` clears the screen.
6. **Interview:** answer each question in STAR format (Situation, Task, Action, Result). Mention real commands and AWS services, then click **Submit answer** for your score and a model answer.
7. **Blueprint:** the button in the top bar shows official exam facts.
8. **Theme:** the ☀/☾ button switches between dark and light mode.
9. **Logo:** use **Change logo** in the sidebar to use your own image (max 256 KB). Its background is removed automatically. **Reset** restores the default.

---

## 7. Mobile and offline access

The portal is an installable web app (PWA). Offline mode and installing need **HTTPS**. The only exception is `localhost` on the same computer.

| How you open it | On phone? | Offline? |
|---|---|---|
| `http://localhost:5000` on your PC | – | ✅ (on the PC) |
| `http://<PC-IP>:5000` over Wi-Fi | ✅ | ❌ (not HTTPS) |
| An `https://` hosted URL (see [Deployment](#8-deployment)) | ✅ | ✅ |

**Quick phone test on the same Wi-Fi (online only):**
```powershell
$env:HOST = "0.0.0.0"; $env:IMDS_DISABLED = "true"; python app.py
ipconfig
```
Find the **IPv4 Address** (e.g. `192.168.1.20`) and open `http://192.168.1.20:5000` on the phone. Allow Python through Windows Firewall if asked.

**Install on the phone (from an HTTPS URL):**
1. Open the URL once while online. After about 5 seconds the status log shows *"Offline mode ready"*.
2. Add it to your home screen:
   - **Android (Chrome):** menu ⋮ → **Install app** / **Add to Home screen**.
   - **iPhone (Safari):** Share → **Add to Home Screen**.
3. Open it from the home-screen icon. It runs full screen and works in airplane mode. Progress is stored on the phone.

---

## 8. Deployment

**Files needed for the static portal:** `index.html`, `manifest.webmanifest`, `sw.js`, `icons/`.
**Also needed for the Live Lab:** `app.py`, `requirements.txt`, `Dockerfile`.

### A. Free static hosting (easiest; HTTPS, phone and offline)

- **Netlify Drop:**
  1. Put the four static items in a folder.
  2. Open https://app.netlify.com/drop and drag the folder in.
  3. You get `https://<name>.netlify.app`.
- **GitHub Pages:**
  ```bash
  git init
  git add index.html manifest.webmanifest sw.js icons README.md
  git commit -m "SAA-C03 portal"
  git branch -M main
  git remote add origin https://github.com/<you>/saa-c03-portal.git
  git push -u origin main
  ```
  Then go to **Settings → Pages** and set Source to `main` and folder `/ (root)`. Your URL is `https://<you>.github.io/saa-c03-portal/`.
- **Cloudflare Pages:** go to **Workers & Pages → Create → Pages → Upload assets**.

### B. AWS S3 + CloudFront
```bash
BUCKET=saa-portal-$RANDOM
aws s3 mb s3://$BUCKET --region us-east-1
aws s3 cp index.html s3://$BUCKET/ --content-type text/html --cache-control no-cache
aws s3 cp sw.js s3://$BUCKET/ --content-type text/javascript --cache-control no-cache
aws s3 cp manifest.webmanifest s3://$BUCKET/ --content-type application/manifest+json
aws s3 cp icons s3://$BUCKET/icons --recursive
```
1. Create a CloudFront distribution:
   - Origin: the bucket, with **Origin Access Control** (keep the bucket private).
   - Default root object: `index.html`.
   - Viewer protocol: **Redirect HTTP to HTTPS**.
2. Apply the bucket policy CloudFront suggests.
3. Open `https://dxxxx.cloudfront.net`.
4. Optional: add a custom domain with Route 53 and an ACM certificate in us-east-1.

### C. Full stack with the Live Lab (Docker)
```bash
docker build -t saa-portal .
docker run -d --name saa-portal -p 5000:5000 \
  -e RDS_HOST=... -e REDIS_HOST=... -e DYNAMODB_TABLE=... -e AWS_REGION=us-east-1 \
  saa-portal
```
Run it on **EC2** behind an ALB with an ACM certificate, or on **AWS App Runner**, which provides HTTPS automatically.

Set `PROXY_X_FOR_HOPS`:
- `2` for ALB → nginx → app
- `1` for ALB → app

If the portal is hosted statically (A/B) and the backend runs elsewhere:
- Enter the backend URL in the **Live Lab** tab's **Backend URL** field.
- Set `CORS_ALLOW_ORIGIN=https://<your-portal-domain>`.

---

## 9. Troubleshooting

Start with the **status log** at the bottom of the portal (click **Expand**) and the terminal running `app.py`. Most problems are explained there.

### Installation problems

| Problem | Cause | Fix |
|---|---|---|
| `'python' is not recognized` | Python not installed or not on PATH | Reinstall Python and tick **Add python.exe to PATH**, or use `py` instead of `python` (`py -m venv .venv`). |
| `Activate.ps1 cannot be loaded because running scripts is disabled` | PowerShell execution policy | Run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once (answer **Y**), or use Command Prompt: `.venv\Scripts\activate.bat`. |
| `ModuleNotFoundError: No module named 'flask'` | Virtual environment not active, or packages missing | Activate `.venv` (the prompt shows `(.venv)`), then `pip install -r requirements.txt`. |
| `pip install` fails behind a company proxy | Proxy blocks PyPI | `pip install --proxy http://<proxy>:<port> -r requirements.txt` |
| gunicorn error on Windows | gunicorn does not run on Windows | Expected. It is skipped on Windows; use `python app.py`. |

### Server problems

| Problem | Cause | Fix |
|---|---|---|
| `Address already in use` / port 5000 busy | Another program is using port 5000 (on macOS, often AirPlay Receiver) | Use another port: `$env:PORT = "8080"; python app.py`. Or find the process: `netstat -ano \| findstr :5000`, then `taskkill /PID <pid> /F`. On macOS/Linux: `lsof -i :5000`. |
| Browser shows "This site can't be reached" | Server not running, or wrong port | Check the terminal for `Running on http://127.0.0.1:5000`, then run `curl http://localhost:5000/api/health`. |
| Startup is slow or logs show IMDS timeouts | Not on EC2, so metadata calls time out | Set `IMDS_DISABLED=true`. |
| Live Lab tiers show **UNCONFIGURED** | No endpoint variables set | Set `RDS_HOST`, `REDIS_HOST` and/or `DYNAMODB_TABLE` (see [Configuration](#5-configuration-environment-variables)). |
| RDS/Redis shows **timeout** | Security group, NACL or route blocks the port, or the server is outside the VPC | Allow inbound 5432/3306/6379 from the app's security group. Test from the server with `nc -zv <host> <port>` (Linux) or `Test-NetConnection <host> -Port <port>` (Windows). |
| RDS/Redis shows **DNS error** | Wrong endpoint, or a private endpoint resolved from outside the VPC | Check the endpoint in the console with `nslookup <host>`. Private endpoints only resolve inside the VPC. |
| Redis connects but the check fails | TLS required | Set `REDIS_TLS=true`. |
| DynamoDB `NoCredentialsError` / `AccessDenied` | No AWS credentials, or missing permissions | Run `aws configure` or attach an IAM role with `dynamodb:DescribeTable` / `dynamodb:ListTables`. Check with `aws sts get-caller-identity`. |
| Log shows "Lab backend unreachable" when the portal is hosted separately | Wrong API URL, or CORS | Enter the backend URL in **Backend URL**, set `CORS_ALLOW_ORIGIN`, and make sure both sites use HTTPS. |

### Phone and offline problems

| Problem | Cause | Fix |
|---|---|---|
| Phone can't open `http://<PC-IP>:5000` | Server listens on localhost only, firewall blocks it, or different network | Start with `HOST=0.0.0.0`, allow Python in **Windows Defender Firewall**, and put both devices on the same Wi-Fi (not guest Wi-Fi). |
| No **Install app** option / "Offline mode unavailable" in the log | Page not served over HTTPS | Deploy with HTTPS (see [Deployment](#8-deployment)). Plain `http://` over Wi-Fi cannot install. |
| iPhone: no install prompt | iOS installs only through Safari | Open in **Safari**, then Share → **Add to Home Screen**. |
| Offline launch shows a blank or "no internet" page | The app was never fully loaded online | Open it once online, wait for *"Offline mode ready"*, then try offline. |

### Browser and app problems

| Problem | Cause | Fix |
|---|---|---|
| Old version still shows after an update | Service worker cache | Hard refresh (`Ctrl + Shift + R`). Or open DevTools (`F12`) → **Application** → **Service workers** → **Unregister**, then reload. When deploying, bump `CACHE` in `sw.js`. |
| Progress lost | Private/incognito window, cleared site data, or a different browser | Use a normal window in the same browser. Progress is stored per browser and per device. |
| Exam stuck or corrupted state | Old saved session | In DevTools Console: `localStorage.removeItem('saa.session.v3')`, then reload. |
| Reset everything (scores, theme, progress) | – | DevTools Console: `Object.keys(localStorage).filter(k => k.startsWith('saa.')).forEach(k => localStorage.removeItem(k)); location.reload();` |
| System Test warns about window scaling | Window smaller than 1100×600, or browser zoom not 100% | It still works (compact layout). Press `Ctrl + 0` to reset zoom. |
| System Test warns about network | Offline or slow connection | Everything except external documentation links still works. |
| Logo shows a box or the wrong image | A custom logo is saved in this browser | Sidebar → **Reset** next to the logo. |
| Diagrams or charts look wrong | Very old browser | Update to the latest Chrome, Edge, Firefox or Safari. |

### Docker problems

| Problem | Fix |
|---|---|
| `docker: command not found` or daemon not running | Install or start **Docker Desktop**, then run `docker info`. |
| Container exits immediately | Check the output with `docker logs saa-portal`. |
| Port conflict | Map another host port: `docker run -p 8080:5000 saa-portal`, then open `http://localhost:8080`. |

---

## 10. Updating the portal

1. Edit `index.html`. Everything, including the question bank, modules and labs, is inside this one file.
2. Open `sw.js` and increase `CACHE`, for example `saa-portal-v6` → `saa-portal-v7`. Installed phones then pick up the new version.
3. Restart `python app.py`, or redeploy the files.
4. Phones update the next time the app is opened online.

---

## 11. Command cheat sheet

```powershell
# --- Windows: first-time setup ---
cd "D:\Guru Documents\saa-c03-portal"
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# --- Windows: run ---
$env:IMDS_DISABLED = "true"; python app.py

# --- Windows: allow phone access on the same Wi-Fi ---
$env:HOST = "0.0.0.0"; $env:IMDS_DISABLED = "true"; python app.py
ipconfig

# --- Health check ---
curl http://localhost:5000/api/health
curl http://localhost:5000/api/lab/status

# --- Port 5000 busy ---
netstat -ano | findstr :5000
taskkill /PID <pid> /F

# --- Docker ---
docker build -t saa-portal .
docker run --rm -p 5000:5000 -e IMDS_DISABLED=true saa-portal
docker logs saa-portal
```

---

**Notes**
- Practice questions are original study material written in the SAA-C03 style, not real exam questions.
- Architecture diagrams use the official AWS Architecture Icons (via the MIT-licensed `aws-icons` package).
- Interview scoring runs locally in the browser using keyword and structure rules. It is a coaching aid, not an AI grader.
- Linux drill terminal output is simulated.
- Lab CLI commands use placeholder IDs such as `111122223333` and `vpc-…`. Replace them with your own, and delete lab resources afterwards to avoid charges.
