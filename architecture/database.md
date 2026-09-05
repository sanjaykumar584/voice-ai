# Database Design — Batch Calling on Supabase

> The Postgres schema behind the batch-calling flow (campaigns → jobs → calls →
> outcomes → recordings). Designed for growth (multi-tenant-ready) but simple
> enough for <100 calls/day. Local Supabase only for now — no auth/RLS yet.

---

## 1. What each table is for

| Table | Purpose |
|---|---|
| `campaigns` | One per imported spreadsheet (a batch run) |
| `call_jobs` | One row per **customer** from the sheet — the intent + retry state |
| `calls` | One row per **dial attempt** — the actual call + outcome + recording |
| `blocklist` | NDNC / opt-out numbers — checked before every dial |
| `escalations` | HARDSHIP / DECEASED / DNC / etc. that need a human |
| `audit_log` | Who did what, when (compliance trail) |

**Key idea:** `call_jobs` (customer) and `calls` (attempt) are separate. A retry
is a **new `calls` row**; the job tracks how many attempts are left.

```
campaigns 1 ──► N call_jobs 1 ──► N calls   (attempts)
                      │                │
                      │                ├─► escalations (flags needing humans)
                      │                └─► audit_log
 blocklist ── checked before each dial
```

---

## 2. Apply it

Option A — **Supabase SQL editor** (local dashboard at `http://localhost:54323`):
paste the whole script from §3 and run.

Option B — **as a migration** (recommended, reproducible):

```bash
# Already saved at supabase/migrations/0001_batch_calling.sql
supabase db reset     # replays all migrations on the local DB
```

---

## 3. The schema (copy-paste)

```sql
-- ================= CAMPAIGNS (one per imported CSV) =================
create table campaigns (
  id uuid primary key default gen_random_uuid(),
  tenant_id text not null default 'default',   -- multi-tenant from day 1
  name text not null,
  source_file text,                            -- original CSV filename
  status text not null default 'draft'
    check (status in ('draft','running','done','cancelled')),
  total_jobs int not null default 0,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

-- ================= CALL_JOBS (one per customer row) =================
create table call_jobs (
  id uuid primary key default gen_random_uuid(),
  campaign_id uuid references campaigns(id) on delete cascade,
  tenant_id text not null default 'default',
  body jsonb not null,                         -- the mapped collections body (replayable)
  loan_no text,
  customer_name text,
  phone text not null,                         -- E.164, e.g. +917299159380
  agent_name text,
  status text not null default 'pending'
    check (status in ('pending','scheduled','running','completed','blocked','failed')),
  blocklist_hit boolean not null default false,
  attempts int not null default 0,
  max_attempts int not null default 3,
  next_attempt_at timestamptz,                 -- when the worker may retry
  last_outcome text,                           -- outcome of the last attempt
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

-- ================= CALLS (one per dial attempt) =================
create table calls (
  id uuid primary key default gen_random_uuid(),
  job_id uuid references call_jobs(id) on delete cascade,
  campaign_id uuid references campaigns(id) on delete cascade,
  tenant_id text not null default 'default',
  phone text not null,
  vobiz_call_uuid text,                        -- Vobiz's call_uuid from /start
  status text not null default 'initiated'
    check (status in ('initiated','ringing','active','ended','failed','timeout')),
  connected boolean not null default false,
  outcome text
    check (outcome in ('PTP','NO_PTP','NO_ARREARS','DISPUTE','HARDSHIP','DECEASED',
                       'SURRENDER','HOSTILE','WRONG_NUMBER','NO_OUTCOME','NO_ANSWER')),
  outcome_note text,
  error text,                                  -- start/poll failure reason
  recording_key text,                          -- Storage path, e.g. recordings/<id>.mp3
  recording_served_url text,                   -- signed URL (short-lived, regenerated)
  duration_secs int,
  cost_estimate numeric(10,4),
  started_at timestamptz,
  ended_at timestamptz,
  created_at timestamptz not null default now()
);

-- ================= BLOCKLIST (NDNC / opt-out) =================
create table blocklist (
  id uuid primary key default gen_random_uuid(),
  tenant_id text not null default 'default',
  phone text not null,
  reason text not null default 'opt-out'
    check (reason in ('opt-out','ndnc','internal','other')),
  created_at timestamptz not null default now(),
  unique (tenant_id, phone)
);

-- ================= ESCALATIONS (compliance flags → humans) =================
create table escalations (
  id uuid primary key default gen_random_uuid(),
  tenant_id text not null default 'default',
  call_id uuid references calls(id) on delete set null,
  job_id uuid references call_jobs(id) on delete cascade,
  flag text not null
    check (flag in ('HARDSHIP','DECEASED','SURRENDER','HOSTILE','DNC',
                    'URGENT-WELFARE','DISPUTE')),
  note text,
  status text not null default 'pending'
    check (status in ('pending','resolved','dismissed')),
  created_at timestamptz not null default now(),
  resolved_at timestamptz
);

-- ================= AUDIT LOG (compliance trail) =================
create table audit_log (
  id bigint generated always as identity primary key,
  tenant_id text not null default 'default',
  actor text,                                  -- 'batch_caller' / 'system' / 'admin'
  action text not null,                        -- 'job_created','call_started','outcome_recorded'...
  entity_type text,                            -- 'call' | 'job' | 'campaign'
  entity_id text,
  details jsonb,
  created_at timestamptz not null default now()
);

-- ================= INDEXES =================
create index idx_jobs_campaign        on call_jobs(campaign_id);
create index idx_jobs_tenant_status   on call_jobs(tenant_id, status);
create index idx_jobs_next_attempt    on call_jobs(next_attempt_at)
  where status in ('pending','scheduled');
create index idx_calls_job            on calls(job_id);
create index idx_calls_tenant_created on calls(tenant_id, created_at desc);
create index idx_calls_status         on calls(status);
create index idx_escalations_pending  on escalations(status) where status = 'pending';
create index idx_blocklist_phone      on blocklist(tenant_id, phone);
```

---

## 4. Storage bucket (recordings)

```bash
# local Supabase: create the private bucket via the dashboard
# (Storage → New bucket → name: recordings, Public: OFF)
```

- MP3s upload to `recordings/<call_id>.mp3`.
- `calls.recording_served_url` holds a **signed URL** (short expiry, e.g. 1h) —
  generated on demand with the service_role key, so the sheet's link never
  exposes the raw bucket.

---

## 5. How the flow uses the tables

| Step | Writes |
|---|---|
| **Import** (`POST /batch/import`) | 1 `campaigns` row + 1 `call_jobs` row per CSV row (`body` = mapped collections body); blocklist check marks `blocked` |
| **Worker** (`POST /batch/{id}/run`) | picks `call_jobs` where `status in (pending,scheduled) and next_attempt_at <= now` → creates a `calls` row → dials → polls |
| **Call end** (`server.py` + `bot.py`) | `calls.status='ended'`, `connected`, `outcome`, `outcome_note` (from `log_outcome`), `recording_key` + signed URL; escalation flags → `escalations` |
| **Retry** | `NO_ANSWER`/`FAILED` → `call_jobs.attempts+1`; if `< max_attempts` → `status='scheduled'`, `next_attempt_at = next day`; else `status='completed'`, `last_outcome` set |
| **Export** (`GET /batch/{id}/export`) | join `call_jobs` + `calls` → the same CSV columns as today |

---

## 6. Integration notes

- **Connection**: psycopg direct, pool from `DATABASE_URL`
  (`postgresql://postgres:postgres@127.0.0.1:54322/postgres` for local Supabase).
- **RLS is OFF** (no auth yet). `tenant_id` exists everywhere so enabling RLS
  later is a policy, not a migration.
- **Outcome values** are a `check` constraint — adding one later is
  `ALTER TABLE calls DROP CONSTRAINT calls_outcome_check;` + re-add (or switch
  to a Postgres enum).
- **Timestamps** are UTC (`timestamptz`); display in IST in the app.
- **Phone** is `text` (E.164) — never a number column (leading `+`, future
  international formats).
- The in-memory registry (`app/calls/registry.py`) is a cache for live WebSocket lookups —
  the DB is the source of truth.
