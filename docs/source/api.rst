Python API reference
====================

This reference is generated from the Python source tree by Doxygen and
rendered by Breathe. It documents the implementation API; the user-facing
workflow is described in the autoresearch documentation.

The implementation is grouped into ``application`` services, ``orchestration``
workflows, and ``interfaces`` (CLI, TUI, and reports). The original flat module
paths remain compatibility façades; new integrations should use the subpackages.
Infrastructure contracts are defined in ``hiraku.application.ports`` and concrete
SQLite/Git/process implementations live below the infrastructure boundary.

.. doxygenindex::
   :project: hiraku
