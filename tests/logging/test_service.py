"""INTERACTION custom log level registration."""

import logging

from jvagent.logging.service import INTERACTION_LEVEL_NUMBER


def test_interaction_level_registered_between_info_and_warning():
    assert logging.INFO < INTERACTION_LEVEL_NUMBER < logging.WARNING
    assert logging.getLevelName(INTERACTION_LEVEL_NUMBER) == "INTERACTION"


def test_logger_exposes_interaction_method():
    logger = logging.getLogger("test_interaction_level")
    assert callable(getattr(logger, "interaction", None))
