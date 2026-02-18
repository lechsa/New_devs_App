# Property Revenue Dashboard — Issues & Fixes

## Reported Problems

1. **Client A (Sunset Properties):** Revenue totals for March don't match their internal records.
2. **Client B (Ocean Rentals):** On refresh, they sometimes see revenue numbers belonging to another company.
3. **Finance team:** Revenue totals are occasionally "slightly off" by a few cents.

---

## Bug 1 — Cross-Tenant Cache Poisoning (CRITICAL)

**File:** `backend/app/services/cache.py` line 14  
**Reported symptom:** Client B sees another company's numbers on refresh.

### Root Cause

The Redis cache key is built as `f"revenue:{property_id}"` and does **not** include `tenant_id`. Since `prop-001` exists in both tenants (Beach House Alpha for tenant-a, Mountain Lodge Beta for tenant-b), whichever tenant queries first gets their revenue cached. The other tenant then receives the wrong data from cache for up to 5 minutes.

```python
# BEFORE (bug)
cache_key = f"revenue:{property_id}"
```

### Fix

Include `tenant_id` in the cache key so each tenant's data is isolated:

```python
# AFTER (fix)
cache_key = f"revenue:{tenant_id}:{property_id}"
```

---

## Bug 2 — Timezone Boundary Misclassification (HIGH)

**File:** `backend/app/services/reservations.py` lines 7-12, `database/seed.sql`  
**Reported symptom:** Client A's March revenue totals don't match their records.

### Root Cause

Reservation `res-tz-1` has `check_in_date = '2024-02-29 23:30:00+00'` (UTC). The property `prop-001` for tenant-a is in `Europe/Paris` (UTC+1), so the local check-in time is `2024-03-01 00:30:00 CET` — this **should** count as a March reservation. However, `calculate_monthly_revenue` constructs date boundaries using naive Python `datetime` objects (no timezone awareness), so the query classifies this reservation as February, losing **€1,250.00** from March totals.

```python
# BEFORE (bug) — naive datetime, no timezone conversion
start_date = datetime(year, month, 1)
```

### Fix

Convert date boundaries to the property's local timezone before querying, or query using timezone-aware timestamps that account for each property's configured timezone.

```python
# AFTER (fix) — use property timezone for date boundaries
import pytz

def get_month_boundaries(year, month, timezone_str):
    tz = pytz.timezone(timezone_str)
    start_local = tz.localize(datetime(year, month, 1))
    if month < 12:
        end_local = tz.localize(datetime(year, month + 1, 1))
    else:
        end_local = tz.localize(datetime(year + 1, 1, 1))
    return start_local.astimezone(pytz.utc), end_local.astimezone(pytz.utc)
```

---

## Bug 3 — `calculate_monthly_revenue` Missing `tenant_id` Parameter (HIGH)

**File:** `backend/app/services/reservations.py` lines 5-33  
**Reported symptom:** Monthly revenue queries would fail or return wrong results.

### Root Cause

The SQL query inside `calculate_monthly_revenue` references `tenant_id = $2`, but the function signature is `(property_id, month, year, db_session)` — it never receives or binds `tenant_id`. This means the query either crashes or ignores tenant filtering entirely.

```python
# BEFORE (bug)
async def calculate_monthly_revenue(property_id: str, month: int, year: int, db_session=None):
    query = """... WHERE property_id = $1 AND tenant_id = $2 ..."""
    # tenant_id is never passed
```

### Fix

Add `tenant_id` to the function signature and bind it in the query:

```python
# AFTER (fix)
async def calculate_monthly_revenue(property_id: str, tenant_id: str, month: int, year: int, db_session=None):
    # ... and pass tenant_id as the $2 parameter when executing
```

---

## Bug 4 — Mock Fallback Data Not Tenant-Scoped (HIGH)

**File:** `backend/app/services/reservations.py` lines 88-96  
**Reported symptom:** Both tenants see identical revenue for shared property IDs when DB is unavailable.

### Root Cause

When the database connection fails, `calculate_total_revenue` falls back to a hardcoded `mock_data` dictionary keyed only by `property_id`. Since `prop-001` exists in both tenants, they both receive the same mock revenue (`1000.00`), regardless of which tenant is requesting.

```python
# BEFORE (bug) — mock data ignores tenant
mock_data = {
    'prop-001': {'total': '1000.00', 'count': 3},
    ...
}
```

### Fix

Key mock data by both `tenant_id` and `property_id`:

```python
# AFTER (fix)
mock_data = {
    ('tenant-a', 'prop-001'): {'total': '2250.00', 'count': 4},
    ('tenant-b', 'prop-001'): {'total': '800.00', 'count': 2},
    ...
}
mock_property_data = mock_data.get((tenant_id, property_id), {'total': '0.00', 'count': 0})
```

---

## Bug 5 — `float()` Precision Loss on Revenue Totals (MEDIUM)

**File:** `backend/app/api/v1/dashboard.py` line 19  
**Reported symptom:** Finance team sees totals "slightly off" by a few cents.

### Root Cause

The database stores `total_amount` as `NUMERIC(10,3)` (3 decimal places for sub-cent tracking). The service correctly uses Python `Decimal` and serializes to string. But the dashboard endpoint converts it via `float()`:

```python
# BEFORE (bug)
total_revenue_float = float(revenue_data['total'])
```

This introduces IEEE 754 floating-point imprecision. Combined with the frontend's `Math.round(data.total_revenue * 100) / 100`, the `Decimal → str → float → JS number → Math.round` pipeline silently loses precision. The seed data deliberately uses values like `333.333 + 333.333 + 333.334` to expose this.

### Fix

Round to 2 decimal places in Python before serializing, or transmit the value as a string and use a decimal library on the frontend:

```python
# AFTER (fix) — option A: round in Python
total_revenue_float = round(float(revenue_data['total']), 2)

# AFTER (fix) — option B: send as string, parse on frontend
"total_revenue": revenue_data['total']  # keep as string
```

---

## Summary Table

| # | Bug | Severity | Symptom | File |
|---|-----|----------|---------|------|
| 1 | Cache key missing `tenant_id` | **Critical** | Cross-tenant data leak | `backend/app/services/cache.py` |
| 2 | Timezone-naive month boundaries | **High** | March revenue off by €1,250 | `backend/app/services/reservations.py` |
| 3 | `calculate_monthly_revenue` missing `tenant_id` param | **High** | Query fails or ignores tenant | `backend/app/services/reservations.py` |
| 4 | Mock fallback data not tenant-scoped | **High** | Both tenants get same mock data | `backend/app/services/reservations.py` |
| 5 | `float()` precision loss | **Medium** | Totals off by a few cents | `backend/app/api/v1/dashboard.py` |