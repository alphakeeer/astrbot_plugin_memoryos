MEMORY_EXTRACTION_PROMPT = """你是 AstrBot MemoryOS 的长期记忆抽取器。

请只从对话中抽取稳定、明确、长期有用的记忆。只输出 JSON，不要输出解释文字。

应该保存：
1. 用户明确要求“记住”的内容
2. 长期偏好
3. 稳定身份或背景信息
4. 项目状态、技术决策、持续任务
5. 群聊中的明确共识或群体决策
6. 用户与机器人协作方式偏好

不要保存：
1. 临时情绪
2. 一次性问题
3. 普通闲聊
4. 玩笑、反讽、表情包梗
5. 敏感个人信息
6. 没有证据的推断
7. 群聊中的个人画像，除非发言人明确要求记住

如果当前是群聊：
- 不要把单个成员的偏好升级为整个群的偏好。
- 只有用户明确要求“记住我...”时，才保存 user_in_group。
- 只有明确群体决策、群公告或持久群上下文，才保存 group_shared。

返回 JSON 数组：
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
    "reason": "为什么值得长期保存"
  }
]
"""


MEMORY_GATE_PROMPT = """你负责判断候选记忆是否应该注入当前 LLM 请求。

只使用与当前用户问题直接相关、且在当前会话作用域中安全的记忆。
只输出 JSON 数组：
[
  {"id": "memory_id", "use": true, "reason": "简短原因"}
]
"""


CONFLICT_PROMPT = """你负责比较一条新记忆候选和一条已有记忆。
只输出 JSON：
{"relation": "duplicate | complement | update | contradict | new", "action": "skip | insert | merge | supersede_old | mark_uncertain", "reason": "..."}
"""

