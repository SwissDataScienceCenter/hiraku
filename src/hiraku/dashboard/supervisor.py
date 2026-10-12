"""Share launcher-owned jobs across isolated marimo browser processes."""

from __future__ import annotations

import os
import secrets
import socket
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from multiprocessing.managers import BaseManager
from typing import Any

from ..config import HirakuConfig
from .capability import DashboardCapability
from .jobs import LaunchController


@contextmanager
def supervise(
    config: HirakuConfig, environment: dict[str, str]
) -> Iterator[LaunchController]:
    controller = LaunchController(config, DashboardCapability(interactive=True))

    class Manager(BaseManager):
        pass

    Manager.register("controller", callable=lambda: controller)
    key = secrets.token_bytes(32)
    server = Manager(address=("127.0.0.1", 0), authkey=key).get_server()
    server.stop_event = threading.Event()

    def serve() -> None:
        # BaseManager's default accepter loops forever, even on a closed socket.
        while not server.stop_event.is_set():
            connection = server.listener.accept()
            threading.Thread(
                target=server.handle_request, args=(connection,), daemon=True
            ).start()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    environment["HIRAKU_DASHBOARD_CONTROLLER_PORT"] = str(server.address[1])
    environment["HIRAKU_DASHBOARD_CONTROLLER_KEY"] = key.hex()
    try:
        yield controller
    finally:
        # The launcher owns workers even if marimo kills a disconnected kernel.
        controller.shutdown()
        server.stop_event.set()
        with socket.create_connection(server.address, timeout=2):
            pass
        thread.join(timeout=2)
        server.listener.close()


def connect_controller() -> Any:
    class Manager(BaseManager):
        pass

    Manager.register("controller")
    manager = Manager(
        address=("127.0.0.1", int(os.environ["HIRAKU_DASHBOARD_CONTROLLER_PORT"])),
        authkey=bytes.fromhex(os.environ["HIRAKU_DASHBOARD_CONTROLLER_KEY"]),
    )
    manager.connect()
    return manager.controller()
