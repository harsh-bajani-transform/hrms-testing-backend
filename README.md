# HRMS Testing Backend

Flask backend for HRMS operations, tracker handling, QC workflows, and reporting.  
This service is the Python-side API used by frontend clients and also integrates with the Node `qc-backend` for end-to-end QC processing.

## What This Service Handles

- User authentication and user management.
- Project, task, category, permission, and dropdown master data APIs.
- Tracker file upload + updates + views (including daily/monthly productivity rollups).
- QC-related APIs (daily assigned hours, temp QC, audit, AFD, rework, history).
- Password reset flow with signed tokens and email delivery.
- API activity logging and dashboard/filter endpoints.
- Cloudinary storage integration for uploaded files.

## Tech Stack

| Layer | Technology |
| --- | --- |
| Language | Python 3.x |
| Framework | Flask |
| Database | MySQL (`mysql-connector-python`) |
| Scheduler | APScheduler |
| Storage | Cloudinary |
| Email/Reset | SMTP + `itsdangerous` token signing |
| Utilities | `python-dotenv`, `requests`, `cryptography` |

## Architecture Overview

```text
Client
  -> Flask Blueprints (/auth, /user, /project, /tracker, /qc, ...)
     -> Route handlers with SQL (mysql-connector)
        -> Shared utils (response, validators, file/cloudinary, security, email)
           -> MySQL + Cloudinary + SMTP
```

## Entry Points

- `app.py` -> creates Flask app, registers blueprints, enables CORS, starts scheduler.
- `config.py` -> env loading, DB connection factory, Cloudinary/env validation.
- `scheduler.py` -> runs daily job that triggers `/qc/assign-daily-hours`.

## Directory Map

- `routes/` -> all API modules grouped by domain.
- `utils/` -> reusable helpers (response format, security, file/cloudinary/email helpers, validation).
- `uploads/` -> local upload area (if local file strategy is used).
- `task_files/` -> task-level files.
- `app.py`, `config.py`, `scheduler.py` -> runtime core.
- `test_api.py` -> lightweight API test script.

## Key Business Flows

### 1) Authentication + Registration

- Endpoint: `POST /auth/user`.
- JSON body is treated as **login**.
- Multipart form-data is treated as **registration** (supports profile picture upload).
- Passwords are stored using app-level encryption utility (`utils/security.py`).

### 2) Tracker Lifecycle

- Create tracker with file upload: `POST /tracker/add`.
- Update tracker/file replacement: `POST|PUT /tracker/update`.
- Soft delete tracker + cleanup linked tracker records: `POST /tracker/delete`.
- Role-aware views:
  - `POST /tracker/view`
  - `POST /tracker/view_daily`
- File uploads are pushed to Cloudinary and persisted as URLs.

### 3) QC Daily Assignment + Temp QC

- Scheduler calls `POST /qc/assign-daily-hours` daily (default 8:00 AM).
- This upserts 9 assigned hours for active agents into `temp_qc`.
- Manual/partial QC updates are handled through `POST /qc/temp-qc`.

### 4) Password Reset Flow

1. `POST /password_reset/forgot-password` creates signed reset token and sends email.
2. `POST /password_reset/verify-reset-token` validates token expiry/signature.
3. `POST /password_reset/reset-password` writes encrypted new password.

## API Modules (Blueprint Prefixes)

Registered in `app.py`:

- `/auth`
- `/user`
- `/project`
- `/project_category`
- `/dropdown`
- `/task`
- `/tracker`
- `/permission`
- `/dashboard`
- `/project_monthly_tracker`
- `/user_monthly_tracker`
- `/api_log_list`
- `/password_reset`
- `/qc`
- `/qc_afd`
- `/qc_audit`
- `/qc_rework`
- `/qc_history_user`

## Environment Variables

This project currently has `.env` but no `.env.example`.  
Create one later for easier onboarding.

Main variables used in code:

- **Database**: `DB_HOST`, `DB_PORT`, `DB_USERNAME`, `DB_PASSWORD`, `DB_DATABASE`
- **Security**: `RESET_SECRET_KEY`, `RESET_TOKEN_TTL_SECONDS`, `ENCRYPTION_KEY`
- **Password reset/frontend**: `RESET_FRONTEND_URL`
- **Cloudinary**: `CLOUDINARY_CLOUD_NAME`, `PYTHON_CLOUDINARY_API_KEY`, `CLOUDINARY_API_SECRET_KEY`
- **Uploads URL base**: `BASE_UPLOAD_URL`
- **Scheduler/API self-call**: `API_BASE_URL`
- **SMTP**: `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASS` (used by email utility)

## Local Setup

### 1) Install dependencies

```bash
pip install -r requirements.txt
```

### 2) Configure environment

- Ensure `.env` exists and contains all required keys.
- `config.py` validates critical env vars on startup.

### 3) Run app

```bash
python app.py
```

Default runtime in `app.py`: host `0.0.0.0`, port `5000`, debug `True`.

### 4) Optional production run

```bash
gunicorn app:app
```

## Coding Conventions Observed

- Modular Flask blueprint pattern: one domain per route file.
- SQL-first approach using `cursor.execute(...)` with explicit transactions where needed.
- Unified response helper pattern through `utils.response.api_response`.
- Soft-delete style commonly used (`is_active`, `is_delete`) instead of hard deletes.
- JSON-typed columns are frequently stored as serialized arrays (`json.dumps` / `json.loads`).

## Integration Notes (with `qc-backend`)

- `qc-backend` consumes tracker data via `/tracker/view`.
- Tracker file URL normalization is important for cross-service compatibility.
- Keep field names (`tracker_id`, `tracker_file`, `user_id`, etc.) stable when changing APIs.

## Common Developer Tasks

- **Add endpoint**
  - Add function in the relevant file under `routes/`.
  - Ensure blueprint is already registered in `app.py` (or register if new module).

- **Add DB-backed feature**
  - Use `get_db_connection()` from `config.py`.
  - Use parameterized SQL and explicit `commit()/rollback()` blocks.

- **Add upload flow**
  - Reuse Cloudinary helpers from `utils/cloudinary_utils.py`.
  - Keep delete/replace logic symmetrical to avoid orphan files.

- **Change auth/security logic**
  - Update `utils/security.py` and corresponding route logic in `routes/auth.py` / `routes/password_reset.py`.

## Current Gaps / Recommendations

- Add `.env.example` with safe placeholders.
- Add a formal test suite (pytest) and CI checks.
- Move route-level SQL into service/query layers for maintainability.
- Consider introducing auth middleware/JWT if not handled externally.

