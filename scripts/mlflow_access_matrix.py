"""Probe MLflow access side effects using disposable databases and environments.

Run with Python 3.11+ and uv installed. Downloads dependencies; never opens a user
database. See scripts/mlflow_access_matrix.md for usage and interpretation.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import shutil
import signal
import socket
import sqlite3
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def snapshot(directory: Path) -> dict:
    """Inspect SQLite without creating it, and fingerprint surrounding files."""
    database = directory / "mlflow.db"
    result = {"exists": database.is_file(), "files": {}}
    for path in sorted(directory.rglob("*")):
        if path.is_file():
            result["files"][str(path.relative_to(directory))] = hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
    if not database.is_file():
        return result
    try:
        with sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True) as connection:
            result["schema"] = connection.execute(
                "SELECT type, name, tbl_name, sql FROM sqlite_master "
                "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
            ).fetchall()
            tables = connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            ).fetchall()
            result["rows"] = {}
            for (table,) in tables:
                quoted = '"' + table.replace('"', '""') + '"'
                rows = connection.execute(f"SELECT * FROM {quoted}").fetchall()
                result["rows"][table] = hashlib.sha256(
                    repr(sorted(rows, key=repr)).encode()
                ).hexdigest()
            if ("alembic_version",) in tables:
                result["revision"] = connection.execute(
                    "SELECT version_num FROM alembic_version ORDER BY version_num"
                ).fetchall()
    except sqlite3.Error as exc:
        result["error"] = str(exc)
    return result


def changes(before: dict, after: dict) -> dict:
    return {
        "created": not before["exists"] and after["exists"],
        "schema_changed": before.get("schema") != after.get("schema"),
        "revision_changed": before.get("revision") != after.get("revision"),
        "rows_changed": before.get("rows") != after.get("rows"),
        "files_changed": sorted(
            name
            for name in before["files"].keys() | after["files"].keys()
            if before["files"].get(name) != after["files"].get(name)
        ),
    }


def worker(action: str, directory: Path, destination: str) -> None:
    """Run inside one isolated MLflow environment."""
    if action == "seed":
        import mlflow

        mlflow.set_tracking_uri(f"sqlite:///{directory / 'mlflow.db'}")
        mlflow.set_experiment("matrix")
        with mlflow.start_run():
            mlflow.set_tag("hiraku.record_id", "baseline")
            mlflow.log_metric("score", 0.5)
        return

    from hiraku.project import Project
    from hiraku.tracking.mlflow import MLflowBackend

    backend = MLflowBackend(
        Project(directory), experiment_name="matrix", tracking_uri=destination
    )
    runs = backend.list_runs()
    print(json.dumps({"runs": len(runs), "ids": [run.id for run in runs]}))


def execute(command: list[str], cwd: Path, log: Path, timeout: int = 300) -> dict:
    with log.open("w") as output:
        try:
            completed = subprocess.run(
                command,
                cwd=cwd,
                stdout=output,
                stderr=subprocess.STDOUT,
                timeout=timeout,
                check=False,
            )
            return {"exit_code": completed.returncode, "log": str(log)}
        except subprocess.TimeoutExpired:
            return {"exit_code": None, "error": "timeout", "log": str(log)}


def free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def probe(user_python: Path, client_python: Path, directory: Path, logs: Path) -> dict:
    before = snapshot(directory)
    server = None
    result = {"before": before}
    try:
        port = free_port()
        destination = f"http://127.0.0.1:{port}"
        with (logs / "server.log").open("w") as output:
            server = subprocess.Popen(
                [
                    str(user_python),
                    "-m",
                    "mlflow",
                    "server",
                    "--backend-store-uri",
                    f"sqlite:///{directory / 'mlflow.db'}",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(port),
                    "--workers",
                    "1",
                ],
                cwd=directory,
                stdout=output,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            if server.poll() is not None:
                result["server_error"] = f"exited {server.returncode}"
                break
            try:
                with urllib.request.urlopen(
                    destination + "/health", timeout=1
                ) as response:
                    if response.status == 200:
                        break
            except (urllib.error.URLError, TimeoutError):
                time.sleep(0.2)
        else:
            result["server_error"] = "startup timeout"
        result["after_startup"] = snapshot(directory)
        result["startup_changes"] = changes(before, result["after_startup"])
        if "server_error" not in result:
            query_before = snapshot(directory)
            result["query"] = execute(
                [
                    str(client_python),
                    str(Path(__file__).resolve()),
                    "--worker",
                    "query",
                    str(directory),
                    destination,
                ],
                directory,
                logs / "query.log",
            )
            result["after_query"] = snapshot(directory)
            result["query_changes"] = changes(query_before, result["after_query"])
    finally:
        if server is not None and server.poll() is None:
            os.killpg(server.pid, signal.SIGTERM)
            try:
                server.wait(timeout=15)
            except subprocess.TimeoutExpired:
                os.killpg(server.pid, signal.SIGKILL)
                server.wait(timeout=15)
    result["after"] = snapshot(directory)
    result["total_changes"] = changes(before, result["after"])
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--versions",
        nargs="+",
        default=["2.17.2", "2.22.0", "3.0.0", "3.10.0", "3.16.0"],
    )
    parser.add_argument("--python", default="3.11")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--jobs", type=int, default=3)
    parser.add_argument("--worker", nargs=3, metavar=("ACTION", "DIRECTORY", "URI"))
    args = parser.parse_args()
    if args.worker:
        worker(args.worker[0], Path(args.worker[1]), args.worker[2])
        return
    if args.output is None:
        parser.error("--output is required (must be a new directory)")
    if args.jobs < 1:
        parser.error("--jobs must be positive")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=args.resume)
    # Explicit tracking settings and clean subprocess state prevent accidental
    # access to a configured remote service or the caller's database.
    for name in list(os.environ):
        if name.startswith("MLFLOW_"):
            del os.environ[name]
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    server_pythons = {}
    client_pythons = {}
    for version in args.versions:
        for role, package, pythons in (
            ("server", "mlflow", server_pythons),
            ("client", "mlflow-skinny", client_pythons),
        ):
            environment = output / "envs" / role / version
            python = environment / "bin/python"
            if args.resume and python.is_file():
                pythons[version] = python
                continue
            setup = execute(
                ["uv", "venv", "--python", args.python, str(environment)],
                output,
                output / f"venv-{role}-{version}.log",
            )
            if setup["exit_code"] == 0:
                setup = execute(
                    [
                        "uv",
                        "pip",
                        "install",
                        "--python",
                        str(python),
                        f"{package}=={version}",
                        *(["sqlalchemy<2.1"] if role == "server" else []),
                        "--editable",
                        str(ROOT),
                    ],
                    output,
                    output / f"install-{role}-{version}.log",
                    timeout=900,
                )
            if setup["exit_code"] != 0:
                raise RuntimeError(f"Environment setup failed: {setup}")
            pythons[version] = python
    results_path = output / "results.json"
    results = (
        json.loads(results_path.read_text())
        if args.resume and results_path.is_file()
        else []
    )
    finished = {(r["user"], r["client"], r["scenario"]) for r in results}
    for user_version, user_python in server_pythons.items():
        seed = output / "seeds" / user_version
        if args.resume and (seed / "mlflow.db").is_file():
            continue
        seed.mkdir(parents=True, exist_ok=args.resume)
        seeded = execute(
            [
                str(user_python),
                str(Path(__file__).resolve()),
                "--worker",
                "seed",
                str(seed),
                "unused",
            ],
            seed,
            output / f"seed-{user_version}.log",
        )
        if seeded["exit_code"] != 0:
            raise RuntimeError(f"Seed failed: {seeded}")

    def run_case(user_version, client_version, scenario):
        name = f"{user_version}__{client_version}__{scenario}__server"
        case = output / "cases" / name
        if case.exists() and args.resume:
            shutil.rmtree(case)
        directory = case / "work"
        directory.mkdir(parents=True)
        if scenario == "existing":
            seed = output / "seeds" / user_version
            shutil.copy2(seed / "mlflow.db", directory / "mlflow.db")
        elif scenario == "empty":
            (directory / "mlflow.db").touch()
        print(name, flush=True)
        result = probe(
            server_pythons[user_version],
            client_pythons[client_version],
            directory,
            case,
        )
        result.update(
            user=user_version, client=client_version, scenario=scenario, mode="server"
        )
        return result

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as executor:
        pending = []
        for user_version in server_pythons:
            for client_version in client_pythons:
                for scenario in ("missing", "empty", "existing"):
                    key = (user_version, client_version, scenario)
                    if key not in finished:
                        pending.append(executor.submit(run_case, *key))
        for future in concurrent.futures.as_completed(pending):
            results.append(future.result())
            temporary = results_path.with_suffix(".tmp")
            temporary.write_text(json.dumps(results, indent=2) + "\n")
            temporary.replace(results_path)
    results.sort(key=lambda r: (r["user"], r["client"], r["scenario"], r["mode"]))
    lines = [
        "| User | Hiraku SDK | DB | Access | Query | Created "
        "| Schema | Revision | Rows | Directories |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for result in results:
        name = (
            f"{result['user']}__{result['client']}__"
            f"{result['scenario']}__{result['mode']}"
        )
        directory = output / "cases" / name / "work"
        # Case preparation creates no subdirectories. Inventory after all servers
        # have stopped also captures empty directories, which file hashes miss.
        result["directories_after"] = [
            str(path.relative_to(directory))
            for path in sorted(directory.rglob("*"))
            if path.is_dir()
        ]
        delta = result["total_changes"]
        status = "OK" if result.get("query", {}).get("exit_code") == 0 else "ERROR"
        values = [
            result["user"],
            result["client"],
            result["scenario"],
            result["mode"],
            status,
        ]
        values.extend(
            str(delta[key])
            for key in ("created", "schema_changed", "revision_changed", "rows_changed")
        )
        values.append(", ".join(result["directories_after"]) or "—")
        lines.append("| " + " | ".join(values) + " |")
    results_path.write_text(json.dumps(results, indent=2) + "\n")
    (output / "report.md").write_text("\n".join(lines) + "\n")
    print(f"Results: {output / 'report.md'}", flush=True)


if __name__ == "__main__":
    main()
