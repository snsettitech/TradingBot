# Setup Guide - Supabase & Email Alerts

## 1. Supabase Setup

### Create a Supabase Project

1. Go to [supabase.com](https://supabase.com) and sign in (or create an account).
2. Click **New Project**.
3. Enter:
   - **Name**: `tsxbot`
   - **Database Password**: (generate a strong one, save it)
   - **Region**: Choose closest to you (e.g., `East US`)
4. Click **Create new project** and wait ~2 minutes.

### Get Your Credentials

1. In the Supabase dashboard, go to **Settings → API**.
2. Copy:
   - **Project URL** → `SUPABASE_URL`
   - **anon public** key → `SUPABASE_KEY`

### Add to `.env`

```env
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_KEY=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...
```

### Create Tables

Run these SQL statements in the Supabase **SQL Editor**:

```sql
-- Tick Data (for historical storage)
CREATE TABLE tick_data (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    symbol TEXT NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL,
    price NUMERIC NOT NULL,
    volume INT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_tick_data_symbol_ts ON tick_data(symbol, timestamp);

-- Levels (daily computed levels)
CREATE TABLE levels (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    date DATE NOT NULL,
    symbol TEXT NOT NULL,
    pdh NUMERIC,
    pdl NUMERIC,
    pdc NUMERIC,
    orh NUMERIC,
    orl NUMERIC,
    vwap NUMERIC,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(date, symbol)
);

-- Trade Journal
CREATE TABLE trade_journal (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id UUID,
    timestamp TIMESTAMPTZ NOT NULL,
    symbol TEXT NOT NULL,
    direction TEXT NOT NULL,
    entry_price NUMERIC NOT NULL,
    exit_price NUMERIC,
    pnl_ticks NUMERIC,
    regime TEXT,
    playbook TEXT,
    features_json JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_trade_journal_regime ON trade_journal(regime);

-- Learned Parameters
CREATE TABLE learned_params (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    strategy TEXT NOT NULL,
    regime TEXT NOT NULL,
    params_json JSONB NOT NULL,
    score NUMERIC,
    sample_size INT,
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    reason TEXT,
    UNIQUE(strategy, regime)
);
```

---

## 2. Email Alert Setup (Gmail SMTP)

### Enable App Passwords

1. Go to [myaccount.google.com](https://myaccount.google.com).
2. Navigate to **Security → 2-Step Verification** (must be enabled).
3. At the bottom, click **App passwords**.
4. Generate a new app password for "Mail" on "Windows Computer".
5. Copy the 16-character password.

### Add to `.env`

```env
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=your-email@gmail.com
SMTP_PASSWORD=your-16-char-app-password
ALERT_RECIPIENTS=your-email@gmail.com,another@example.com
```

### Alternative: SendGrid

If you prefer SendGrid:

1. Create a SendGrid account at [sendgrid.com](https://sendgrid.com).
2. Go to **Settings → API Keys → Create API Key**.
3. Add to `.env`:

```env
SMTP_HOST=smtp.sendgrid.net
SMTP_PORT=587
SMTP_USER=apikey
SMTP_PASSWORD=SG.your-api-key
ALERT_RECIPIENTS=your-email@gmail.com
```

---

## 3. Windows Task Scheduler (Daily Auto-Run)

### Create a Scheduled Task

1. Open **Task Scheduler** (search in Start Menu).
2. Click **Create Task** (not Basic Task).
3. **General tab**:
   - Name: `TSXBot Daily Run`
   - Check: "Run whether user is logged on or not"
4. **Triggers tab**:
   - New → Daily → Start: 9:25 AM (5 min before RTH)
5. **Actions tab**:
   - New → Start a program
   - Program: `C:\Users\saina\trading\tsxbot\.venv\Scripts\python.exe`
   - Arguments: `-m tsxbot run --dry-run`
   - Start in: `C:\Users\saina\trading\tsxbot`
6. **Conditions tab**:
   - Uncheck "Start only if on AC power" (for laptops)
7. Click OK and enter your Windows password.

### Verify

Run manually from Task Scheduler to confirm it starts correctly.
