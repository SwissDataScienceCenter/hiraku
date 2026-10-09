"""Opt-in real-browser checks; see docs/dashboard.md for setup."""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

import pytest
from fixtures import create_acceptance_project
from selenium import webdriver
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import Select, WebDriverWait

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
        lambda b: b.execute_script(
            "return document.querySelector(arguments[0])?.shadowRoot"
            "?.querySelector(arguments[1])",
            host,
            selector,
        )
    )


def _body(browser):
    return browser.find_element(By.TAG_NAME, "body").text


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


def test_dashboard_browser_workflow(tmp_path):
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
            if os.environ["HIRAKU_TEST_BROWSER"] == "webkit":
                browser = webdriver.WebKitGTK()
            else:
                options = webdriver.ChromeOptions()
                options.add_argument("--headless=new")
                options.add_argument("--no-sandbox")
                browser = webdriver.Chrome(options=options)
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
            canvas = _control(browser, "marimo-vega", "canvas")
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
