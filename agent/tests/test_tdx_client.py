from __future__ import annotations

import os
from pathlib import Path

from src.tdx_data.client import TdxClient


class RestartedTq:
    def __init__(self) -> None:
        self._initialized = True
        self._connection_path = ""
        self.initialize_calls: list[str] = []
        self.reads = 0

    def initialize(self, path: str) -> None:
        self._connection_path = path
        self.initialize_calls.append(path)
        self._initialized = True

    def close(self) -> None:
        self._initialized = False

    def get_stock_list(self, *_args, **_kwargs):
        self.reads += 1
        if self.reads == 1:
            self._initialized = False
            return []
        return [{"Code": "600519.SH", "Name": "贵州茅台"}]


class InitializePathErrorTq:
    def __init__(self) -> None:
        self._initialized = False
        self.initialize_calls = 0

    def initialize(self, _path: str) -> None:
        self.initialize_calls += 1
        if self.initialize_calls == 1:
            raise RuntimeError("TQ数据接口初始化失败: 连接路径为空，请先调用 tq.initialize(path)")
        self._initialized = True

    def close(self) -> None:
        self._initialized = False

    def get_stock_list(self, *_args, **_kwargs):
        return [{"Code": "600519.SH", "Name": "贵州茅台"}]


def test_connect_always_restores_vendor_connection_path(tmp_path: Path) -> None:
    client = TdxClient(tmp_path)
    client.bridge_file.parent.mkdir(parents=True)
    client.bridge_file.touch()
    (tmp_path / "tdxw.exe").touch()
    tq = RestartedTq()
    client._tq = tq

    client.connect()

    assert tq._connection_path.endswith(f"hzstock_data_service_{os.getpid()}.py")
    assert tq.initialize_calls


def test_read_reconnects_once_after_tdx_process_restart(tmp_path: Path) -> None:
    client = TdxClient(tmp_path)
    client.bridge_file.parent.mkdir(parents=True)
    client.bridge_file.touch()
    (tmp_path / "tdxw.exe").touch()
    tq = RestartedTq()
    client._tq = tq

    value = client.call("get_stock_list", "5", list_type=1)

    assert value[0]["Code"] == "600519.SH"
    assert tq.reads == 2
    assert len(tq.initialize_calls) >= 2


def test_path_empty_vendor_error_is_reinitialized_and_retried(tmp_path: Path) -> None:
    client = TdxClient(tmp_path)
    client.bridge_file.parent.mkdir(parents=True)
    client.bridge_file.touch()
    (tmp_path / "tdxw.exe").touch()
    tq = InitializePathErrorTq()
    client._tq = tq

    value = client.call("get_stock_list", "5", list_type=1)

    assert value[0]["Code"] == "600519.SH"
    assert tq.initialize_calls == 2
