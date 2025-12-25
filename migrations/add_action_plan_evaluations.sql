-- Migration: Add action_plan_evaluations table
-- Created: 2025-12-26
-- Description: Stores quality evaluation metrics for action plans to track accuracy trends

CREATE TABLE IF NOT EXISTS action_plan_evaluations (
    id SERIAL PRIMARY KEY,
    plan_id INTEGER NOT NULL UNIQUE REFERENCES action_plans(id) ON DELETE CASCADE,
    uid VARCHAR(255) NOT NULL,
    
    -- Structural Metrics
    structure_valid BOOLEAN NOT NULL DEFAULT TRUE,
    
    -- Relevance Metrics (0-100, LLM-evaluated)
    personalization_score INTEGER,
    condition_appropriateness INTEGER,
    feedback_alignment_score INTEGER,
    
    -- Citation Quality (0-100)
    citation_validity_score INTEGER,
    citation_relevance_score INTEGER,
    
    -- Aggregate
    overall_quality_score INTEGER,
    
    -- Metadata
    evaluation_cost VARCHAR(50),
    evaluation_time_ms INTEGER,
    evaluator_model VARCHAR(50) DEFAULT 'gpt-4o-mini',
    llm_evaluation_response JSONB,
    
    -- Timestamps
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Indexes for efficient queries
CREATE INDEX IF NOT EXISTS idx_evaluation_plan ON action_plan_evaluations(plan_id);
CREATE INDEX IF NOT EXISTS idx_evaluation_user ON action_plan_evaluations(uid, created_at);
CREATE INDEX IF NOT EXISTS idx_evaluation_score ON action_plan_evaluations(overall_quality_score);

-- Useful monitoring queries:

-- 1. Average quality score by day
-- SELECT DATE(created_at) as date, 
--        AVG(overall_quality_score) as avg_score,
--        COUNT(*) as plans_evaluated
-- FROM action_plan_evaluations 
-- GROUP BY DATE(created_at) 
-- ORDER BY date DESC;

-- 2. Low quality plans for review (score < 70)
-- SELECT * FROM action_plan_evaluations 
-- WHERE overall_quality_score < 70 
-- ORDER BY created_at DESC;

-- 3. Score distribution
-- SELECT 
--     CASE 
--         WHEN overall_quality_score >= 90 THEN 'Excellent (90+)'
--         WHEN overall_quality_score >= 70 THEN 'Good (70-89)'
--         WHEN overall_quality_score >= 50 THEN 'Average (50-69)'
--         ELSE 'Poor (<50)'
--     END as quality_tier,
--     COUNT(*) as count
-- FROM action_plan_evaluations
-- GROUP BY quality_tier;
