# VG Learner Portal

**VG Learner Portal** is an all-in-one study portal for the **AWS Certified Solutions Architect – Associate (SAA-C03)** exam. It includes:
- timed practice exams
- a study guide with AWS architecture diagrams
- hands-on AWS labs
- a Linux administration lab
- a technical interview simulator
- a live AWS dependency monitor

Everything sits behind a simple **login page** (email and password). New users can **sign up**, but they must **verify their email address** before their first login. Two profiles exist: **Admin Profile** and **User Profile**. Only admins can see session tracking, the audit log and user accounts, and only admins can give other users admin access.

The portal itself is a single web page (`index.html`). It can be installed on a phone and works **offline** once you have logged in. A small Flask backend handles login, sessions and the Live Lab:
- `app.py`: the web server and Live Lab API.
- `auth.py`: login, profiles (roles), sessions and the audit log.

---

## Table of contents
1. [Features](#1-features)
2. [Project structure](#2-project-structure)
3. [Prerequisites](#3-prerequisites)
4. [Local installation](#4-local-installation)
5. [First login: the admin (root) account](#5-first-login-the-admin-root-account)
6. [Where everything is stored: users, passwords, logs, data](#6-where-everything-is-stored-users-passwords-logs-data)
7. [Login, profiles and user management](#7-login-profiles-and-user-management)
8. [Configuration (environment variables)](#8-configuration-environment-variables)
9. [Using the portal](#9-using-the-portal)
10. [Mobile and offline access](#10-mobile-and-offline-access)
11. [Deployment](#11-deployment)
12. [Troubleshooting](#12-troubleshooting)
13. [Updating the portal](#13-updating-the-portal)
14. [Command cheat sheet](#14-command-cheat-sheet)

---

## 1. Features

| Area | What it does |
|---|---|
| **Login page** | Nothing in the portal is visible until you log in. It works like a real-world login page: **Email**, **Password** (eye icon to show or hide it), **Remember me**, **Forgot password?**, **Login**, and **Sign up** for new users. Sign-up asks for full name, email, a password with a live strength meter, password confirmation and acceptance of the Terms & Privacy notice. Password reset uses a single-use link sent by email (or by an admin). Logged-in users can **Change password** from the account menu. The logo sits above the fields and blends into both themes. |
| **Dashboard** | Countdown to the exam date (18 Oct 2026), performance summary and score-trajectory chart for the six practice sets. |
| **Mock Exam** | Six practice sets plus a full-length simulation: 65 questions (50 scored + 15 unscored), 130 minutes and domain weights 30/26/24/20. **Timed mode** auto-submits and locks when time runs out. **Review mode** has "Check Answer" on every question. |
| **Study Guide** | Two sections. **Curriculum modules**: 14 modules with core notes, 126 AWS services (each with a use case and a memory aid), 25 architecture diagrams drawn with the official AWS icons, and 11 hands-on labs. **SAA-C03 Pass Strategy Guide**: a day-by-day plan to score above 720, exam-room tactics, the 6 Well-Architected pillars (principles, key services, what the exam tests), 10 service handnote boxes (S3, EC2 & EBS, VPC, IAM, RDS/Aurora, DynamoDB/DAX, CloudFront/Route 53, SQS/SNS/EventBridge, Lambda, CloudWatch/CloudTrail/KMS), a keyword-to-service map, and Practice Labs 1–4 (auto-healing ASG + ALB + CloudWatch alarms; VPC isolation with IGW/NAT/EIPs/route tables; S3 policies + CRR + lifecycle to Deep Archive; RDS Multi-AZ + Aurora Serverless v2 Global Database + DAX). |
| **Linux Lab** | Three modules: L1 Basic/Intermediate, L2 Advanced, and **L3 Linux & Networking Boot Camp** (directory layout, grep/awk/sed pipelines, apt vs dnf, systemctl/journalctl, permission and disk lab playbooks, online volume growth, OSI model, subnet/CIDR table, routing tables, and a socket/port diagnostic cheat sheet). Five troubleshooting drills in a simulated terminal: permission denied, disk full, SSH timeout, broken network config and a crashing service. |
| **Interview** | Two sections. **Panel interview simulator**: nine questions for a Lead Infrastructure Engineer (4-year level), one at a time, scored on STAR structure, Linux commands, AWS services, seniority signals and measurable results, with a model answer after each. **Interview Practice Center**: a Q&A library in Basic, Intermediate and Advanced tracks (stateless vs stateful, drift, CI/CD bottlenecks, GitFlow vs trunk-based; Terraform drift, multi-stage Dockerfiles, persistent volumes, nodeSelector; STAR post-mortems on thread starvation, OOMKilled, intermittent private-endpoint timeouts and multi-Region replication), with progress tracking. |
| **Performance Report** | Scaled score (100–1000, pass 720), per-domain and per-module results, and remediation for anything below 72%. Each missed question explains why the other options are wrong. |
| **Live Lab** | Real connectivity checks from the server to Amazon RDS, ElastiCache (Redis) and DynamoDB, plus EC2 instance metadata (IMDSv2). |
| **Session Tracking & Audit** *(Admin Profile only)* | The **Live Session Tracking & Enterprise Audit Logs** panel:<br>• the **Active Concurrent Sessions Logged In** counter<br>• login statistics<br>• the **Enterprise Audit Log Matrix** (Timestamp, User, Event Action, Client Source IP, Session Status), with a **Revoke Session** button on every row<br>It refreshes every 5 seconds. For a User Profile, this tab and its panels are not in the page at all, and the server refuses its data. |

It also has:
- a dark/light theme switch
- an exam Blueprint drawer
- a startup system test (storage, network and screen size)
- a terminal-style status log

Study progress is saved in the browser's LocalStorage. Users, sessions and the audit log are stored on the server in `data/portal.db`.

---

## 2. Project structure

```
saa-c03-portal/
├── index.html             # The complete portal (HTML + CSS + JavaScript, no build step)
├── manifest.webmanifest   # App name/icon/colours for "Add to Home screen"
├── sw.js                  # Service worker: caches the portal so it works offline
├── icons/                 # Logo and app icons
├── app.py                 # Flask backend: serves the portal + Live Lab API
├── auth.py                # Login, profiles (roles), sessions, audit log + user-management commands
├── requirements.txt       # Python packages
├── Dockerfile             # Container image
├── .env.example           # Settings template (email server, sign-up mode…): copy to .env
├── .gitignore
├── README.md              # This file
└── data/portal.db         # Created automatically: users, sessions, audit log (never commit it)
```

| Endpoint | Access | Purpose |
|---|---|---|
| `GET /` and the app files (`/sw.js`, `/manifest.webmanifest`, `/icons/*`) | Public | The portal (it shows the login page first) |
| `GET /api/health` | Public | Liveness check |
| `GET /api/auth/config` | Public | Whether login is enabled and accounts exist |
| `POST /api/auth/login` | Public | Email + password (+ code if the account uses MFA) → session token and profile |
| `GET /api/auth/session` | Signed in | Session check (the browser calls it every 5 s) |
| `POST /api/auth/logout` | Signed in | End the session |
| `POST /api/auth/signup` | Public | Create a User Profile account (unverified until the email link is opened) |
| `POST /api/auth/verify`, `POST /api/auth/resend-verification` | Public | Confirm an email address with its link / ask for a new link |
| `POST /api/auth/forgot`, `GET`/`POST /api/auth/reset` | Public | Request a password-reset link / check it / set the new password |
| `POST /api/auth/change-password` | Signed in | Change your own password |
| `GET /api/admin/users`, `POST /api/admin/users/<email>/<action>` | Admin Profile | List accounts; `approve`, `reject`, `verify`, `verify-link`, `reset-link`, `make-admin`, `remove-admin`, `disable`, `enable` |
| `POST /api/audit/event` | Signed in | Record `EXAM_STARTED` / `EXAM_SUBMITTED` |
| `GET /api/lab/status`, `POST /api/lab/refresh` | Signed in | Live Lab |
| `GET /api/admin/sessions`, `GET /api/admin/audit` | Admin Profile | Session metrics and audit log |
| `POST /api/admin/sessions/<id>/revoke` | Admin Profile | Revoke a session |

---

## 3. Prerequisites

| Software | Version | Needed for | Download |
|---|---|---|---|
| A modern browser | Chrome, Edge, Firefox or Safari (latest) | Everything | – |
| **Python** | 3.10 or newer (3.11 recommended) | Running the server | https://www.python.org/downloads/ (tick **"Add python.exe to PATH"** on Windows) |
| pip | Comes with Python | Installing packages | – |
| Authenticator app *(optional)* | Any TOTP app (Google/Microsoft Authenticator, Authy …) | Only for accounts created with `--mfa` | Your phone's app store |
| Git *(optional)* | Any | Version control | https://git-scm.com/downloads |
| Docker Desktop *(optional)* | 24 or newer | Running in a container | https://www.docker.com/products/docker-desktop/ |
| AWS CLI v2 *(optional)* | 2.x | Deployment, DynamoDB credentials, labs | https://aws.amazon.com/cli/ |

Python packages (installed from `requirements.txt`):
- `Flask`: the web server. It includes Werkzeug, which hashes passwords.
- `boto3`: the DynamoDB check.
- `gunicorn`: the production server. It is Linux/macOS only and is skipped automatically on Windows.

The database (SQLite) and the optional authenticator codes need nothing extra; they use the Python standard library.

Check what you already have:
```bash
python --version
pip --version
docker --version
aws --version
```

---

## 4. Local installation

**Windows (PowerShell)**
```powershell
cd "D:\Guru Documents\saa-c03-portal"
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
python auth.py create-user --email admin@vglearner.local
python auth.py create-user --email student@example.com
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
python auth.py create-user --email admin@vglearner.local
python auth.py create-user --email student@example.com
export IMDS_DISABLED=true
python app.py
```

`create-user` asks for a password (at least 12 characters):
- `admin@vglearner.local` automatically gets the **Admin Profile**.
- Any other email gets the **User Profile**.

See [section 5](#5-first-login-the-admin-root-account) (admin account) and [section 7](#7-login-profiles-and-user-management) (login, sign-up, profiles).

Open **http://localhost:5000** and log in. Stop the server with `Ctrl + C`.

The next time, you only need to activate the environment and run the app:
```powershell
cd "D:\Guru Documents\saa-c03-portal"
.\.venv\Scripts\Activate.ps1
$env:IMDS_DISABLED = "true"; python app.py
```

`IMDS_DISABLED=true` skips the EC2 metadata lookup, which only works on an EC2 instance. Leave it out when running on EC2.

### Run with Docker
```bash
cd saa-c03-portal
docker build -t saa-portal .
docker volume create saa-data
docker run --rm -it -v saa-data:/app/data saa-portal python auth.py create-user --email admin@vglearner.local
docker run -d --name saa-portal -p 5000:5000 -v saa-data:/app/data -e IMDS_DISABLED=true saa-portal
```
The `saa-data` volume keeps users and the audit log when the container is replaced.

### Check that it is running
```bash
curl http://localhost:5000/api/health
curl http://localhost:5000/api/auth/config
```
Expected output: `{"ok": true, ...}`, then `{"auth_enabled": true, "has_users": true, ...}`.

---

## 5. First login: the admin (root) account

**There is no built-in "root" account and no default password.** This is deliberate: a default login that everyone knows is one of the most common ways systems get broken into. The first administrator is created on the server, by someone who can run commands in the project folder. That access to the server is the real "root" of the portal.

### Create the first admin (one time)
```powershell
cd "D:\Guru Documents\saa-c03-portal"
.\.venv\Scripts\Activate.ps1
python auth.py create-user --email admin@vglearner.local --name "Portal Admin"
```
- It asks for the password twice (at least 12 characters). This is the admin password; nobody else knows it.
- `admin@vglearner.local` is always an **Admin Profile** because it is listed in `ADMIN_EMAILS`. You can use your own email instead: add `--role admin`.
- Accounts created this way are trusted, so they don't need email verification.

Then open `http://localhost:5000` and log in with that email and password.

### Already have an admin account?
List every account with its profile:
```powershell
python auth.py list-users
```
Example output:
```
admin.guru@vgs.com     Admin Profile  mfa=yes created 2026-10-01
```

| Situation | Command |
|---|---|
| Forgot the admin password (passwords can't be read back, only reset) | `python auth.py reset-password --email admin.guru@vgs.com` |
| The account asks for a 6-digit code and you want email + password only | `python auth.py disable-mfa --email admin.guru@vgs.com` |
| Lost the authenticator app | `python auth.py reset-mfa --email admin.guru@vgs.com` (new key) or `disable-mfa` |
| Every admin is locked out | `python auth.py create-user --email rescue@example.com --role admin` |

### Giving other people admin access
Only an admin can do this. Use any of these:
- In the portal: **Session Tracking & Audit → User accounts & requests → Make admin** (or **Remove admin**). Their open sessions switch within 5 seconds, without logging in again.
- On the server: `python auth.py set-role --email user@example.com --role admin` (or `--role learner`).
- Permanently: add the email to the `ADMIN_EMAILS` setting.

Admins cannot change their own role, and accounts in `ADMIN_EMAILS` can only be demoted on the server.

---

## 6. Where everything is stored: users, passwords, logs, data

| What | Where it is stored | How to see it |
|---|---|---|
| **User IDs** (the email address is the user ID), names, profile, status (verified / pending / disabled) | `data/portal.db`, table **`users`** | Portal: **Session Tracking & Audit → User accounts & requests** (admins). Server: `python auth.py list-users` or `python auth.py export-users --out users.csv` |
| **Passwords** | `data/portal.db`, `users.pw_hash`, as salted **scrypt hashes only** | Nobody can view or recover a password, not even an admin. Reset it with **Forgot password?**, an admin **Reset link**, or `python auth.py reset-password` |
| Authenticator (MFA) secrets | `users.totp_secret` | Shown once, when created with `--mfa` or `reset-mfa` |
| **Sessions** (who is logged in, from which IP, until when) | table **`sessions`** (stores only a hash of each session token) | Portal: **Active Concurrent Sessions Logged In** and the audit matrix (admins); **Revoke Session** ends one |
| **Audit log / security logs** (logins, failures, sign-ups, verifications, password changes, revocations, admin changes, exam submissions) | table **`audit`**, kept 90 days (`AUDIT_RETENTION_DAYS`) | Portal: **Enterprise Audit Log Matrix** (admins only; filter and search). Server: `python auth.py export-audit --out audit.csv [--days 30]` |
| Password-reset and email-verification links | table **`resets`** (only hashes of the links) | Requests appear in **User accounts & requests** as "reset requested" or "verification requested" |
| **Server / technical log** (startup, errors, emails sent, Live Lab checks) | Not stored in a file: printed to the terminal that runs `python app.py` | Watch that terminal; Docker: `docker logs saa-portal`; more detail: `$env:LOG_LEVEL = "DEBUG"`. To keep a file: `python app.py *> server.log` |
| **Status log** (the "Terminal · Status log" panel at the bottom of the portal) | Browser memory only | Visible while logged in; cleared at logout |
| **Study progress, scores, exam in progress, theme, custom logo** | The browser's LocalStorage on each device (keys starting with `saa.`) | Stays on that device; see *Reset everything* in [Troubleshooting](#12-troubleshooting) |
| Settings (sign-up mode, email server, admin emails…) | Environment variables when the server starts | See [Configuration](#8-configuration-environment-variables) |

**Database file location:** `D:\Guru Documents\saa-c03-portal\data\portal.db`. It is created automatically on first start; change it with `AUTH_DB`. In Docker it lives in the `saa-data` volume at `/app/data/portal.db`. It is in `.gitignore`, so it is never committed.

> Only **Admin Profile** users can see logs and accounts inside the portal. Anyone who can open the `data` folder on the server can read the database, so keep that folder private.

### Looking inside the database (read-only examples)
```powershell
python -c "import sqlite3; c=sqlite3.connect('data/portal.db'); [print(r) for r in c.execute('SELECT email, name, role, email_verified, pending, disabled FROM users')]"
python -c "import sqlite3; c=sqlite3.connect('data/portal.db'); [print(r) for r in c.execute(\"SELECT datetime(ts,'unixepoch','localtime'), email, action, ip, detail FROM audit ORDER BY id DESC LIMIT 20\")]"
```

### Backup and restore
```powershell
python -c "import sqlite3; sqlite3.connect('data/portal.db').backup(sqlite3.connect('portal-backup.db'))"
```
To restore, stop the server and copy `portal-backup.db` over `data/portal.db`.

### Importing users in bulk (CSV)
Create a CSV with the columns `email,name,role` (role is `learner` or `admin`; empty means learner):
```csv
email,name,role
kiran@example.com,Kiran Das,learner
lead@example.com,Team Lead,admin
```
Run:
```powershell
python auth.py import-users --csv users.csv
```
- Each new user gets a **set-password link** (valid 72 hours, `INVITE_TOKEN_HOURS`). The command prints the links; send each person theirs.
- With an email server configured, add `--send` to email them automatically.
- Opening the link lets the user choose their own password, which also verifies their email. Until then they cannot log in.
- Existing emails and invalid rows are skipped and reported.

### Exporting
```powershell
python auth.py export-users --out users.csv          # no password hashes are ever exported
python auth.py export-audit --out audit.csv --days 30
```

---

## 7. Login, profiles and user management

### Logging in
1. Open the portal. The **login page** appears; nothing else is visible.
2. Enter your **Email** and **Password**. The eye icon shows or hides the password.
3. Tick **Remember me** to stay logged in on this device for 30 days (`REMEMBER_ME_DAYS`). Without it, the session ends when the browser closes. Don't tick it on shared computers.
4. Click **Login**.
5. If the account was set up with an authenticator app (`--mfa`), a **6-digit code** field appears after the password is accepted.

Your profile appears at the top right (**Admin Profile** or **User Profile**). The round button with your initial shows your session details and has **Change password**, **Terms & privacy** and **Logout**.

### Signing up (new users)
1. On the login page, click **Sign up**.
2. Enter your **full name**, **email** and a **password**. The strength meter shows how strong it is.
   - The minimum length is 12 characters (`PASSWORD_MIN_LENGTH`).
   - Common passwords and passwords containing your name or email are rejected.
3. Confirm the password.
4. Tick **I agree to the Terms of Use and Privacy Notice**. The link opens the full text.
5. Click **Create account**.

Next, **verify your email address** (see below). You cannot log in before that.

What happens after verification depends on `SIGNUP_MODE`:
- `open` (default): you can log in.
- `approval`: an admin must also approve the account.
- `closed`: the **Sign up** link is hidden.

### Email verification (first login)
New sign-ups must prove they own their email address before their first login (`EMAIL_VERIFICATION=true`, the default).
- **With an email server** (`SMTP_*`): the user receives "Verify your email for VG Learner Portal" with a link valid for 24 hours (`VERIFY_TOKEN_HOURS`). Opening it shows "Your email address is verified. You can log in now."
- **Without an email server** (the default): the request appears in **User accounts & requests** as **EMAIL NOT VERIFIED · verification requested**. The admin either clicks **Verification link** and sends it to the user's email address, or clicks **Mark verified** after confirming the address another way.
- Trying to log in before verifying shows a message and a **Resend the verification email** (or **Ask an administrator to verify me**) button.
- Verification links work once. Accounts created by an admin with `create-user` are already verified. Completing a password-reset or invite link also verifies the address.
- Server alternative: `python auth.py verify-user --email user@example.com`.

### Sending emails automatically (verification, password reset, invitations)
Out of the box no email server is configured, so the sign-up message says *"An administrator will send you a verification link"*. To have the portal **email the verification link to the address used at sign-up automatically**, give it an email account to send from (SMTP). The same setting also sends password-reset links and `import-users --send` invitations.

**What you need:** an email account or service that allows SMTP sending. Any one of these works:

| Provider | Good for | What to prepare |
|---|---|---|
| **Gmail / Google Workspace** | Quick start, small groups | Turn on 2-Step Verification, then create an **App password** at https://myaccount.google.com/apppasswords. Your normal Gmail password will not work. |
| **Outlook.com / Microsoft 365** | Company mailboxes | Your mailbox and password. For Microsoft 365, an admin must enable **Authenticated SMTP** for that mailbox. |
| **Amazon SES** | Production on AWS | Verify your sender address or domain in SES, then create **SMTP credentials** (SES console → SMTP settings). New SES accounts can only send to verified addresses until you request production access. |
| **Brevo / SendGrid** | Free tiers, no own mail server | Create an account and an SMTP key. |

**Step 1: create the settings file.** In the project folder, copy the template:
```powershell
cd "D:\Guru Documents\saa-c03-portal"
Copy-Item .env.example .env
notepad .env
```

**Step 2: fill in one provider block.** Remove the `#` in front of its lines. For example, Gmail:
```ini
PUBLIC_URL=http://localhost:5000
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=yourname@gmail.com
SMTP_PASSWORD=abcdefghijklmnop
SMTP_FROM=VG Learner Portal <yourname@gmail.com>
```
- `PUBLIC_URL` is the address users open; it is put inside the emailed links. Use your real `https://` address in production.
- Port `587` uses STARTTLS. If your provider gives port `465`, SSL is switched on automatically.

**Step 3: test it before real users sign up.**
```powershell
python auth.py test-email --to you@example.com
```
`OK: the test email was accepted by the mail server` means it works. Otherwise the command explains the most likely cause (wrong App password, SMTP AUTH disabled, wrong port…).

**Step 4: restart the server.** Press `Ctrl + C` in its terminal, then run `python app.py`.

From now on, signing up shows *"We sent a verification link to …"* and the email arrives automatically. Unverified users can also click **Resend the verification email** on the login page.

Notes:
- `.env` contains your mail password. It is listed in `.gitignore`, so never commit it or share it.
- Real environment variables (`$env:SMTP_HOST=…`, `docker run -e …`) override values in `.env`.
- In Docker, pass the same values with `--env-file .env`.
- Check the server terminal for `Verification email sent to …` or `… failed: …` lines.

Rules that always apply:
- Self-registered accounts are always **User Profile**.
- Admin emails (`ADMIN_EMAILS`) can never be used to sign up.
- `SIGNUP_DOMAINS` can limit sign-up to company email domains.
- Each network can make at most 5 sign-up attempts per hour.

### Forgot / reset password
1. On the login page, click **Forgot password?** and enter your email.
2. The answer is always the same whether or not the account exists, so nobody can probe which emails are registered.
3. How the link arrives:
   - **With an email server configured** (`SMTP_*` settings): the user receives a link that works **once** and expires in **30 minutes** (`RESET_TOKEN_MINUTES`).
   - **Without email** (the default): the request appears in **Session Tracking & Audit → User accounts & requests** with a "reset requested" flag. An admin clicks **Send reset link**, copies the link and gives it to the user over a trusted channel.
4. Opening the link shows **Set a new password**, with a strength meter and confirmation.
5. After the reset, every session of that account is logged out, and the user logs in with the new password.

### Changing your password
In the account menu, choose **Change password** and enter your current password and the new one twice. Your other devices are logged out; this one stays logged in.

### Admin: managing accounts
The **User accounts & requests** card (Admin Profile only) lists every account. Pending sign-ups and reset requests appear first. For each account it shows:
- name and email
- profile (Admin or User)
- status: Active, Email not verified, Pending (approval) or Disabled
- flags such as "verification requested" or "reset requested"
- last login
- number of active sessions

The actions are:
- **Verification link** / **Mark verified** for unverified emails.
- **Approve** / **Reject** for pending sign-ups.
- **Reset link** for password resets.
- **Make admin** / **Remove admin** to give or take away access to the logs and user management.
- **Disable** / **Enable**.

The built-in admin account cannot be disabled or demoted here, and admins cannot change their own role. Every action is written to the audit log.

### Profiles (role-based access)
| | User Profile | Admin Profile |
|---|---|---|
| Who | Every other account | `admin@vglearner.local`, any email listed in `ADMIN_EMAILS`, or an account given `--role admin` |
| Dashboard, Exam, Study, Linux, Interview, Report, Live Lab | ✅ | ✅ |
| **Session Tracking & Audit** tab (sessions counter, metrics, audit log matrix, **Revoke Session**) | ❌ Not in the page at all | ✅ |
| `/api/admin/*` | ❌ `403`, logged as `ACCESS_DENIED` | ✅ |

**The server decides the profile, not the browser.** There is deliberately no "I'm an admin" checkbox on the login page, because anyone could tick it. To make someone an admin, do one of these:
- in the portal (admins only): **User accounts & requests → Make admin**;
- create the account with `--role admin`;
- run `set-role`;
- add the email to `ADMIN_EMAILS`.

For a User Profile, the admin tab, its panels, metrics and audit data are removed from the page, not merely hidden. They are built only after an Admin Profile logs in, and removed again at logout or revocation.

### How it is secured
The server (`auth.py`) makes every security decision. The browser only holds a random session token.

**Passwords**
- Stored as salted **scrypt** hashes, never in plain text.
- Minimum 12 characters.

**Sessions**
- 256-bit random tokens. The database stores only a SHA-256 hash.
- Sessions end after `SESSION_TTL_MINUTES` (default 12 hours), at logout, or when an admin revokes them.

**Brute-force lock**
- 5 failed attempts for one email in 15 minutes lock that email.
- 20 failed attempts from one IP address lock that IP.

**Optional MFA**
- Accounts created with `--mfa` also need a 6-digit authenticator code (TOTP). Each code works once.
- Recommended for admin accounts on shared deployments.

**Audit events**
- Login: `LOGIN_SUCCESS`, `LOGIN_FAILED`, `LOGIN_LOCKED`.
- Accounts: `SIGNUP_UNVERIFIED`, `EMAIL_VERIFIED`, `VERIFICATION_RESENT`, `VERIFICATION_ISSUED`, `SIGNUP_APPROVED`, `SIGNUP_REJECTED`, `USER_IMPORTED`, `USER_DISABLED`, `USER_ENABLED`.
- Admin access: `ADMIN_GRANTED`, `ADMIN_REVOKED`.
- Passwords: `PASSWORD_RESET_REQUESTED`, `PASSWORD_RESET_ISSUED`, `PASSWORD_RESET`, `PASSWORD_CHANGED`, `PASSWORD_CHANGE_FAILED`.
- Authenticator codes: `MFA_TRIGGERED`, `MFA_SUCCESS`, `MFA_FAILED`.
- Sessions and access: `LOGOUT`, `SESSION_EXPIRED`, `SESSION_REVOKED`, `ACCESS_DENIED`.
- Exams: `EXAM_STARTED`, `EXAM_SUBMITTED`.
- User changes made from the command line.
- Kept for `AUDIT_RETENTION_DAYS` (default 90).

### Revoke Session
1. In **Session Tracking & Audit**, an admin clicks **Revoke Session** on a row and confirms.
2. The server rejects that session immediately.
3. Within 5 seconds, the affected browser does the following, on any device:
   - deletes its stored token and in-progress exam
   - clears the status log
   - removes any admin panels
   - returns to the login page with the message "Your session was revoked by an administrator."
4. Revoking your own session logs you out at once.

Scores and study history stay on the device.

### Adding an authenticator app (only for `--mfa` accounts)
`create-user --mfa` and `reset-mfa` print a **Secret key**.
1. In the authenticator app, choose **Add account**, then **Enter a setup key**.
2. Paste the key.
3. Choose **time-based**.

The key is shown only once.

### User-management commands (run on the server, in the project folder)
| Task | Command |
|---|---|
| Create the admin | `python auth.py create-user --email admin@vglearner.local` |
| Create a student (User Profile) | `python auth.py create-user --email student@example.com` |
| Create an account that also needs an authenticator code | `python auth.py create-user --email admin2@example.com --role admin --mfa` |
| List users and their profiles | `python auth.py list-users` |
| Reset a password (also revokes that user's sessions) | `python auth.py reset-password --email user@example.com` |
| Make someone an admin / a normal user (revokes their sessions) | `python auth.py set-role --email user@example.com --role admin` (or `--role learner`) |
| Turn on / replace the authenticator code | `python auth.py reset-mfa --email user@example.com` |
| Turn off the authenticator code | `python auth.py disable-mfa --email user@example.com` |
| Block / unblock an account | `python auth.py disable-user --email ...` / `python auth.py enable-user --email ...` |
| Delete an account | `python auth.py delete-user --email user@example.com` |
| Mark an email address verified | `python auth.py verify-user --email user@example.com` |
| Approve a pending sign-up | `python auth.py approve-user --email user@example.com` |
| Import users from CSV (prints set-password links) | `python auth.py import-users --csv users.csv` (add `--send` to email them) |
| Export users / audit log to CSV | `python auth.py export-users --out users.csv` · `python auth.py export-audit --out audit.csv --days 30` |

In Docker, prefix the command with `docker exec -it saa-portal`, for example `docker exec -it saa-portal python auth.py list-users`.

---

## 8. Configuration (environment variables)

All settings are optional.

**Server**
| Variable | Default | Meaning |
|---|---|---|
| `HOST` | `127.0.0.1` | Address to listen on. Use `0.0.0.0` to allow other devices (e.g. your phone) |
| `PORT` | `5000` | Port to listen on |
| `PROXY_X_FOR_HOPS` | `2` | Trusted proxies in `X-Forwarded-For`, so the audit log shows the real client IP (`2` = ALB + nginx, `1` = ALB only, `0` = none) |
| `CORS_ALLOW_ORIGIN` | `*` | Origin allowed to call the API when the page is hosted elsewhere |
| `LOG_LEVEL` | `INFO` | `DEBUG` for more detail |

**Login and sessions**
| Variable | Default | Meaning |
|---|---|---|
| `AUTH_ENABLED` | `true` | `false` removes the login wall and gives everyone admin access. Use it only on a private PC. |
| `AUTH_DB` | `data/portal.db` | SQLite file for users, sessions and the audit log |
| `SESSION_TTL_MINUTES` | `720` | Session lifetime (12 h). It is also how long an installed phone app works offline. |
| `ONLINE_WINDOW_SECONDS` | `60` | How recent a heartbeat must be to count as "online now" |
| `MAX_FAILED_LOGINS` | `5` | Failures per email before lockout |
| `LOCKOUT_MINUTES` | `15` | Lockout and failure-counting window |
| `AUDIT_RETENTION_DAYS` | `90` | Older audit rows are deleted |
| `ADMIN_EMAILS` | `admin@vglearner.local` | Comma-separated emails that always get the **Admin Profile** (and can never be used to sign up) |
| `SIGNUP_MODE` | `open` | `open` (instant), `approval` (an admin approves) or `closed` (no self sign-up) |
| `SIGNUP_DOMAINS` | – | e.g. `example.com,example.org` to restrict self sign-up to those email domains |
| `PASSWORD_MIN_LENGTH` | `12` | Minimum password length for sign-up, reset and change |
| `REMEMBER_ME_DAYS` | `30` | Session length when **Remember me** is ticked |
| `RESET_TOKEN_MINUTES` | `30` | How long a password-reset link stays valid |
| `EMAIL_VERIFICATION` | `true` | New sign-ups must verify their email before the first login (`false` turns it off) |
| `VERIFY_TOKEN_HOURS` | `24` | How long an email-verification link stays valid |
| `INVITE_TOKEN_HOURS` | `72` | How long the set-password links from `import-users` stay valid |
| `PUBLIC_URL` | this server | The portal's public address, used to build reset links (e.g. `https://learn.example.com`) |
| `SMTP_HOST` / `SMTP_PORT` | – / `587` | Mail server for reset emails. Leave `SMTP_HOST` empty to use admin-delivered links |
| `SMTP_USER` / `SMTP_PASSWORD` | – | Mail server login (e.g. Amazon SES SMTP credentials) |
| `SMTP_FROM` | `SMTP_USER` | Sender address, e.g. `no-reply@example.com` |
| `SMTP_STARTTLS` | `true` (unless SSL) | Use STARTTLS on the SMTP connection (port 587) |
| `SMTP_SSL` | `true` on port 465 | Use implicit SSL/TLS (port 465) |

Every setting above can also be written in a **`.env` file** in the project folder (copy `.env.example`). The server reads it at start-up; real environment variables take priority.

**Live Lab**
| Variable | Default | Meaning |
|---|---|---|
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

Without the Live Lab variables, the Live Lab shows each AWS tier as **UNCONFIGURED**.

Example:
```powershell
$env:SESSION_TTL_MINUTES = "480"
$env:RDS_HOST = "mydb.abc123.us-east-1.rds.amazonaws.com"
$env:REDIS_HOST = "my-cache.abc123.use1.cache.amazonaws.com"
$env:DYNAMODB_TABLE = "Orders"; $env:AWS_REGION = "us-east-1"
python app.py
```

The DynamoDB check uses your normal AWS credentials: `aws configure`, environment variables, or an IAM role on EC2.

---

## 9. Using the portal

1. **Log in** on the login page (see [section 7](#7-login-profiles-and-user-management)). Your profile shows at the top right. The round button with your initial has your session details and **Logout**.
2. **First run:** the **System Test** banner checks storage, network and screen size. Click **Continue**.
3. **Exam:** open **Mock Exam**, pick a set and choose one of:
   - **Timed:** 130 minutes, locks when time is up.
   - **Review:** instant *Check Answer*.

   Answers, flags and the timer survive a page refresh. Starting and submitting an exam are recorded in the audit log.
4. **Report:** after submitting, **Performance Report** shows your scaled score, weak areas below 72%, explanations, diagrams and labs.
5. **Study:** the left sidebar lists the 14 modules. Use the search box to find services.
6. **Linux Lab:** type real commands into the drill terminal. `hint` shows a hint, `answer` shows the expected command, and `clear` clears the screen.
7. **Interview:** answer in STAR format (Situation, Task, Action, Result), then click **Submit answer** for your score and a model answer.
8. **Session Tracking & Audit** (Admin Profile only):
   - watch the **Active Concurrent Sessions Logged In** counter
   - filter the audit log by event or search text
   - tick **Active sessions only**
   - click **Revoke Session** on any active row
9. **Blueprint**, **Theme (☀/☾)** and **Change logo** work as before. The theme button is also on the login page.

---

## 10. Mobile and offline access

The portal is an installable web app (PWA). Offline mode and installing need **HTTPS**. The only exception is `localhost` on the same computer.

| How you open it | On phone? | Offline? |
|---|---|---|
| `http://localhost:5000` on your PC | – | ✅ (on the PC) |
| `http://<PC-IP>:5000` over Wi-Fi | ✅ | ❌ (not HTTPS) |
| An `https://` URL (see [Deployment](#11-deployment)) | ✅ | ✅ |

**Offline and login:**
- You must log in **online** at least once.
- After that, the installed app keeps working offline until the session expires (`SESSION_TTL_MINUTES`, default 12 h).
- While offline, the status log says the session is cached.
- When the server is reachable again, the session is checked again. A revoked session is signed out at that point.
- Exam events recorded offline are sent then.

**Quick phone test on the same Wi-Fi (online only):**
```powershell
$env:HOST = "0.0.0.0"; $env:IMDS_DISABLED = "true"; python app.py
ipconfig
```
Find the **IPv4 Address** (e.g. `192.168.1.20`) and open `http://192.168.1.20:5000` on the phone. Allow Python through Windows Firewall if asked.

**Install on the phone (from an HTTPS URL):**
1. Open the URL and log in. The status log shows *"Offline mode ready"*.
2. Add it to your home screen:
   - **Android (Chrome):** menu ⋮ → **Install app**.
   - **iPhone (Safari):** Share → **Add to Home Screen**.
3. Open it from the home-screen icon. It runs full screen.

---

## 11. Deployment

Because login is checked on the server, **the Flask backend must run somewhere reachable over HTTPS**. A static host alone (Netlify, GitHub Pages, S3) can show the login page but cannot sign anyone in.

### A. Recommended: one container (portal + login + Live Lab)
```bash
docker build -t saa-portal .
docker volume create saa-data
docker run --rm -it -v saa-data:/app/data saa-portal python auth.py create-user --email admin@vglearner.local
docker run -d --name saa-portal -p 5000:5000 -v saa-data:/app/data \
  -e RDS_HOST=... -e REDIS_HOST=... -e DYNAMODB_TABLE=... -e AWS_REGION=us-east-1 \
  saa-portal
```
Run it on **EC2** behind an **ALB with an ACM certificate** (HTTPS), with the volume on the instance's EBS disk.

Set `PROXY_X_FOR_HOPS`:
- `2` for ALB → nginx → app
- `1` for ALB → app

This gives the audit log correct client IPs.

Keep **one** container (one gunicorn worker) per database file. On **App Runner** or Fargate the disk is temporary, so users and the audit log are lost on redeploy. Use EC2 with EBS, or mount EFS at `/app/data`.

### B. Static page + separate backend
You can still host `index.html`, `manifest.webmanifest`, `sw.js` and `icons/` on Netlify, GitHub Pages, Cloudflare Pages or S3 + CloudFront, as long as the backend from option A runs elsewhere over HTTPS:
- On the login page, open **Server settings** and enter the backend URL (e.g. `https://api.example.com`).
- On the backend, set `CORS_ALLOW_ORIGIN=https://<your-portal-domain>`.

S3 + CloudFront upload:
```bash
BUCKET=saa-portal-$RANDOM
aws s3 mb s3://$BUCKET --region us-east-1
aws s3 cp index.html s3://$BUCKET/ --content-type text/html --cache-control no-cache
aws s3 cp sw.js s3://$BUCKET/ --content-type text/javascript --cache-control no-cache
aws s3 cp manifest.webmanifest s3://$BUCKET/ --content-type application/manifest+json
aws s3 cp icons s3://$BUCKET/icons --recursive
```
Then create a CloudFront distribution with:
- the bucket as origin, using **Origin Access Control**
- default root object `index.html`
- **Redirect HTTP to HTTPS**

### Production security checklist
- [ ] Serve only over **HTTPS**.
- [ ] Admin accounts use strong passwords and, ideally, an authenticator code (`--mfa` or `reset-mfa`).
- [ ] `AUTH_ENABLED` is not `false`.
- [ ] `CORS_ALLOW_ORIGIN` is set to your portal's origin, not `*`, when the page and API are on different domains.
- [ ] `data/portal.db` is on a persistent, backed-up disk and never committed to Git (it is in `.gitignore`).
- [ ] `PROXY_X_FOR_HOPS` matches your proxy chain.
- [ ] Choose `SIGNUP_MODE` deliberately: `approval` or `closed` (plus `SIGNUP_DOMAINS`) for private or company portals.
- [ ] Configure `SMTP_*` and `PUBLIC_URL` (e.g. Amazon SES) so users can verify their email and reset passwords without an admin.
- [ ] Keep `EMAIL_VERIFICATION=true`.
- [ ] Review who has the Admin Profile regularly (`python auth.py list-users`).
- [ ] Back up `data/portal.db` regularly.

---

## 12. Troubleshooting

Start with the **status log** at the bottom of the portal (click **Expand**) and the terminal running `app.py`. Most problems are explained there.

### Login problems

| Problem | Cause | Fix |
|---|---|---|
| Login page says **"No accounts exist yet"** | No users in the database | `python auth.py create-user --email admin@vglearner.local`, then reload the page. |
| Server pill shows **Offline** / "Cannot reach the login server" | `app.py` not running, wrong URL, or the page was opened as a file | Start `python app.py` and open `http://localhost:5000`. If the page is hosted elsewhere, set the URL under **Server settings**. |
| "Incorrect email or password." | Wrong credentials, or the account is disabled | Check with `python auth.py list-users`. Reset with `python auth.py reset-password --email ...`. |
| A code field appears after the password, or "That MFA code is invalid or was already used." | The account was created with `--mfa`; the phone clock is off, or a code was reused | Turn on automatic date/time on the phone and use the next code. To return to password-only login: `python auth.py disable-mfa --email ...` |
| "Too many failed attempts. Try again in 15 minutes." | Brute-force lockout | Wait `LOCKOUT_MINUTES`, or restart with a smaller value for testing. |
| Lost the authenticator / new phone | – | `python auth.py reset-mfa --email ...` (new key) or `disable-mfa` |
| Signed out with "Your session expired." | `SESSION_TTL_MINUTES` reached | Log in again, or raise `SESSION_TTL_MINUTES`. |
| Signed out with "Your session was revoked by an administrator." | An admin clicked **Revoke Session**, or a password reset/disable revoked it | Log in again. Ask the admin if it was unexpected. |
| No **Session Tracking & Audit** tab | You are a **User Profile**. This is by design. | On the server: `python auth.py set-role --email you@example.com --role admin`, or add the email to `ADMIN_EMAILS`, then log in again. |
| Audit log shows `127.0.0.1` or the proxy's IP for everyone | `PROXY_X_FOR_HOPS` doesn't match the proxy chain | Set it to the number of proxies in front of the app. |
| `sqlite3.OperationalError: unable to open database file` | The `data/` folder isn't writable (e.g. a Docker volume owned by root) | Check folder permissions, or set `AUTH_DB` to a writable path. |
| What is the root / admin password? | There is none built in | Create the admin: `python auth.py create-user --email admin@vglearner.local`, or reset an existing one: `python auth.py reset-password --email ...`. See [First login](#5-first-login-the-admin-root-account). |
| "Your email address isn't verified yet" / "Please verify your email address before logging in" | New sign-up that hasn't verified | Open the verification link. Without an email server, an admin uses **Verification link** or **Mark verified**, or runs `python auth.py verify-user --email ...`. |
| Verification email doesn't arrive | No `SMTP_HOST`, wrong SMTP settings, spam folder, or link older than 24 h | Run `python auth.py test-email --to your@email`. Click **Resend the verification email** on the login page. Check the server terminal for `Verification email ... failed`. An admin can send a new link. |
| Sign-up says "An administrator will send you a verification link" | No email server configured | Set up `.env` as described in [Sending emails automatically](#sending-emails-automatically-verification-password-reset-invitations), then restart the server. |
| `test-email` says the login was rejected | Gmail without an App password, Microsoft 365 SMTP AUTH disabled, or SES IAM keys used instead of SMTP credentials | Follow the hint the command prints, then test again. |
| `test-email` works but users get nothing | Mail in spam, `SMTP_FROM` not allowed by the provider, or SES still in sandbox | Use a verified sender address; for SES, request production access; ask users to check spam. |
| Changed `.env` but nothing changed | The server reads `.env` only at start-up | Restart `python app.py`. |
| "This verification link is invalid, already used or expired" | Links work once and last `VERIFY_TOKEN_HOURS` | If you already verified, just log in. Otherwise try to log in and click **Resend**. |
| The server address / "Server settings" are not on the login page | Shown only when the server can't be reached | Normal. If the server is down, they appear automatically so you can fix the URL. |
| A user needs to see the logs | Logs are Admin Profile only | An admin clicks **Make admin** for that user (they can **Remove admin** later). |
| No **Sign up** link on the login page | `SIGNUP_MODE=closed` | Set `SIGNUP_MODE=open` or `approval` and restart, or create accounts with `python auth.py create-user`. |
| Sign-up says "This email address is reserved." | The email is in `ADMIN_EMAILS` | Admin accounts are created on the server: `python auth.py create-user --email admin@vglearner.local`. |
| Sign-up says "An account with this email already exists." | Already registered | Use **Log in**, or **Forgot password?**. |
| "Your account is waiting for administrator approval." | `SIGNUP_MODE=approval` | An admin approves it in **User accounts & requests**, or on the server: `python auth.py approve-user --email ...`. |
| Password rejected as too short, common, or containing your name/email | Password policy | Use a longer passphrase (3–4 random words with a number), at least `PASSWORD_MIN_LENGTH` characters. |
| Forgot password: no email arrives | No `SMTP_HOST` configured (admin delivery mode), wrong SMTP settings, or the email went to spam | Without SMTP, an admin sends the link from **User accounts & requests**. With SMTP, check the server log for `Password reset email ... failed` and verify `SMTP_*` (for SES: verified sender, SMTP credentials, port 587). |
| Reset link says "invalid, already used or expired" | Links work once and expire after `RESET_TOKEN_MINUTES` | Request a new one with **Forgot password?**. |
| Reset link opens the wrong server | `PUBLIC_URL` not set behind a proxy or CDN | Set `PUBLIC_URL` to the portal's public address. |
| Logged out every time the browser closes | **Remember me** wasn't ticked | Tick **Remember me** when logging in. |
| Locked out completely (no admin can log in) | – | On the server: `python auth.py reset-password --email admin@vglearner.local`, or `python auth.py create-user --email rescue@example.com --role admin`. |

### Installation problems

| Problem | Cause | Fix |
|---|---|---|
| `'python' is not recognized` | Python not installed or not on PATH | Reinstall Python and tick **Add python.exe to PATH**, or use `py` instead of `python`. |
| `Activate.ps1 cannot be loaded because running scripts is disabled` | PowerShell execution policy | Run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once (answer **Y**), or use Command Prompt: `.venv\Scripts\activate.bat`. |
| `ModuleNotFoundError: No module named 'flask'` | Virtual environment not active, or packages missing | Activate `.venv` (the prompt shows `(.venv)`), then `pip install -r requirements.txt`. |
| `pip install` fails behind a company proxy | Proxy blocks PyPI | `pip install --proxy http://<proxy>:<port> -r requirements.txt` |
| `create-user` says "Password must be at least 12 characters." | Password policy | Use a longer passphrase. |
| gunicorn error on Windows | gunicorn does not run on Windows | Expected. It is skipped on Windows; use `python app.py`. |

### Server problems

| Problem | Cause | Fix |
|---|---|---|
| `Address already in use` / port 5000 busy | Another program is using port 5000 (on macOS, often AirPlay Receiver) | Use another port: `$env:PORT = "8080"; python app.py`. Or find the process: `netstat -ano \| findstr :5000`, then `taskkill /PID <pid> /F`. On macOS/Linux: `lsof -i :5000`. |
| Browser shows "This site can't be reached" | Server not running, or wrong port | Check the terminal for `Running on http://127.0.0.1:5000`, then run `curl http://localhost:5000/api/health`. |
| Startup is slow or logs show IMDS timeouts | Not on EC2, so metadata calls time out | Set `IMDS_DISABLED=true`. |
| Live Lab tiers show **UNCONFIGURED** | No endpoint variables set | Set `RDS_HOST`, `REDIS_HOST` and/or `DYNAMODB_TABLE`. |
| RDS/Redis shows **timeout** | Security group, NACL or route blocks the port | Allow inbound 5432/3306/6379 from the app's security group. Test with `nc -zv <host> <port>` (Linux) or `Test-NetConnection <host> -Port <port>` (Windows). |
| RDS/Redis shows **DNS error** | Wrong endpoint, or a private endpoint resolved from outside the VPC | Check with `nslookup <host>`. Private endpoints only resolve inside the VPC. |
| Redis connects but the check fails | TLS required | Set `REDIS_TLS=true`. |
| DynamoDB `NoCredentialsError` / `AccessDenied` | No AWS credentials, or missing permissions | Run `aws configure` or attach an IAM role with `dynamodb:DescribeTable` / `dynamodb:ListTables`. Check with `aws sts get-caller-identity`. |
| CORS errors in the browser console | Page and API on different domains | Set `CORS_ALLOW_ORIGIN` to the page's origin and use HTTPS on both. |

### Phone and offline problems

| Problem | Cause | Fix |
|---|---|---|
| Phone can't open `http://<PC-IP>:5000` | Server listens on localhost only, firewall blocks it, or different network | Start with `HOST=0.0.0.0`, allow Python in **Windows Defender Firewall**, and put both devices on the same Wi-Fi. |
| No **Install app** option | Page not served over HTTPS | Deploy with HTTPS (see [Deployment](#11-deployment)). |
| iPhone: no install prompt | iOS installs only through Safari | Open in **Safari**, then Share → **Add to Home Screen**. |
| Offline launch shows the login page | Never logged in online on this device, or the session expired | Log in once while online. Offline use lasts until the session expires. |

### Browser and app problems

| Problem | Cause | Fix |
|---|---|---|
| Old version still shows after an update | Service worker cache | Hard refresh (`Ctrl + Shift + R`). Or open DevTools (`F12`) → **Application** → **Service workers** → **Unregister**, then reload. When deploying, bump `CACHE` in `sw.js`. |
| Progress lost | Private window, cleared site data, a different browser, or a revoked session (clears the in-progress exam) | Use a normal window in the same browser. Scores and history stay per browser and device. |
| Exam stuck or corrupted state | Old saved session | In DevTools Console: `localStorage.removeItem('saa.session.v3')`, then reload. |
| Reset everything on this device (also logs out locally) | – | DevTools Console: `Object.keys(localStorage).filter(k => k.startsWith('saa.')).forEach(k => localStorage.removeItem(k)); location.reload();` |
| System Test warns about window scaling | Window smaller than 1100×600, or browser zoom not 100% | It still works (compact layout). Press `Ctrl + 0` to reset zoom. |
| Logo shows a box or the wrong image | A custom logo is saved in this browser | Sidebar → **Reset** next to the logo. |

### Docker problems

| Problem | Fix |
|---|---|
| `docker: command not found` or daemon not running | Install or start **Docker Desktop**, then run `docker info`. |
| Container exits immediately | Check the output with `docker logs saa-portal`. |
| Users disappear after recreating the container | Mount the volume: `-v saa-data:/app/data`. |
| Port conflict | Map another host port: `docker run -p 8080:5000 ...`, then open `http://localhost:8080`. |

---

## 13. Updating the portal

1. Edit `index.html`. Everything, including the question bank, modules and labs, is inside this one file. Login logic is in `auth.py`.
2. Open `sw.js` and increase `CACHE`, for example `saa-portal-v7` → `saa-portal-v8`. Installed phones then pick up the new version.
3. Restart `python app.py`, or rebuild and redeploy the container.
4. Users stay logged in across updates, unless the server's database was replaced.

---

## 14. Command cheat sheet

```powershell
# --- Windows: first-time setup ---
cd "D:\Guru Documents\saa-c03-portal"
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python auth.py create-user --email admin@vglearner.local

# --- Windows: run ---
$env:IMDS_DISABLED = "true"; python app.py

# --- Users ---
python auth.py list-users
python auth.py create-user --email student@example.com
python auth.py reset-password --email student@example.com
python auth.py set-role --email student@example.com --role admin
python auth.py disable-user --email student@example.com
python auth.py approve-user --email student@example.com
python auth.py verify-user --email student@example.com
python auth.py import-users --csv users.csv
python auth.py export-users --out users.csv
python auth.py export-audit --out audit.csv --days 30
python auth.py test-email --to you@example.com      # check the email (SMTP) settings

# --- Sign-up mode and password-reset email (examples) ---
$env:SIGNUP_MODE = "approval"; $env:SIGNUP_DOMAINS = "example.com"
$env:SMTP_HOST = "email-smtp.us-east-1.amazonaws.com"; $env:SMTP_USER = "<ses-smtp-user>"; $env:SMTP_PASSWORD = "<ses-smtp-password>"
$env:SMTP_FROM = "no-reply@example.com"; $env:PUBLIC_URL = "https://learn.example.com"

# --- Phone on the same Wi-Fi ---
$env:HOST = "0.0.0.0"; $env:IMDS_DISABLED = "true"; python app.py
ipconfig

# --- Health checks ---
curl http://localhost:5000/api/health
curl http://localhost:5000/api/auth/config

# --- Port 5000 busy ---
netstat -ano | findstr :5000
taskkill /PID <pid> /F

# --- Docker ---
docker build -t saa-portal .
docker volume create saa-data
docker run -d --name saa-portal -p 5000:5000 -v saa-data:/app/data -e IMDS_DISABLED=true saa-portal
docker exec -it saa-portal python auth.py list-users
docker logs saa-portal
```

---

**Notes**
- Practice questions are original study material written in the SAA-C03 style, not real exam questions.
- The name **VG Learner Portal** appears in the browser tab, the top bar, the login page and the installed phone app. The exam content itself refers to the AWS SAA-C03 exam.
- Architecture diagrams use the official AWS Architecture Icons (via the MIT-licensed `aws-icons` package).
- Interview scoring runs locally in the browser using keyword and structure rules. It is a coaching aid, not an AI grader.
- Linux drill terminal output is simulated.
- Lab CLI commands use placeholder IDs such as `111122223333` and `vpc-…`. Replace them with your own, and delete lab resources afterwards to avoid charges.
