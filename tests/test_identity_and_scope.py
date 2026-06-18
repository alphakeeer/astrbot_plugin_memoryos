from types import SimpleNamespace

from core.config import MemoryOSConfig
from core.identity import IdentityResolver
from core.models import (
    SCOPE_GROUP_SHARED,
    SCOPE_SESSION,
    SCOPE_USER_IN_GROUP,
    SCOPE_USER_PRIVATE,
    allowed_scope_rules,
)


def _event(group_id=""):
    sender = SimpleNamespace(user_id="u1", nickname="Alice")
    msg = SimpleNamespace(
        self_id="bot1",
        session_id="s1",
        message_id="m1",
        group_id=group_id,
        sender=sender,
        message_str="hello",
        timestamp=1_700_000_000,
    )
    return SimpleNamespace(
        message_obj=msg,
        unified_msg_origin="umo1",
        get_sender_name=lambda: "Alice",
        get_platform_name=lambda: "aiocqhttp",
    )


def test_identity_private_keys():
    identity = IdentityResolver().resolve(_event())

    assert identity.platform_id == "aiocqhttp"
    assert identity.user_id == "u1"
    assert identity.is_group is False
    assert identity.private_space == "aiocqhttp:private:u1"
    assert identity.session_key == "aiocqhttp:session:s1"


def test_private_allowed_scopes_do_not_include_group_by_default():
    identity = IdentityResolver().resolve(_event())
    rules = allowed_scope_rules(identity, MemoryOSConfig())
    scopes = {rule.scope for rule in rules}

    assert SCOPE_USER_PRIVATE in scopes
    assert SCOPE_SESSION in scopes
    assert SCOPE_GROUP_SHARED not in scopes


def test_group_allowed_scopes_do_not_include_private_by_default():
    identity = IdentityResolver().resolve(_event("g1"))
    rules = allowed_scope_rules(identity, MemoryOSConfig())
    pairs = {(rule.scope, rule.owner_key) for rule in rules}

    assert (SCOPE_GROUP_SHARED, "aiocqhttp:group:g1") in pairs
    assert (SCOPE_USER_IN_GROUP, "aiocqhttp:group:g1:user:u1") in pairs
    assert (SCOPE_USER_PRIVATE, "aiocqhttp:private:u1") not in pairs

