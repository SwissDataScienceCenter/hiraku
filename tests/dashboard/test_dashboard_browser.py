"""Opt-in real-browser checks; see docs/dashboard.md for setup."""

from __future__ import annotations

import os
import re
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

import pytest
from fixtures import create_acceptance_project, create_launch_project
from selenium import webdriver
from selenium.common.exceptions import StaleElementReferenceException
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import Select, WebDriverWait

from hiraku.config import HirakuConfig
from hiraku.record import load_record

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.environ.get("HIRAKU_TEST_BROWSER"),
        reason="Set HIRAKU_TEST_BROWSER=webkit or chrome to run browser acceptance",
    ),
]


def _elements(browser, selector):
    # Marimo controls use open shadow roots. Traverse DOM, not runtime internals.
    return browser.execute_script(
        """
        const found = [];
        function visit(root) {
            found.push(...root.querySelectorAll(argumentsSelector));
            for (const node of root.querySelectorAll('*')) {
                if (node.shadowRoot) visit(node.shadowRoot);
            }
        }
        const argumentsSelector = arguments[0];
        visit(document);
        return found;
        """,
        selector,
    )


def _control(browser, host, selector):
    return WebDriverWait(browser, 20).until(
        lambda b: next(
            (
                control
                for element in _elements(b, host)
                if (
                    control := b.execute_script(
                        "return arguments[0].shadowRoot?.querySelector(arguments[1])",
                        element,
                        selector,
                    )
                )
            ),
            None,
        )
    )


def _body(browser):
    return browser.find_element(By.TAG_NAME, "body").text


def _rendered_canvas(browser):
    WebDriverWait(
        browser, 20, ignored_exceptions=(StaleElementReferenceException,)
    ).until(
        lambda b: b.execute_script(
            """
            const c = arguments[0];
            c.scrollIntoView();
            if (c.width < 350 || c.height < 300) return false;
            const pixels = c.getContext('2d').getImageData(
                0, 0, c.width, c.height).data;
            let n = 0;
            for (let i = 0; i < pixels.length; i += 4) {
                if (pixels[i+2] > pixels[i] + 20
                    && pixels[i+2] > pixels[i+1] + 15
                    && pixels[i+3] > 50) n++;
            }
            return n > 50;
            """,
            _control(b, "marimo-vega", "canvas"),
        )
    )
    return _control(browser, "marimo-vega", "canvas")


def _select_record(browser, identifier):
    _control(browser, 'marimo-dropdown[data-label*="Record"]', "button").click()
    option = WebDriverWait(browser, 20).until(
        lambda b: next(
            (e for e in _elements(b, '[role="option"]') if e.text == identifier),
            None,
        )
    )
    option.click()
    WebDriverWait(browser, 20).until(
        lambda b: f"Record detail{identifier}" in _body(b).replace("\n", "")
    )


def _run_rows(browser):
    return int(
        browser.find_element(
            By.CSS_SELECTOR, 'marimo-table[data-label*="Runs"]'
        ).get_attribute("data-total-rows")
    )


def _files(root: Path):
    return {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def _new_browser(monkeypatch):
    if os.environ["HIRAKU_TEST_BROWSER"] == "webkit":
        # MLflow uses navigator.language with Intl; the C locale is not a BCP-47 tag.
        monkeypatch.setenv("LANG", "en_US.UTF-8")
        monkeypatch.setenv("LC_ALL", "en_US.UTF-8")
        return webdriver.WebKitGTK()
    options = webdriver.ChromeOptions()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    return webdriver.Chrome(options=options)


def test_interactive_browser_create_preview_and_launch(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    config = create_launch_project(project)
    config_before = config.path.read_bytes()
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    with (tmp_path / "server.log").open("w+") as log:
        server = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "hiraku.cli",
                "dashboard",
                "--interactive",
                "--record",
                "child",
                "--port",
                str(port),
                "--no-browser",
            ],
            cwd=project,
            stdout=log,
            stderr=log,
        )
        browser = None
        try:
            deadline = time.monotonic() + 30
            token_url = None
            while time.monotonic() < deadline:
                log.seek(0)
                output = log.read()
                match = re.search(
                    r"http://(?:127\.0\.0\.1|localhost):\d+\S*access_token=\S+",
                    output,
                )
                if match:
                    token_url = match.group(0)
                    break
                assert server.poll() is None, output
                time.sleep(0.1)
            assert token_url is not None, log.read()
            browser = _new_browser(monkeypatch)
            browser.get(token_url)
            WebDriverWait(browser, 30).until(lambda b: "Interactive" in _body(b))
            assert "Create record" in _body(browser)
            assert "Launch run" in _body(browser)
            _control(
                browser, 'marimo-text[data-label*="Record ID"]', "input"
            ).send_keys("gui-child")
            _control(browser, 'marimo-button[data-label*="Preview"]', "button").click()
            WebDriverWait(browser, 20).until(
                lambda b: "Preview: record gui-child" in _body(b)
            )
            created_path = config.records_dir / "task/gui-child.md"
            assert not created_path.exists()
            next(e for e in _elements(browser, "button") if e.text == "Create").click()
            WebDriverWait(browser, 20).until(
                lambda b: "Created record gui-child" in _body(b)
            )
            assert load_record("gui-child", config=config).parent_id == "child"
            _control(
                browser, 'marimo-button[data-label*="Preview launch"]', "button"
            ).click()
            WebDriverWait(browser, 20).until(
                lambda b: "Launch preview:" in _body(b) and "gui-child" in _body(b)
            )
            _control(
                browser, 'marimo-button[data-label*="Launch run"]', "button"
            ).click()
            WebDriverWait(browser, 30).until(
                lambda b: "succeeded" in _body(b) and _run_rows(b) == 1
            )
            assert "gui-child-0" in (project / "runs.csv").read_text()
            assert config.path.read_bytes() == config_before
            assert HirakuConfig.load(config.path).active_record_id is None
        finally:
            if browser is not None:
                (tmp_path / "browser.txt").write_text(_body(browser))
                browser.save_screenshot(str(tmp_path / "interactive.png"))
                browser.quit()
            server.send_signal(signal.SIGINT)
            server.wait(timeout=20)


def test_dashboard_browser_workflow(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    create_acceptance_project(project)
    csv = project / "results/a.csv"
    with csv.open("a") as stream:
        stream.write("shared-b,child-b,2026-01-04T00:00:00Z,0.6,0.4\n")
    before = _files(project)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    with (tmp_path / "server.log").open("w+") as log:
        server = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "hiraku.cli",
                "dashboard",
                "--port",
                str(port),
                "--no-browser",
            ],
            cwd=project,
            stdout=log,
            stderr=log,
        )
        browser = None
        try:
            deadline = time.monotonic() + 30
            while True:
                try:
                    with urlopen(url, timeout=1):
                        break
                except (URLError, TimeoutError):
                    assert server.poll() is None
                    assert time.monotonic() < deadline
                    time.sleep(0.1)
            browser = _new_browser(monkeypatch)
            browser.get(url)
            WebDriverWait(browser, 30).until(
                lambda b: "Select a record to load" in _body(b)
            )
            _select_record(browser, "child-a")
            WebDriverWait(browser, 20).until(lambda b: _run_rows(b) == 2)
            _control(
                browser,
                'marimo-table[data-label*="Runs"]',
                '[data-testid="select-row-checkbox"]',
            ).click()
            WebDriverWait(browser, 20).until(lambda b: "Run: a-1" in _body(b))
            assert "No safe external link" in _body(browser)
            canvas = _rendered_canvas(browser)
            browser.execute_script("arguments[0].scrollIntoView()", canvas)
            point = WebDriverWait(browser, 20).until(
                lambda b: b.execute_script(
                    """
                    const c = arguments[0], rect = c.getBoundingClientRect();
                    const pixels = c.getContext('2d').getImageData(
                        0, 0, c.width, c.height).data;
                    let sumX = 0, sumY = 0, n = 0;
                    for (let y = 0; y < c.height; y++) {
                        for (let x = 0; x < c.width; x++) {
                            const i = (y * c.width + x) * 4;
                            if (pixels[i+2] > pixels[i+1] + 15
                                && pixels[i+1] > pixels[i] + 20 && pixels[i+3] > 50) {
                                sumX += x; sumY += y; n++;
                            }
                        }
                    }
                    return n ? [sumX/n * rect.width/c.width - rect.width/2,
                                sumY/n * rect.height/c.height - rect.height/2] : null;
                    """,
                    canvas,
                )
            )
            ActionChains(browser).move_to_element_with_offset(
                canvas, int(point[0]), int(point[1])
            ).click().perform()
            WebDriverWait(browser, 20).until(
                lambda b: (
                    b.find_elements(By.CSS_SELECTOR, "marimo-table")[-1].get_attribute(
                        "data-total-rows"
                    )
                    == "1"
                )
            )
            assert "a-1" in browser.find_elements(By.CSS_SELECTOR, "marimo-table")[
                -1
            ].get_attribute("data-data")

            # Changing controls must not see new tracker rows until explicit refresh.
            with csv.open("a") as stream:
                stream.write("a-new,child-a,2026-01-06T00:00:00Z,0.95,0.1\n")
            before[csv.relative_to(project)] = csv.read_bytes()
            Select(
                _control(browser, 'marimo-dropdown[data-label*="Sort"]', "select")
            ).select_by_visible_text("metric.acc")
            assert _run_rows(browser) == 2
            _control(
                browser,
                'marimo-radio[data-label*="Run scope"]',
                'button[value="All attributed runs in this source"]',
            ).click()
            WebDriverWait(browser, 20).until(lambda b: _run_rows(b) == 3)
            assert "Run: a-1" in _body(browser)
            _control(
                browser,
                'marimo-radio[data-label*="Plot view"]',
                'button[value="Pareto"]',
            ).click()
            WebDriverWait(browser, 20).until(
                lambda b: (
                    b.find_element(By.CSS_SELECTOR, "marimo-vega")
                    .get_attribute("data-label")
                    .find("Pareto")
                    >= 0
                )
            )
            _control(
                browser,
                'marimo-table[data-label*="Runs"]',
                'tbody tr:last-child [data-testid="select-row-checkbox"]',
            ).click()
            WebDriverWait(browser, 20).until(lambda b: "Run: shared-b" in _body(b))
            _control(browser, 'marimo-button[data-label*="child-b"]', "button").click()
            WebDriverWait(browser, 20).until(lambda b: _run_rows(b) == 1)
            assert "/b.csv" in _body(browser)
            _select_record(browser, "child-a")
            WebDriverWait(browser, 20).until(lambda b: _run_rows(b) == 3)
            _control(browser, "marimo-refresh", "button").click()
            WebDriverWait(browser, 20).until(lambda b: _run_rows(b) == 4)
            assert "STALE" not in _body(browser)
            assert _files(project) == before
        finally:
            if browser is not None:
                (tmp_path / "browser.txt").write_text(_body(browser))
                browser.save_screenshot(str(tmp_path / "browser.png"))
                browser.quit()
            server.send_signal(signal.SIGINT)
            server.wait(timeout=15)
            log.seek(0)
            output = log.read()
            assert "Traceback" not in output, output


def test_digits_demo_defaults_and_mlflow_training_ui(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[2]
    with socket.socket() as probe, socket.socket() as mlflow_probe:
        probe.bind(("127.0.0.1", 0))
        mlflow_probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
        mlflow_port = mlflow_probe.getsockname()[1]
    log_path = tmp_path / "demo.log"
    with log_path.open("w") as log:
        process = subprocess.Popen(
            [
                sys.executable,
                str(root / "scripts/test_dashboard.py"),
                "--skip-tests",
                "--epochs",
                "2",
                "--no-browser",
                "--project-dir",
                str(tmp_path / "demo"),
                "--port",
                str(port),
                "--mlflow-port",
                str(mlflow_port),
            ],
            cwd=root,
            stdout=log,
            stderr=log,
        )
        browser = None
        try:
            deadline = time.monotonic() + 90
            while "Hiraku dashboard:" not in log_path.read_text():
                assert process.poll() is None, log_path.read_text()
                assert time.monotonic() < deadline, log_path.read_text()
                time.sleep(0.1)
            browser = _new_browser(monkeypatch)
            browser.get(f"http://127.0.0.1:{port}")
            WebDriverWait(browser, 30).until(lambda b: _run_rows(b) == 2)
            assert "Scope: This subtree" in _body(browser)
            WebDriverWait(browser, 20).until(
                lambda b: len(_elements(b, ".hk-tree-node")) >= 1
            )
            _toggle = _elements(browser, ".hk-tree-toggle")[0]
            browser.execute_script(
                "arguments[0].dispatchEvent(new MouseEvent('click', {bubbles: true}));",
                _toggle,
            )
            WebDriverWait(browser, 20).until(
                lambda b: len(_elements(b, ".hk-tree-node")) >= 3
            )
            # Exercise selection, search and expansion through the actual widget.
            _svg = _elements(browser, ".hk-tree-svg")[0]
            browser.execute_script(
                "arguments[0].dispatchEvent(new WheelEvent('wheel', "
                "{deltaY: -100, clientX: 100, clientY: 100, bubbles: true}));",
                _svg,
            )
            _transform = _elements(browser, ".hk-tree-viewport")[0].get_attribute(
                "transform"
            )
            browser.execute_script(
                "arguments[0].dispatchEvent(new MouseEvent('click', {bubbles: true}));",
                _elements(browser, '.hk-tree-node[data-id="strong-l2"]')[0],
            )
            WebDriverWait(browser, 20).until(lambda b: _run_rows(b) == 1)
            assert "Record detailstrong-l2" in _body(browser).replace("\n", "")
            assert (
                _elements(browser, ".hk-tree-viewport")[0].get_attribute("transform")
                == _transform
            )
            _search = _elements(browser, ".hk-tree-search")[0]
            browser.execute_script(
                "arguments[0].value = 'weak'; "
                "arguments[0].dispatchEvent(new Event('input', {bubbles: true}));",
                _search,
            )
            WebDriverWait(browser, 20).until(
                lambda b: _elements(b, '.hk-tree-node[data-id="weak-l2"]')
            )
            browser.execute_script(
                "arguments[0].dispatchEvent(new MouseEvent('click', {bubbles: true}));",
                _elements(browser, '.hk-tree-node[data-id="weak-l2"]')[0],
            )
            WebDriverWait(browser, 20).until(
                lambda b: "Record detailweak-l2" in _body(b).replace("\n", "")
            )
            assert _elements(browser, ".hk-tree-search")[0].get_attribute("value") == (
                "weak"
            )
            _select_record(browser, "digits")
            WebDriverWait(browser, 20).until(lambda b: _run_rows(b) == 2)
            WebDriverWait(browser, 20).until(
                lambda b: _elements(b, '.hk-tree-node.is-selected[data-id="digits"]')
            )
            _control(
                browser,
                'marimo-table[data-label*="Runs"]',
                '[data-testid="select-row-checkbox"]',
            ).click()
            WebDriverWait(browser, 20).until(
                lambda b: len(_elements(b, "a.hk-run-link")) == 1
            )
            _run_link = _elements(browser, "a.hk-run-link")[0]
            assert _run_link.get_attribute("target") == "_blank"
            assert _run_link.get_attribute("rel") == "noopener noreferrer"
            assert "/runs/" in _run_link.get_attribute("href")
            run_url = _run_link.get_attribute("href")
            run_id = run_url.split("/runs/", 1)[1].split("?", 1)[0]
            assert f"Run: {run_id}" in _body(browser)
            assert (
                Select(
                    _control(
                        browser,
                        'marimo-dropdown[data-label*="X / progress metric"]',
                        "select",
                    )
                ).first_selected_option.text
                == "val/log_loss"
            )
            assert (
                Select(
                    _control(
                        browser, 'marimo-dropdown[data-label*="Y metric"]', "select"
                    )
                ).first_selected_option.text
                == "val/accuracy"
            )
            _control(browser, "marimo-vega", "canvas")
            _control(
                browser,
                'marimo-radio[data-label*="Plot view"]',
                'button[value="Pareto"]',
            ).click()
            WebDriverWait(browser, 20).until(
                lambda b: (
                    "Pareto"
                    in b.find_element(By.CSS_SELECTOR, "marimo-vega").get_attribute(
                        "data-label"
                    )
                )
            )
            canvas = _rendered_canvas(browser)
            browser.execute_script("arguments[0].scrollIntoView()", canvas)
            browser.save_screenshot(str(tmp_path / "digits-pareto.png"))
            assert "Select both metrics and explicit" not in _body(browser)
            browser.get(run_url)
            WebDriverWait(browser, 30).until(
                lambda b: (
                    "val/log_loss" in b.execute_script("return document.body.innerText")
                )
            )
            mlflow_url = re.search(r"MLflow UI: (\S+)", log_path.read_text()).group(1)
            assert "workflowType=machine_learning" in mlflow_url
            browser.get(mlflow_url)
            WebDriverWait(browser, 30).until(
                lambda b: (
                    "weak-l2" in b.execute_script("return document.body.innerText")
                    and "strong-l2"
                    in b.execute_script("return document.body.innerText")
                )
            )
            browser.save_screenshot(str(tmp_path / "mlflow-training.png"))
        finally:
            if browser is not None:
                browser.quit()
            if process.poll() is None:
                process.send_signal(signal.SIGINT)
                process.wait(timeout=20)
