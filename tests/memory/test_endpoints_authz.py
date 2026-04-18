"""Tests for memory endpoint auth-related behavior."""

from fastapi.params import Query as QueryParam

from jvagent.memory.endpoints import get_my_memory


def test_get_my_memory_user_id_is_not_query_param_default():
    """Ensure user_id is not declared as a client-controlled query default."""
    default_value = get_my_memory.__defaults__[0]
    assert default_value is None
    assert not isinstance(default_value, QueryParam)
