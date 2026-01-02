CREATE TABLE IF NOT EXISTS ai_model_usage_logs (
    id SERIAL PRIMARY KEY,
    plan_id INTEGER REFERENCES action_plans(id) ON DELETE CASCADE,
    user_id VARCHAR(255) NOT NULL,
    primary_model VARCHAR(100) NOT NULL, -- e.g., gpt-4o-mini
    fallback_model VARCHAR(100), -- e.g., llama-3.3-70b
    switch_reason TEXT, -- e.g., "Low condition_appropriateness score: 65"
    final_model_used VARCHAR(100) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_ai_model_usage_plan_id ON ai_model_usage_logs(plan_id);
CREATE INDEX idx_ai_model_usage_user_id ON ai_model_usage_logs(user_id);
