Hiraku documentation
====================

Hiraku is a goal-oriented toolkit for guided and bounded-autonomous data
science research.

The core lifecycle is:

.. code-block:: text

   Goal → Experiment → Run → Analysis

SQLite is the authoritative local lifecycle store. Git provides source
provenance, while the CLI, TUI, and generated reports are views over the same
core state.

Getting started
---------------

Initialize a repository with ``hiraku init``, define a Goal, create an
Experiment, and launch its declared entrypoint. The repository-local agent
skill is installed under ``.agents/skills/hiraku/`` and supports both guided
and autonomous workflows.

.. toctree::
   :maxdepth: 2
   :caption: Reference

   api
   autoresearch/README
   autoresearch/cli
   autoresearch/data-model
   autoresearch/environment
   autoresearch/research-loop
   autoresearch/agent-protocol
   autoresearch/agent-workflows
   autoresearch/hypothesis-discovery
   autoresearch/dashboard
   autoresearch/relation-to-other-branches

.. toctree::
   :maxdepth: 2
   :caption: Agent workflows

   autoresearch/agent-workflows/auto-loop
   autoresearch/agent-workflows/create-child
   autoresearch/agent-workflows/discover-hypotheses
   autoresearch/agent-workflows/triage-failure

Autoresearch guide
------------------

The workflow guide is organized above by lifecycle, CLI, data model,
environment, agent protocol, and autonomous workflow.
