# HR-05 — prepared Flagship adapter changes (pending sandbox write access)

The outer Flagship repo `/Users/daniel/git/flaghsip-hariku/tp-agents` is outside
this workspace's write scope. This file records the exact changes to apply there
once the write grant takes effect (the user approved it; a session reload is
needed). The outer repo's seven existing registry tests pass in a scratch venv:

```bash
cd /Users/daniel/git/flaghsip-hariku/tp-agents
PYTHONPATH=$PWD /tmp/tp-agents-test/bin/python -m pytest tests/test_registry_manifest.py -q
# 7 passed
```

Scratch env: `uv venv /tmp/tp-agents-test && uv pip install --python
/tmp/tp-agents-test/bin/python smolagents "openai>=1.0" "tomli>=2.0" pytest -e
/Users/daniel/git/flaghsip-hariku/hiraku/worktrees/registry`.

## 1. `src/registry_manifest.py` — append

```python
def _basename(path: str) -> str:
    return PurePosixPath(path).name


def legacy_manifest_to_generic(manifest: dict[str, Any]) -> dict[str, Any]:
    """Map a legacy build_manifest result to the generic registration manifest."""
    source = "flagship-cohort"
    cohort_id = manifest["cohort_id"]
    sop_info = {sop["id"]: sop for sop in manifest.get("sops", [])}
    datasets = []
    for legacy in manifest["datasets"]:
        registry = legacy["registry"]
        identity_inputs = registry["identity_inputs"]
        key = (
            identity_inputs[1:]
            if identity_inputs and identity_inputs[0] == cohort_id
            else identity_inputs
        )
        artifacts = []
        for row in legacy["files"]:
            artifacts.append({
                "key": row["artifact_id"],
                "display_path": row["path"],
                "revision": row["revision"],
                "size": row["size_bytes"],
                "availability": "present" if row["state"] == "file" else "unknown",
                "documentation": [
                    {
                        "key": match["sop_id"],
                        "role": "unknown",
                        "applicability": "dataset",
                        "filename": _basename(match["expected_filename"]),
                        "version_state": sop_info.get(match["sop_id"], {}).get("version_state"),
                    }
                    for match in row["matches"]
                ],
            })
        documentation = [
            {
                "key": item["sop_id"],
                "role": "unknown",
                "applicability": "dataset",
                "filename": _basename(item["expected_filename"]),
                "expected_availability": item.get("availability"),
                "version_state": sop_info.get(item["sop_id"], {}).get("version_state"),
            }
            for item in legacy["missing_expected"]
        ]
        datasets.append({
            "namespace": cohort_id,
            "key": key,
            "parent": [legacy["technology"]],
            "metadata": {"technology": legacy["technology"], "sample_key": legacy["sample_key"]},
            "coverage": "exhaustive" if registry.get("scan_complete") else "partial",
            "availability": None,
            "artifacts": artifacts,
            "documentation": documentation,
        })
    return {
        "schema_version": 1,
        "source": source,
        "scope": {
            "coverage": "exhaustive" if manifest.get("complete") else "partial",
            "filters": manifest.get("scope", {}),
        },
        "datasets": datasets,
        "coverage": manifest.get("coverage"),
        "sop_catalog_complete": manifest.get("sop_catalog_complete"),
        "complete": manifest.get("complete"),
        "counts": manifest.get("counts"),
    }


def build_registration_manifest(settings, backend, *, technology, sample=None,
                                all_samples=False, all_sop_versions=False,
                                max_samples=10000, max_files=100000,
                                source_config=None) -> dict[str, Any]:
    """Build the generic registration manifest for the selected cohort scope."""
    legacy = build_manifest(settings, backend, technology=technology, sample=sample,
                            all_samples=all_samples, all_sop_versions=all_sop_versions,
                            max_samples=max_samples, max_files=max_files,
                            source_config=source_config)
    generic = legacy_manifest_to_generic(legacy)
    try:
        from hiraku.registration import validate_manifest
    except ImportError as exc:
        raise RuntimeError(
            "Make Hiraku's registration package importable to build a generic "
            "manifest; install hiraku or set HIRAKU_PYTHONPATH to the directory "
            "containing its 'hiraku' package"
        ) from exc
    generic["content_sha256"] = validate_manifest(generic).compute_digest()
    return generic
```

## 2. `src/registry_writer.py` — replace `_hiraku_api` source-path fallback and append

Replace the `except ImportError` branch of `_hiraku_api()` with a single clear
error (remove the `Path(__file__).resolve().parents[2] / "hiraku" / "src"`
fallback), then append:

```python
def apply_registration_manifest(manifest, project, *, override_conflicts=False,
                                dry_run=True, reviewed_hash=None) -> dict[str, Any]:
    """Apply a generic registration manifest through Hiraku's public API."""
    from hiraku.config import HirakuConfig
    from hiraku.registration import apply_import, plan_import, validate_manifest

    config_path = Path(project).expanduser().resolve()
    if config_path.is_dir():
        config_path /= "hiraku.yml"
    if not config_path.is_file():
        raise ValueError(f"Hiraku project config not found: {config_path}")
    config = HirakuConfig.load(config_path)
    model = validate_manifest(manifest, check_digest=True)
    if dry_run:
        plan = plan_import(model, config, override=override_conflicts)
    else:
        plan = apply_import(model, config, reviewed_hash=reviewed_hash,
                            override=override_conflicts)
    changed_ids = [*plan.creates, *plan.updates]
    conflicts = [{"id": c.record_id, "message": c.message} for c in plan.conflicts]
    result = {"changed_ids": changed_ids, "conflicts": conflicts, "dry_run": dry_run}
    if not manifest.get("complete", True):
        result["write_blocked"] = "manifest is incomplete"
    return result
```

## 3. `src/registry_cli.py` — use the generic path

In `main`, replace `build_manifest`/`apply_manifest` with
`build_registration_manifest`/`apply_registration_manifest` (same flags and
reviewed-hash guard; the `content_sha256` check now compares against the
generic manifest's digest).

## 4. `docs/registry-import.md` — update coverage notes

Document local adapter coverage as `derived/pass_*` only, that an MCP descriptor
import does not require local cohort mounts, and that SOP role/applicability
are reported as unknown.

## 5. New `tests/test_registry_generic.py`

- generic manifest build is deterministic and maps cohorts/artifacts/SOPs.
- generic API-backed write via `apply_registration_manifest` (Hiraku's
  `apply_import`) creates datasets and preserves human text.
- legacy reimport via `hiraku.registration.compat` does not duplicate datasets.
- role/applicability stay `unknown`; pagination bounds and all-sample scope.
- synthetic fixture reproduces the cohort directory shape.
