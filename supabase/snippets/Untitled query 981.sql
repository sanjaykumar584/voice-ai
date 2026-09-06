-- 0001_batch_calling.sql
-- Batch-calling schema for the EMI collections voice agent.
-- See architecture/database.md for the design notes.
-- Note: the `recordings` Storage bucket is created via the dashboard/API, not SQL.

-- ================= CAMPAIGNS (one per imported CSV) =================
create table campaigns (
  id uuid primary key default gen_random_uuid(),
  tenant_id text not null default 'default',
  name text not null,
  source_file text,
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
  body jsonb not null,
  loan_no text,
  customer_name text,
  phone text not null,
  agent_name text,
  status text not null default 'pending'
    check (status in ('pending','scheduled','running','completed','blocked','failed')),
  blocklist_hit boolean not null default false,
  attempts int not null default 0,
  max_attempts int not null default 3,
  next_attempt_at timestamptz,
  last_outcome text,
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
  vobiz_call_uuid text,
  status text not null default 'initiated'
    check (status in ('initiated','ringing','active','ended','failed','timeout')),
  connected boolean not null default false,
  outcome text
    check (outcome in ('PTP','NO_PTP','NO_ARREARS','DISPUTE','HARDSHIP','DECEASED',
                       'SURRENDER','HOSTILE','WRONG_NUMBER','NO_OUTCOME','NO_ANSWER')),
  outcome_note text,
  error text,
  recording_key text,
  recording_served_url text,
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
  actor text,
  action text not null,
  entity_type text,
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
