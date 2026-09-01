
### Running the examples

Each example is a copyable project. Copy it outside the Hiraku checkout before initializing it;
do not create nested Git repositories inside `examples/`:

```bash
cp -R examples/heart-disease ../hiraku-heart-disease
cd ../hiraku-heart-disease
git init -b main
hiraku init
git add .
git commit -m "initialize Heart Disease example"
```

Entrypoints and paths in an example are relative to the copied repository root. Each example
declares its own dependencies and its README continues the task-specific workflow. Automated
tests follow the same copy-and-`git init` process in temporary repositories.

The smoke and heart-disease examples select their run writer from
`HIRAKU_TRACKING_BACKEND`. Their launch wrapper uses the table dependencies by default and adds
the optional MLflow dependency automatically for an MLflow launch, while the writer uses only
the backend-specific tracking location injected by Hiraku.

The interactive [`examples/heart-disease`](examples/heart-disease) flow is the main tutorial example. The same task can use the `autoresearch` program for a bounded CPU-only autonomous loop.
[`examples/smoke`](examples/smoke) is only the shortest installation and tracking-contract check.

Stage 2 also includes [`examples/autoresearch`](examples/autoresearch), pinned to upstream commit `228791fb499afffb54b46200aca536f79142f117`. It vendors the upstream GPU training and preparation code with MIT attribution, closely adapts the original autonomous program to Hiraku tasks, experiments, and runs, lets the user choose `revert`, `reset`, or retained-experiment-`branch` history, and provides a CPU-only contract entrypoint for automated workflow validation.
