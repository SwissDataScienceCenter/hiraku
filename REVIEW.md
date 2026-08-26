
- heart disease example should use to distinct entrypoints for logistic regression and random forest, rather than a single entrypoint with a `--model` argument. 
- `hiraku entrypoint list` or maybe `hiraku task entrypoints` for listing entrypoints, an option would be also to make `hiraku task show list entrypoints`, and we should probably only print metadata, not the markdown description unless requested (--verbose). 
- make `hiraku task` a shortcut for `hiraku task show` for the current active task.
- include hypothesis in experiment list from `hiraku experiment list`
- add `hiraku exp` as a shortcut for `hiraku experiment`
- add timestamps for runs (recorded by the tracking backend) and experiments (`created_at:` and `updated_at:`) to the experiment yml / markdown.

