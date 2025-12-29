# 🚀 AUVRA Timezone Quick Reference

## 🎯 Quick Rules

### Rule #1: Never use system date/time for user logic
```python
# ❌ WRONG
today = date.today()
now = datetime.now()

# ✅ CORRECT
from app.utils.timezone_utils import get_user_current_date
today = get_user_current_date(uid, db)
```

### Rule #2: Always get user's timezone from database
```python
# ❌ WRONG
timezone = "Asia/Seoul"  # Hardcoded

# ✅ CORRECT
from app.utils.timezone_utils import get_user_timezone
timezone = get_user_timezone(uid, db)
```

### Rule #3: Store UTC in database, convert for logic
```python
# ✅ Store UTC
plan.created_at = datetime.utcnow()

# ✅ Convert for display/logic
user_date = convert_from_utc(plan.created_at, user_timezone)
```

---

## 📦 Essential Imports

```python
from app.utils.timezone_utils import (
    get_user_current_date,        # Most common: get today in user's TZ
    get_user_timezone,            # Get user's timezone string
    validate_timezone,            # Validate before storing
)
```

---

## 🔧 Common Tasks

### Get today's date for a user
```python
from app.utils.timezone_utils import get_user_current_date

today = get_user_current_date(uid, db)
# Returns: date(2025, 12, 29) in user's timezone
```

### Calculate streak using user's timezone
```python
from app.services.streak_service import StreakService
from app.utils.timezone_utils import get_user_timezone

user_tz = get_user_timezone(uid, db)
streak_service = StreakService(db)
streak = streak_service.calculate_streak_from_actions(uid, user_tz)
```

### Generate action plan for user's "today"
```python
from app.services.action_plan_generator import get_action_plan_generator
from app.utils.timezone_utils import get_user_timezone

user_tz = get_user_timezone(uid, db)
generator = get_action_plan_generator()
plan = await generator.get_or_generate_today_plan(uid, user_tz, db)
```

### Check if user completed today
```python
from app.utils.timezone_utils import get_user_current_date

today = get_user_current_date(uid, db)
plan = db.query(ActionPlan).filter(
    and_(
        ActionPlan.uid == uid,
        ActionPlan.plan_date == today
    )
).first()
```

---

## 🌍 API Integration

### Mobile app should send timezone
```javascript
// Get device timezone
const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone;

// Send on requests
fetch('/api/v1/new-scheduling/assignments/today?timezone=' + timezone)
```

### Update user's timezone
```bash
POST /api/v1/timezone/update
{
  "timezone": "America/New_York"
}
```

---

## 🧪 Quick Test

```python
# Test timezone utilities
from app.utils.timezone_utils import *

# Test get user date
uid = "test_user"
today = get_user_current_date(uid, db)
print(f"Today for {uid}: {today}")

# Test timezone validation
assert validate_timezone("America/New_York") == True
assert validate_timezone("Invalid/Zone") == False

# Test conversion
from datetime import datetime, date
from zoneinfo import ZoneInfo

utc_time = datetime(2025, 12, 29, 17, 0, tzinfo=ZoneInfo("UTC"))
tokyo_date = convert_from_utc(utc_time, "Asia/Tokyo")
# Tokyo: 2025-12-30 (next day)
ny_date = convert_from_utc(utc_time, "America/New_York")
# NY: 2025-12-29 (same day, 12:00 PM)
```

---

## ⚠️ Common Pitfalls

### Pitfall 1: Using date.today()
```python
# ❌ This uses SERVER timezone
today = date.today()

# ✅ Use this instead
today = get_user_current_date(uid, db)
```

### Pitfall 2: Hardcoding timezone
```python
# ❌ Don't assume timezone
timezone = "UTC"  # or "Asia/Seoul"

# ✅ Get from user profile
timezone = get_user_timezone(uid, db)
```

### Pitfall 3: Comparing dates without timezone context
```python
# ❌ Wrong: compares in server timezone
if plan.plan_date == date.today():
    ...

# ✅ Correct: use user's timezone
user_today = get_user_current_date(uid, db)
if plan.plan_date == user_today:
    ...
```

---

## 📍 Where Timezone Matters

✅ **Action Plan Generation** - Generate for user's "today"
✅ **Streak Calculation** - Count consecutive days in user's timezone
✅ **Progress Tracking** - Weekly/monthly in user's timezone
✅ **Reward Limits** - Daily limits reset at user's midnight
✅ **Freeze Tokens** - Protect user's local day
✅ **Schedule Firing** - Execute at user's local time

---

## 🔍 Debugging Tips

### Check user's timezone
```python
from app.utils.timezone_utils import get_user_timezone
print(f"User timezone: {get_user_timezone(uid, db)}")
```

### Check what "today" is for user
```python
from app.utils.timezone_utils import get_user_current_date
print(f"User's today: {get_user_current_date(uid, db)}")
```

### Check if two UTC times are same day for user
```python
from app.utils.timezone_utils import is_same_day_in_timezone

utc_time1 = datetime(2025, 12, 29, 23, 0, tzinfo=ZoneInfo("UTC"))
utc_time2 = datetime(2025, 12, 30, 1, 0, tzinfo=ZoneInfo("UTC"))

# In UTC: different days
# In Los Angeles: same day (Dec 29)
same_day = is_same_day_in_timezone(utc_time1, utc_time2, "America/Los_Angeles")
print(f"Same day in LA: {same_day}")  # True
```

---

## 📝 Code Review Checklist

When reviewing code, check for:
- [ ] Uses `get_user_current_date()` instead of `date.today()`
- [ ] Gets timezone from database, not hardcoded
- [ ] Stores UTC in database
- [ ] Converts to user timezone for logic
- [ ] Passes timezone to services that need it
- [ ] Validates timezone before storing

---

## 🆘 Emergency Contacts

If timezone issues occur:
1. Check logs for timezone errors
2. Verify user's timezone in database
3. Test timezone utilities independently
4. Check `TIMEZONE_IMPLEMENTATION.md` for details
5. Review `TIMEZONE_TESTING.md` for test scenarios

---

## 🎓 Learn More

- Implementation Guide: `TIMEZONE_IMPLEMENTATION.md`
- Testing Guide: `TIMEZONE_TESTING.md`
- Summary: `TIMEZONE_SUMMARY.md`
- Code: `app/utils/timezone_utils.py`
