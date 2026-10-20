"""Trajectory analysis: rebuild builds from their traces, measure the process, classify failures.

Reads run directories only (trace.jsonl + result.json); used by eval/analyze.py and the Reflector.
"""

from blendharness.analysis.metrics import aggregate, repair_table, run_metrics, spatial_share
from blendharness.analysis.taxonomy import CATEGORIES, NON_SPATIAL, SPATIAL, IssueClassifier, keyword_category
from blendharness.analysis.trajectory import CoderRun, Issue, Trajectory, find_runs, load_trajectory

__all__ = ["CATEGORIES", "CoderRun", "Issue", "IssueClassifier", "NON_SPATIAL", "SPATIAL", "Trajectory", "aggregate",
           "find_runs", "keyword_category", "load_trajectory", "repair_table", "run_metrics", "spatial_share"]
