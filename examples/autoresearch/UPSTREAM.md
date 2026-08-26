# Upstream provenance

This example adapts Andrej Karpathy's [`karpathy/autoresearch`](https://github.com/karpathy/autoresearch) at commit [`228791fb499afffb54b46200aca536f79142f117`](https://github.com/karpathy/autoresearch/commit/228791fb499afffb54b46200aca536f79142f117), resolved from upstream `HEAD` on 2026-08-26.

The vendored `prepare.py` and `train.py` files are copied from that revision. The Hiraku program closely adapts `program.md` from the same revision, preserving its structure and autonomous loop while mapping run tags, candidate commits, and result rows to Hiraku tasks, experiments, and runs and adding the user-selected `revert`, `reset`, and `branch` modes. The task template, launch configuration, tracking wrapper, and CPU contract mock are adaptations maintained by this project. Upstream declares the project MIT licensed; the license is reproduced in `LICENSE` with attribution.
