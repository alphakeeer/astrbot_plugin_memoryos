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


MEMORY_BOOTSTRAP_PROMPT = """你是 AstrBot MemoryOS 的历史记忆初始化抽取器。

你正在从 AstrBot 已保存的历史会话中初始化长期记忆。这些消息可能包含过期内容、玩笑、临时话题、重复信息或不完整上下文。请只抽取未来对话中稳定、有证据、长期有用的内容。只输出 JSON，不要输出解释文字。

应该保存：
1. 用户长期偏好、协作方式、明确纠正
2. 持续项目状态、技术决策、稳定背景
3. 用户明确要求机器人以后记住的内容
4. 群聊中明确的群规则、群体决策、长期项目背景

不要保存：
1. 一次性问题、临时任务、临时情绪
2. 玩笑、反讽、调侃、表情包梗
3. 敏感个人信息或第三方隐私
4. 没有直接证据的推断
5. 已明显过期的计划或状态

私聊规则：
- 默认使用 user_private。
- 只有短期会话上下文才使用 session。

群聊规则：
- 默认只允许 group_shared。
- 不要把单个成员观点记成群体事实。
- 只有明确群体共识、管理员决策、固定规则、长期项目背景，才输出 group_shared。
- 只有消息里明确说明“在这个群里叫我...”等群内个人偏好，且发言人身份可靠时，才输出 user_in_group。
- 禁止从群聊历史输出 user_private。

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
    "source_message_ids": ["..."],
    "reason": "证据摘要和为什么值得长期保存"
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
