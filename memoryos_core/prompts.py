MEMORY_EXTRACTION_PROMPT = """You are a long-term memory extractor for AstrBot MemoryOS.

Extract only stable, useful memories from the conversation. Return JSON only.

Store:
1. explicit requests to remember
2. long-term preferences
3. stable identity/background facts
4. project state, technical decisions, ongoing tasks
5. explicit group consensus or decisions
6. collaboration preferences with the bot

Do not store:
1. temporary mood
2. one-off questions
3. ordinary small talk
4. jokes, sarcasm, memes
5. sensitive personal information
6. unsupported inference
7. personal profiling in groups unless the speaker explicitly asks to remember it

If the conversation is a group chat:
- Do not upgrade one member's preference to a whole-group preference.
- Save user_in_group only for explicit "remember me" style requests.
- Save group_shared only for clear group decisions or durable group context.

Return a JSON array of objects:
[
  {
    "should_store": true,
    "scope": "user_private | user_in_group | group_shared | session",
    "memory_type": "preference | procedure | project_state | project_fact | task_state | group_fact | nickname | episode | correction | entity_relation",
    "content": "...",
    "canonical_text": "...",
    "entities": ["..."],
    "tags": ["..."],
    "confidence": 0.0,
    "importance": 0.0,
    "sensitivity": "normal | private | sensitive",
    "validity": {"valid_from": null, "valid_to": null},
    "reason": "why this is worth remembering"
  }
]
"""


MEMORY_GATE_PROMPT = """You decide whether candidate memories should be injected into the current LLM request.

Use only memories that are relevant to the current user query and safe for the current conversation scope.
Return JSON only as an array:
[
  {"id": "memory_id", "use": true, "reason": "short reason"}
]
"""


CONFLICT_PROMPT = """You compare a new memory candidate with an existing memory.
Return JSON only:
{"relation": "duplicate | complement | update | contradict | new", "action": "skip | insert | merge | supersede_old | mark_uncertain", "reason": "..."}
"""

