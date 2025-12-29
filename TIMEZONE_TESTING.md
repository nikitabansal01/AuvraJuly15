# AUVRA Timezone Implementation - Testing Checklist

## 🧪 Comprehensive Timezone Testing

This checklist ensures all timezone functionality works correctly across different scenarios.

---

## ✅ Pre-Deployment Checklist

### 1. Database Migration
- [ ] Run `python migrations/timezone_migration.py`
- [ ] Verify all users have `current_timezone` set
- [ ] Check migration logs for any errors
- [ ] Verify indexes created successfully

### 2. Utility Functions
- [ ] Test `get_user_current_date()` with different timezones
- [ ] Test `get_user_current_datetime()` returns timezone-aware datetime
- [ ] Test `validate_timezone()` with valid/invalid timezones
- [ ] Test `convert_to_utc()` and `convert_from_utc()`
- [ ] Test `is_same_day_in_timezone()` across date boundaries

### 3. Service Layer
- [ ] Streak Service uses user timezone for all calculations
- [ ] Action Plan Generator respects user timezone
- [ ] Progress Service calculates dates in user timezone
- [ ] Reward Service uses user timezone for daily limits

---

## 🌍 Multi-Timezone Scenarios

### Scenario 1: Date Boundary Test

**Setup**: Two users at the same UTC time, different timezones
- User A: New York (UTC-5) → December 29, 2025 @ 11:00 PM EST
- User B: Tokyo (UTC+9) → December 30, 2025 @ 1:00 PM JST
- UTC Time: December 30, 2025 @ 4:00 AM UTC

**Tests**:
- [ ] User A sees action plan for: **2025-12-29**
- [ ] User B sees action plan for: **2025-12-30**
- [ ] User A's streak counts through 2025-12-29
- [ ] User B's streak counts through 2025-12-30
- [ ] Both users can complete today's actions correctly

**API Calls**:
```bash
# User A (New York)
curl -X GET "http://localhost:8000/api/v1/new-scheduling/assignments/today?timezone=America/New_York" \
  -H "Authorization: Bearer USER_A_TOKEN"

# User B (Tokyo)  
curl -X GET "http://localhost:8000/api/v1/new-scheduling/assignments/today?timezone=Asia/Tokyo" \
  -H "Authorization: Bearer USER_B_TOKEN"
```

**Expected Results**:
- User A: `plan_date: "2025-12-29"`
- User B: `plan_date: "2025-12-30"`

---

### Scenario 2: DST Transition Test

**Setup**: User in timezone with DST during transition
- Timezone: America/New_York
- Date: March 9, 2025 (DST begins - clocks "spring forward")
- Before: 1:59 AM EST → After: 3:00 AM EDT

**Tests**:
- [ ] Streak calculation doesn't skip March 9th
- [ ] No duplicate day counted
- [ ] Hour 2:00 AM doesn't exist (handled gracefully)
- [ ] Action plan generation works correctly
- [ ] Next fire times calculated correctly

**Verification**:
```python
# Check streak continuity around DST transition
dates = [
    date(2025, 3, 8),   # Before DST
    date(2025, 3, 9),   # DST transition day
    date(2025, 3, 10),  # After DST
]

for d in dates:
    plan = get_plan_for_date(uid, d)
    assert plan is not None, f"Plan missing for {d}"
```

---

### Scenario 3: User Travels Across Timezones

**Setup**: User changes timezone during active streak
- Day 1: New York (UTC-5) - Complete actions
- Day 2: London (UTC+0) - Change timezone, complete actions  
- Day 3: Tokyo (UTC+9) - Change timezone, complete actions

**Tests**:
- [ ] Streak continues correctly (not broken by timezone change)
- [ ] Each day uses correct local date
- [ ] Historical plans retain original dates
- [ ] Progress tracking accurate across timezone changes

**API Sequence**:
```bash
# Day 1 - New York
POST /api/v1/timezone/update {"timezone": "America/New_York"}
GET /api/v1/new-scheduling/assignments/today  # Date: 2025-12-29
POST /api/v1/new-scheduling/assignments/1/complete

# Day 2 - London  
POST /api/v1/timezone/update {"timezone": "Europe/London"}
GET /api/v1/new-scheduling/assignments/today  # Date: 2025-12-30
POST /api/v1/new-scheduling/assignments/2/complete

# Day 3 - Tokyo
POST /api/v1/timezone/update {"timezone": "Asia/Tokyo"}
GET /api/v1/new-scheduling/assignments/today  # Date: 2025-12-31
POST /api/v1/new-scheduling/assignments/3/complete

# Check streak
GET /api/v1/rewards  # Should show current_streak: 3
```

---

### Scenario 4: Midnight Edge Cases

**Setup**: Test behavior around midnight in user's timezone

**Test 1: Just before midnight**
- Local Time: 2025-12-29 23:59:00
- Action: Generate today's plan

Expected: Plan for 2025-12-29

**Test 2: Just after midnight**
- Local Time: 2025-12-30 00:01:00
- Action: Generate today's plan

Expected: Plan for 2025-12-30

**Test 3: Complete action before midnight, check streak after midnight**
- 23:50: Complete all actions for 2025-12-29
- 00:10: Check streak

Expected: Streak includes 2025-12-29, "today" is now 2025-12-30

---

## 🧪 API Endpoint Tests

### Timezone Management

#### Test: Update Timezone
```bash
POST /api/v1/timezone/update
{
  "timezone": "America/Los_Angeles"
}
```
- [ ] Returns success
- [ ] User profile updated in database
- [ ] Invalid timezone rejected with 400 error

#### Test: Get Current Timezone
```bash
GET /api/v1/timezone/current
```
- [ ] Returns user's current timezone
- [ ] Returns default "UTC" for new users

#### Test: Validate Timezone
```bash
GET /api/v1/timezone/validate/America/New_York
GET /api/v1/timezone/validate/InvalidTimezone
```
- [ ] Valid timezone returns `valid: true`
- [ ] Invalid timezone returns `valid: false`

---

### Action Plan API

#### Test: Generate Today's Plan
```bash
GET /api/v1/new-scheduling/assignments/today?timezone=Asia/Tokyo
```
- [ ] Uses provided timezone for date calculation
- [ ] Updates user profile with new timezone
- [ ] Returns plan for correct local date

#### Test: Get Historical Plan
```bash
GET /api/v1/new-scheduling/assignments/2025-12-25
```
- [ ] Returns plan for specified date (if exists)
- [ ] Uses user's timezone for any calculations
- [ ] Returns empty response if no plan exists

---

### Streak & Rewards API

#### Test: Get Streak Status
```bash
GET /api/v1/rewards
```
- [ ] `current_streak` calculated in user's timezone
- [ ] `last_activity_date` shown in local date
- [ ] `today_completed` uses local date

#### Test: Use Freeze Proactive
```bash
POST /api/v1/rewards/use-freeze
```
- [ ] Freezes today (user's local date)
- [ ] Protects streak for current day
- [ ] Can't freeze same day twice

---

## 🔧 Service Layer Tests

### Streak Service

```python
def test_streak_calculation_different_timezones():
    # User in Tokyo completes actions
    uid = "test_user_tokyo"
    user_timezone = "Asia/Tokyo"
    
    # Create action plans for consecutive days
    create_completed_plan(uid, date(2025, 12, 27))
    create_completed_plan(uid, date(2025, 12, 28))
    create_completed_plan(uid, date(2025, 12, 29))
    
    # Calculate streak
    streak_service = StreakService(db)
    streak = streak_service.calculate_streak_from_actions(uid, user_timezone)
    
    assert streak == 3, f"Expected streak of 3, got {streak}"
```

#### Tests:
- [ ] Streak calculation uses user timezone
- [ ] Freeze protects correct local day
- [ ] Missed day detection uses local dates
- [ ] Longest streak calculation accurate

---

### Action Plan Generator

```python
async def test_action_plan_generation_timezone():
    uid = "test_user"
    user_timezone = "America/New_York"
    
    # Generate plan
    result = await generator.get_or_generate_today_plan(
        user_id=uid,
        user_timezone=user_timezone,
        db=db
    )
    
    # Verify plan date is today in user's timezone
    from app.utils.timezone_utils import get_user_current_date
    expected_date = get_user_current_date(uid, db)
    
    assert result["plan_date"] == expected_date.isoformat()
```

#### Tests:
- [ ] Plan generated for correct local date
- [ ] Existing plan retrieved correctly
- [ ] No duplicate plans for same date
- [ ] Plan carries forward from frozen day correctly

---

## 🌐 Real-World Testing

### Test with Real Timezones

Pick 5 diverse timezones:
1. **America/New_York** (UTC-5/-4, has DST)
2. **Europe/London** (UTC+0/+1, has DST)
3. **Asia/Tokyo** (UTC+9, no DST)
4. **Australia/Sydney** (UTC+10/+11, has DST, Southern Hemisphere)
5. **Pacific/Auckland** (UTC+12/+13, has DST, crosses date line)

For each timezone:
- [ ] Create test user
- [ ] Set timezone
- [ ] Generate action plan
- [ ] Complete actions
- [ ] Verify streak
- [ ] Use freeze
- [ ] Check rewards

---

## 📊 Monitoring & Logs

### Log Checks

Search logs for these patterns:

#### Success Patterns:
```
✅ User {uid} current date: 2025-12-29 (timezone: America/New_York)
✅ Streak calc for {uid}: user_timezone=Asia/Tokyo, today=2025-12-30
✅ Plan generated for user {uid} on 2025-12-29
```

#### Error Patterns to Watch:
```
❌ Failed to get user timezone
❌ Invalid timezone
❌ Failed to convert to local date
❌ Timezone not set for user
```

### Monitoring Queries

```sql
-- Check timezone distribution
SELECT current_timezone, COUNT(*) 
FROM user_profiles 
GROUP BY current_timezone 
ORDER BY COUNT(*) DESC;

-- Find users without timezone
SELECT uid, name, email 
FROM user_profiles 
WHERE current_timezone IS NULL;

-- Check for timezone-related errors
SELECT uid, plan_date, created_at 
FROM action_plans 
WHERE plan_date != DATE(created_at AT TIME ZONE 'UTC');
```

---

## 🎯 Performance Tests

### Load Test

Simulate 1000 concurrent requests from different timezones:
- [ ] Response time < 500ms
- [ ] No timezone conversion errors
- [ ] Correct date for each user's timezone
- [ ] Database load acceptable

---

## ✅ Final Checklist

Before going to production:

- [ ] All database migrations run successfully
- [ ] All utility functions tested
- [ ] All service layer tests pass
- [ ] All API endpoints tested
- [ ] Multi-timezone scenarios validated
- [ ] DST transitions handled correctly
- [ ] Edge cases (midnight, date boundaries) tested
- [ ] Documentation complete
- [ ] Logs reviewed
- [ ] Performance acceptable
- [ ] Mobile app integration tested
- [ ] Rollback plan in place

---

## 🚨 Emergency Rollback

If critical issues found:

1. **Revert API changes** (remove timezone parameter handling)
2. **Disable timezone validation** (allow NULL temporarily)
3. **Switch to server timezone** (temporary fallback)
4. **Monitor logs** for root cause
5. **Fix and re-deploy** with proper testing

---

## 📞 Support

For issues or questions:
- Check `TIMEZONE_IMPLEMENTATION.md` for implementation details
- Review logs for timezone-related errors
- Test with `migrations/timezone_migration.py --verify`
