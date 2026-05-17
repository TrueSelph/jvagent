"""Regression test for App.update_mode persistence after ``object.__setattr__``.

AUDIT-core C-3 raised a concern that ``set_app_update_mode``'s use of
``object.__setattr__`` would bypass jvspatial's dirty tracking and silently
no-op the subsequent ``save()``. Empirically this is NOT the case because
``Object.save()`` calls ``context.save()`` which exports the FULL document
via ``model_dump()``; ``object.__setattr__`` correctly mutates the Pydantic
field and ``model_dump()`` sees the new value.

This test locks that behaviour in so future jvspatial changes can't
silently regress it.
"""

from jvagent.core.app import App


def test_object_setattr_on_update_mode_visible_in_model_dump():
    app = App()
    assert app.update_mode == "run"
    object.__setattr__(app, "update_mode", "merge")
    assert app.update_mode == "merge"
    dumped = app.model_dump()
    assert dumped.get("update_mode") == "merge", dumped


def test_object_setattr_then_setattr_again_round_trips():
    app = App()
    object.__setattr__(app, "update_mode", "source")
    assert app.update_mode == "source"
    object.__setattr__(app, "update_mode", "run")
    assert app.update_mode == "run"
    assert app.model_dump()["update_mode"] == "run"
