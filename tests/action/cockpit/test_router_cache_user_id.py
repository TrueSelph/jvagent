"""Router cache key must vary by user_id (AUDIT-interact-cockpit HIGH-04).

Without user_id in the key, two users sharing the same conversation_id
would receive each other's cached routing decision (including the
``interpretation`` text, which is per-user).
"""

from jvagent.core.cache import cache_manager


def test_router_cache_key_varies_by_user_id():
    base_kwargs = dict(
        conversation_id="conv_x",
        utterance="hello",
        last_interaction_ids=(),
        buffer_fingerprint="",
        active_task_fingerprint="",
        proactive_tasks_fingerprint="",
    )
    k_a = cache_manager.router_cache_key(**base_kwargs, user_id="user_a")
    k_b = cache_manager.router_cache_key(**base_kwargs, user_id="user_b")
    assert k_a != k_b


def test_router_cache_key_stable_with_same_inputs():
    base_kwargs = dict(
        conversation_id="conv_x",
        utterance="hello",
        last_interaction_ids=(),
        buffer_fingerprint="",
        active_task_fingerprint="",
        proactive_tasks_fingerprint="",
        user_id="user_a",
    )
    k1 = cache_manager.router_cache_key(**base_kwargs)
    k2 = cache_manager.router_cache_key(**base_kwargs)
    assert k1 == k2
