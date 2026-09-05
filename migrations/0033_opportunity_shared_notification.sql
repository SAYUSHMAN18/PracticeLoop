-- share_opportunity (app/classrooms/service.py) notifies classroom members
-- with kind='opportunity_shared', same fan-out notify_classroom_members
-- already uses for 'assignment_created' -- the notifications_kind_check
-- constraint (0020, extended in 0025) needs to allow it too.
ALTER TABLE notifications DROP CONSTRAINT IF EXISTS notifications_kind_check;
ALTER TABLE notifications ADD CONSTRAINT notifications_kind_check
  CHECK (kind IN (
    'assignment_created', 'guardian_accepted', 'badge_earned', 'streak_milestone', 'opportunity_shared'
  ));
