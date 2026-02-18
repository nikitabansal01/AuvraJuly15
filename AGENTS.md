# backend/chatbot/AGENTS.md

## This folder — LangGraph graph code only

Rules:
- State schema is in state.py — never modify it without reading it first
- All tools are in tools.py — never add a tool without wiring it to a node
- Checkpointer is configured in graph.py — always PostgresSaver in prod
- Intent classification is in classify_node() — always with_structured_output()
- ui_elements MUST be returned in state from any node that shows options
- user_preferences MUST be accumulated (not reset) in every node that touches it

## Run tests for this folder
pytest tests/chatbot/ -v
```

---

### Step 16 — Add a simple test file for intent coverage

Ask Codex to create this after the main fix:
```
Create a file tests/chatbot/test_intent_coverage.py that tests all 10 
user intent scenarios from the Phase 4 checklist. Use pytest. 
Mock the LLM calls. Verify state updates for each intent.
```

---

## SUMMARY — The Exact Order
```
TODAY:
  [ ] 1. Install Codex CLI
  [ ] 2. Authenticate (codex auth login)
  [ ] 3. Set config.toml → gpt-5.3-codex, xhigh
  [ ] 4. Create ~/.codex/AGENTS.md (global rules)
  [ ] 5. Create AGENTS.md in repo root (project rules)
  [ ] 6. Fill in [FILL IN] sections in AGENTS.md
  [ ] 7. Create .agents/skills/langgraph-production/SKILL.md
  [ ] 8. Open Codex: cd your-project && codex --sandbox workspace-write --ask-for-approval on-request
  [ ] 9. Verify AGENTS.md loaded (ask Codex what instructions it has)
  [ ] 10. Paste the main task prompt
  [ ] 11. Steer mid-run if needed
  [ ] 12. Review and approve each apply_patch diff
  [ ] 13. Manual smoke test (5 flows)
  [ ] 14. Fix any failures
  [ ] 15. git commit

AFTER:
  [ ] 16. Add chatbot subfolder AGENTS.md
  [ ] 17. Ask Codex to create intent coverage tests
  [ ] 18. Run full test suite

