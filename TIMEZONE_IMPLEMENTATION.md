# AUVRA Timezone Implementation Guide

## 🌍 Overview

This implementation ensures **all date/time calculations use the user's local timezone**, which is critical for:
- **Daily Action Plans**: Generating plans for the correct "today" in user's timezone
- **Streak Calculations**: Counting consecutive days based on user's local dates
- **Scheduling**: Firing recommendations at the right time in user's timezone
- **All Date-Based Features**: Progress tracking, insights, rewards, etc.

## 🎯 Key Principles

### 1. **UTC as Universal Reference**
- All `DateTime` fields in database store UTC timestamps
- Internal calculations use UTC
- Convert to user's timezone only for display/business logic

### 2. **User Timezone Storage**
- Every user has `current_timezone` field in `user_profiles` table
- Uses IANA timezone identifiers (e.g., "America/New_York", "Asia/Tokyo")
- Defaults to "UTC" for safety

### 3. **Timezone-Aware Date Calculations**
- "Today" is calculated in user's timezone
- Date boundaries respect timezone (e.g., midnight in New York vs Tokyo)
- DST transitions handled automatically by Python's `zoneinfo`

## 📦 Implementation Components

### 1. Core Utilities (`app/utils/timezone_utils.py`)

Provides all timezone conversion functions:

```python
from app.utils.timezone_utils import (
    get_user_current_date,        # Get today's date in user's timezone
    get_user_current_datetime,    # Get current datetime in user's timezone
    get_user_timezone,            # Get user's timezone string
    convert_to_utc,               # Convert local date to UTC
    convert_from_utc,             # Convert UTC to local date
    validate_timezone,            # Validate IANA timezone string
    is_same_day_in_timezone,      # Check if two UTC times are same day locally
)
```

**Key Functions:**

- `get_user_current_date(uid, db)` - Always use this instead of `date.today()`
- `get_user_timezone(uid, db)` - Get user's timezone for calculations
- `validate_timezone(tz_str)` - Validate before storing

### 2. Database Schema

**User Profile Table:**
```sql
user_profiles:
  - uid (PK)
  - current_timezone VARCHAR(50) DEFAULT 'UTC'  -- IANA timezone
  - ... other fields
```

**Action Plan Table:**
```sql
action_plans:
  - id (PK)
  - uid (FK)
  - plan_date DATE  -- Date in user's timezone
  - created_at DATETIME  -- UTC timestamp
  - ... other fields
```

### 3. Service Layer Updates

**Streak Service** (`app/services/streak_service.py`):
```python
class StreakService:
    def calculate_streak_from_actions(self, uid: str, user_timezone: str = None):
        # Uses user's timezone to determine "today" and "yesterday"
        today = self._get_user_today(uid, user_timezone)
        check_date = today - timedelta(days=1)
        # ... streak calculation
```

**Action Plan Generator** (`app/services/action_plan_generator.py`):
```python
async def get_or_generate_today_plan(self, user_id, user_timezone, db):
    # Gets today in user's timezone
    today = self._get_user_today(user_timezone)
    # ... generate plan for that date
```

**Reward Service** (`app/services/reward_service.py`):
```python
def get_refresh_status(self, uid: str):
    # Uses user's current date for "today"
    today = get_user_current_date(uid, self.db)
    # ... check refresh limits
```

**Progress Service** (`app/services/progress_service.py`):
```python
def get_weekly_progress(self, uid: str, target_date: date = None):
    if target_date is None:
        target_date = get_user_current_date(uid, self.db)
    # ... calculate progress
```

### 4. API Endpoints

**New Timezone Management API:**
```
POST /api/v1/timezone/update
GET  /api/v1/timezone/current
GET  /api/v1/timezone/validate/{timezone_str}
```

**Action Plan API (Enhanced):**
```
GET /api/v1/new-scheduling/assignments/today?timezone=America/New_York
```
- Accepts optional `timezone` parameter
- Updates user profile if provided
- Uses user's timezone for all calculations

**Rewards API:**
```
GET /api/v1/rewards
```
- Returns streak calculated in user's timezone
- Freeze tokens respect user's local dates

## 🔧 Usage Examples

### Example 1: Generate Today's Action Plan

```python
# Client sends timezone on every request (optional but recommended)
GET /api/v1/new-scheduling/assignments/today?timezone=America/Los_Angeles

# Server:
1. Updates user profile if timezone changed
2. Calculates "today" in user's timezone (e.g., 2025-12-29 in LA)
3. Generates or retrieves plan for that date
4. Returns plan
```

### Example 2: Calculate Streak

```python
from app.services.streak_service import StreakService
from app.utils.timezone_utils import get_user_timezone

# Get user's timezone
user_tz = get_user_timezone(uid, db)  # "Asia/Tokyo"

# Calculate streak (uses user's local dates)
streak_service = StreakService(db)
current_streak = streak_service.calculate_streak_from_actions(uid, user_tz)
# Counts consecutive days in user's timezone
```

### Example 3: Check if User Completed Today

```python
from app.utils.timezone_utils import get_user_current_date

# Get today in user's timezone
user_today = get_user_current_date(uid, db)  # 2025-12-29

# Check for plan on that date
plan = db.query(ActionPlan).filter(
    and_(
        ActionPlan.uid == uid,
        ActionPlan.plan_date == user_today
    )
).first()

# Check if all items completed
if plan:
    total = count(items in plan where not replaced)
    completed = count(items in plan where completed and not replaced)
    is_complete = (total > 0 and completed == total)
```

## 🚀 Migration Guide

### For Existing Users

Run the migration script to set default timezone:

```bash
cd /Users/mohanganesh/AUVRA/AuvraJuly15
python migrations/timezone_migration.py
```

This will:
1. Set `current_timezone = 'UTC'` for all users without timezone
2. Verify all users have timezone set
3. Add timezone-related indexes

### Mobile App Integration

The mobile app should:

1. **On App Launch**: Detect device timezone
   ```javascript
   const deviceTimezone = Intl.DateTimeFormat().resolvedOptions().timeZone;
   // e.g., "America/New_York"
   ```

2. **Send on Every API Request**:
   ```javascript
   // Include timezone in query params or headers
   GET /api/v1/new-scheduling/assignments/today?timezone=${deviceTimezone}
   ```

3. **Update on Timezone Change**:
   ```javascript
   // When user travels or changes timezone
   POST /api/v1/timezone/update
   {
     "timezone": "Europe/London"
   }
   ```

## ⚠️ Critical Implementation Notes

### DO's ✅

1. **Always use utility functions for user dates**:
   ```python
   # ✅ CORRECT
   from app.utils.timezone_utils import get_user_current_date
   today = get_user_current_date(uid, db)
   
   # ❌ WRONG
   today = date.today()  # Uses server timezone!
   ```

2. **Store UTC in database, convert for logic**:
   ```python
   # ✅ CORRECT
   plan.created_at = datetime.utcnow()  # UTC in DB
   user_date = convert_from_utc(plan.created_at, user_tz)  # Convert for display
   ```

3. **Pass timezone to services**:
   ```python
   # ✅ CORRECT
   streak_service.calculate_streak_from_actions(uid, user_timezone)
   ```

### DON'Ts ❌

1. **Don't use system date/time**:
   ```python
   # ❌ WRONG
   date.today()           # Server timezone
   datetime.now()         # Server timezone
   datetime.utcnow()      # OK for UTC storage, not for user logic
   ```

2. **Don't assume timezone**:
   ```python
   # ❌ WRONG
   timezone = "Asia/Seoul"  # Hardcoded default
   
   # ✅ CORRECT
   timezone = get_user_timezone(uid, db)  # From database
   ```

3. **Don't mix naive and aware datetimes**:
   ```python
   # ❌ WRONG
   dt = datetime.now()  # Naive
   
   # ✅ CORRECT
   from zoneinfo import ZoneInfo
   dt = datetime.now(ZoneInfo("UTC"))  # Aware
   ```

## 🧪 Testing Scenarios

### 1. Date Boundary Test
```python
# User in Tokyo (UTC+9): 2025-12-30 02:00 JST
# User in LA (UTC-8): 2025-12-29 09:00 PST
# UTC time: 2025-12-29 17:00 UTC

# Tokyo user should see: plan_date = 2025-12-30
# LA user should see: plan_date = 2025-12-29
```

### 2. DST Transition Test
```python
# User in New York during DST change (March 2025)
# Before: EDT (UTC-4)
# After: EST (UTC-5)

# Ensure streak calculation handles DST transition
# Day count should not skip or duplicate
```

### 3. Multi-Timezone Users
```python
# User travels from New York to Tokyo
# Streak should continue correctly
# "Today" should update to Tokyo date
```

## 📊 Monitoring & Logging

All timezone operations log:
- User ID
- Timezone used
- Dates calculated
- Any conversion errors

Check logs for:
```
User {uid} current date: 2025-12-29 (timezone: America/New_York)
Streak calc for {uid}: user_timezone=Asia/Tokyo, today=2025-12-30, starting from yesterday=2025-12-29
```

## 🔍 Troubleshooting

### Issue: User sees wrong date for action plan
**Solution**: Check user's timezone in database, ensure mobile app sends correct timezone

### Issue: Streak breaks unexpectedly
**Solution**: Check if dates are calculated in user's timezone, not server timezone

### Issue: Invalid timezone error
**Solution**: Validate timezone string using `validate_timezone()` before storing

## 📚 Resources

- [IANA Time Zone Database](https://www.iana.org/time-zones)
- [Python zoneinfo Documentation](https://docs.python.org/3/library/zoneinfo.html)
- [Timezone Best Practices](https://en.wikipedia.org/wiki/List_of_tz_database_time_zones)

## 🎯 Summary

**Before this implementation:**
- ❌ Dates calculated in server timezone
- ❌ Streaks broken for users in different timezones
- ❌ Action plans generated at wrong times

**After this implementation:**
- ✅ All dates calculated in user's timezone
- ✅ Streaks accurate for all users worldwide
- ✅ Action plans generated at correct local times
- ✅ DST transitions handled automatically
- ✅ Consistent behavior across all features
