ALTER TABLE mesa_results DROP CONSTRAINT IF EXISTS mesa_results_overall_status_check;
UPDATE mesa_results SET overall_status = 'needs_review_large_delta' WHERE overall_status = 'critical';
ALTER TABLE mesa_results ADD CONSTRAINT mesa_results_overall_status_check CHECK (overall_status IN ('clean', 'known_anomaly', 'warning', 'discrepancy', 'needs_review_large_delta'));
