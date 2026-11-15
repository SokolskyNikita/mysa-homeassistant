"""The climate entity provides the temperature names Home Assistant reads."""

import logging

from custom_components.mysa_local.climate import MysaClimate


def test_no_deprecated_climate_names_reported(caplog):
    """From 2026.11, providing only a pre-native_ name logs at class creation.

    MysaClimate keeps both names so 2026.10 works; on 2026.11 the native_
    ones must win, so a subclass inherits them without a deprecation warning.
    Before 2026.11 there is nothing to report and this passes trivially.
    """
    with caplog.at_level(logging.WARNING):

        class Subclass(MysaClimate):
            pass

    assert not [r for r in caplog.records if "deprecated" in r.getMessage()]
