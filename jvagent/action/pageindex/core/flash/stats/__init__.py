"""Page-level and document-level layout statistics. The statistics layer computes weighted percentiles, dominant styles, script
families, page spacing measures, and document-wide recurrence signals used by
classification and outline assembly.
"""

import functools
import json
import math
from pathlib import Path
from typing import Optional

from ..model import (
    Line,
    Span,
    _format_half_up_one_decimal,
    _max_nan_propagating,
    info_weight,
)
from .aggregates import (
    DocStats,
    PageStats,
    _percentile_sample_cmp,
    column_index_of,
    compute_doc_stats,
    compute_page_stats,
    style_key,
    weighted_percentile,
)
from .scripts import (
    _SCRIPT_BUCKET_TABLE_PATH,
    SCRIPT_BUCKET_TABLE,
    SCRIPT_FAMILY_WEIGHTS,
    ScriptHistogram,
    char_script_bucket,
    dominant_script_family,
    tally_scripts,
)

__all__ = [
    "weighted_percentile",
    "style_key",
    "PageStats",
    "compute_page_stats",
    "DocStats",
    "compute_doc_stats",
    "column_index_of",
    "char_script_bucket",
    "tally_scripts",
    "dominant_script_family",
    "ScriptHistogram",
    "SCRIPT_FAMILY_WEIGHTS",
    "SCRIPT_BUCKET_TABLE",
]
