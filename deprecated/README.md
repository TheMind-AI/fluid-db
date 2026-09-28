# Archived implementations

TypeScript is the maintained project at the repository root. `python/` preserves the 2023–24 prototype and the
2026 research lab together, including their original module names, datasets, reports and results.

- [Prototype](python/README.md): `fluiddb/`, `eval/`, `experiments/`, `data/`, `tests/`, `main.py`.
- [Research commands](python/lab/README.md) and [findings](python/lab/REPORT.md): `lab/`.
- [Design and research record](../llp/0000-fluiddb.explainer.md): still maintained at the repository root.

Run historical commands from `deprecated/python/` so imports such as `lab.memory` and relative data paths resolve.
Create an environment there using the lab's setup instructions. An existing root `.venv` can also be used:

```sh
cd deprecated/python
LAB_CACHE_ONLY=1 ../../.venv/bin/python -m lab.report
```

The lab still reads keys from the repository-root `.env`. Caches, downloaded benchmarks, local databases and private
data keep their ignore rules. Re-analysis must use `LAB_CACHE_ONLY=1`; cache misses fail instead of spending.
Existing `LAB_PRIVATE_DIR` paths remain external and unchanged.

Historical accuracy numbers describe the Python systems. Evaluate the current implementation using
[`bun run eval`](../evals/README.md) from the root. Archived code is excluded from TypeScript builds, CI tests and
formatting; changes here should be limited to preserving reproducibility.
