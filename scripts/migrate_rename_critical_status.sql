-- Rename overall_status 'critical' → 'needs_review_large_delta'
-- Run once in Supabase SQL editor after deploying the code change.
UPDATE mesa_results
SET overall_status = 'needs_review_large_delta'
WHERE overall_status = 'critical';
