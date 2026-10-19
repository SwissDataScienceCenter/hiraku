---
id: autodataset
---
# Autodataset

## Instructions

Find `count` datasets for the task in the context. Datasets listed in
`already_found` are documented already; do not duplicate them.

1. Read the task record (`record_show`) and its existing child records to
   understand the goal, the data the task needs, and constraints. Ask the user
   to clarify domain requirements if they are unclear.
2. Search for candidate datasets. For each candidate, assess:
   - **relevance** to the task's goal and evaluation;
   - **access**: where and how to obtain it, and any registration or fees;
   - **licensing**: the license and whether it permits the intended use;
   - **quality**: size, provenance, known issues, and maintenance.
3. Document each suitable dataset with `record_create`: parent is the task,
   `type: dataset`, a `summary`, a `sources` list with the URLs or references
   you relied on, and fields for `access`, `license`, and `quality`. Put the
   detailed assessment and open uncertainties in the Markdown body.
4. Do not download, preprocess, or validate the data itself.

Then reply:

- `complete` with all dataset record IDs if you documented `count` datasets in
  total, including `already_found`; or
- `report_shortfall` with the records you have and the reason, if you could not
  find enough suitable datasets. Never pad the result with unsuitable datasets.

## Shortfall

Fewer suitable datasets were found than requested. Explain the shortfall to the
user using the context, and ask whether to keep searching (`continue_search`)
or to accept the partial result (`accept_partial`).

## Done

Give the user a short comparison of the datasets in the context: why each is
relevant, how to obtain it, and what remains uncertain. Say that Hiraku checked
only the records' structure and recorded sources, not licensing or
suitability. If the result is partial, explain the shortfall.
