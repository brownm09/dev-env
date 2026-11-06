"""Calibrate the vector instrument on a repo outside the corpus, before any arm is scored.

Usage: <venv-python> calibrate_vector.py <issue-cache-dir>

Known good: each issue's own text must rank itself first. Known bad: a string unrelated to the repo
must score below every issue's self-match, by a clear margin. Prints numbers only.
"""
import json
import sys

import numpy as np
from fastembed import TextEmbedding

from vector_arm import MODEL, doc_text, unit

UNRELATED = "Sourdough hydration ratios for a high altitude bakery in late autumn"


def main(cache_dir):
    with open(f"{cache_dir}/issues_win11-init-tools.json", encoding="utf-8") as f:
        issues = json.load(f)
    model = TextEmbedding(MODEL)
    vectors = unit(list(model.embed([doc_text(i) for i in issues])))
    sims = vectors @ vectors.T
    self_first = int(sum(int(np.argmax(sims[i])) == i for i in range(len(issues))))
    unrelated = unit(list(model.embed([UNRELATED])))[0]
    best_unrelated = float((vectors @ unrelated).max())
    print(f"docs={len(issues)} self_ranked_first={self_first} best_unrelated_cosine={best_unrelated:.3f}")
    if self_first != len(issues) or best_unrelated >= 0.7:
        raise SystemExit("vector calibration failed")


if __name__ == "__main__":
    main(sys.argv[1])
