# Proxy Crack

A cross-platform client that routes traffic through a **user-defined chain of proxies**, the way
Linux `proxychains` does — but system-wide, health-aware, and with automatic failover.

Android is the foundation; Windows, Linux and macOS clients are built from the same codebase.

> **Honest scope:** this is layered egress routing through commercial proxies you supply. It is
> **not** Tor and makes no anonymity guarantee — one account, one payment trail, proxies you
> purchased. See [`Project/roadmap.md`](Project/roadmap.md).

---

## 👉 Start here

**[`Project/STATUS.md`](Project/STATUS.md)** — what works today, what doesn't, what's blocked.

---

## Layout

| Path | What it is |
|---|---|
| [`app/`](app/) | Flutter mobile app — UI, adaptive layouts, engine behind an interface |
| [`backend/`](backend/) | Django 5 + DRF — auth, entitlement, proxy inventory, health, admin API |
| [`admin/`](admin/) | React + TypeScript operations console |
| [`Project/`](Project/) | Architecture, decisions, threat model, roadmap — **the source of truth** |
| [`Pre-Flight/`](Pre-Flight/) | Environment setup checklists and the install log |
| [`design-system/`](design-system/) | Design tokens shared by the app and the console |

### Documentation

| File | Answers |
|---|---|
| [STATUS.md](Project/STATUS.md) | Where the project stands right now |
| [architecture.md](Project/architecture.md) | How the system is designed, and the hard part |
| [memory.md](Project/memory.md) | Every decision and why, open questions, gotchas |
| [graph.md](Project/graph.md) | Component, data-flow and entity diagrams |
| [roadmap.md](Project/roadmap.md) | What beats proxychains, and what deliberately doesn't |
| [engine-decision.md](Project/engine-decision.md) | Xray-core vs sing-box — licence and capability |
| [admin-api-threat-model.md](Project/admin-api-threat-model.md) | STRIDE analysis of the admin surface |

---

## Running it

### Backend

```bash
cd backend
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt

cp .env.example .env          # then edit it — see the warning below
.venv/Scripts/python.exe manage.py migrate
.venv/Scripts/python.exe manage.py seed_plans
.venv/Scripts/python.exe manage.py createsuperuser
.venv/Scripts/python.exe manage.py runserver 8000
```

> ⚠️ **A fresh clone will not even migrate without `PROXY_CRED_KEY`.**
> Proxy credentials are encrypted at rest, and the app **refuses to start** without a valid key
> rather than silently writing plaintext. Generate one:
> ```bash
> python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
> ```
> Put it in `.env` as `PROXY_CRED_KEY`. It accepts a comma-separated list for rotation —
> encrypt with the first, decrypt with any. **Lose it and the credentials are unrecoverable.**

### Admin console

```bash
cd admin
npm install
npm run dev        # http://localhost:5173 — uses an in-memory mock by default
```

Point it at the real backend with `echo VITE_USE_MOCK_API=false > .env.development.local`.

### Mobile app

```bash
cd app
flutter pub get
flutter run        # Android only for now; needs an emulator or a device
```

Environment setup from scratch: [`Pre-Flight/README.md`](Pre-Flight/README.md).

---

## Operational commands

| Command | Purpose |
|---|---|
| `probe_proxies` | Health-check proxies, record results, rescore |
| `prune_probes` | Trim probe history (30-day default) |
| `prune_audit_log` | Trim audit history (365-day default, 90-day floor, dry-run by default) |
| `rotate_proxy_credentials` | Re-encrypt credentials under a new key |
| `seed_plans` | Create the free and paid plans |

---

## Tests

```bash
cd backend && .venv/Scripts/python.exe manage.py test     # 628 tests
cd admin   && npx tsc --noEmit && npm run build
cd app     && flutter analyze && flutter test
```

---

## Security notes

- Proxy credentials are **Fernet-encrypted at rest** and never returned in list responses.
  Disclosure is an explicit `POST /api/admin/proxies/{id}/reveal/`, throttled and audited.
- The admin API is **unscoped by design**, so every endpoint requires staff. A startup check
  refuses to boot if any route under `/api/admin/` is missing that permission.
- The audit log is **append-only, enforced by a database trigger** — not by convention.
- Entitlement is enforced **server-side**; the client only renders what it is given.

Before deploying, work through the controls checklist in
[`Project/admin-api-threat-model.md`](Project/admin-api-threat-model.md).

---

## Status

Backend and admin console are working and verified. The mobile app is UI with a mock engine.
**The VPN engine itself is not built yet** — see [`Project/STATUS.md`](Project/STATUS.md).
