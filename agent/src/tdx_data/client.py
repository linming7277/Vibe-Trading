"""Lazy, read-only wrapper around the broker TongDaXin Python bridge."""

from __future__ import annotations

import importlib.util
import math
import os
import sys
import threading
from datetime import date, datetime
from pathlib import Path
from typing import Any


def json_safe(value: Any) -> Any:
    """Convert pandas/numpy values returned by tqcenter into JSON-safe objects."""
    if hasattr(value, "to_dict"):
        try:
            records = value.reset_index().to_dict(orient="records")
            return [json_safe(item) for item in records]
        except (TypeError, ValueError):
            return json_safe(value.to_dict())
    if isinstance(value, str):
        # The broker DLL sometimes returns GBK bytes widened as Latin-1 when
        # the API process is launched without an interactive console.  Repair
        # that representation while leaving normal Unicode/ASCII untouched.
        try:
            repaired = value.encode("latin-1").decode("gbk")
            if sum("\u4e00" <= ch <= "\u9fff" for ch in repaired) > sum("\u4e00" <= ch <= "\u9fff" for ch in value):
                return repaired
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
        return value
    if isinstance(value, (date, datetime)) or value.__class__.__name__ == "Timestamp":
        try:
            return value.isoformat()
        except (AttributeError, ValueError):
            return str(value)
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if hasattr(value, "item"):
        try:
            value = value.item()
        except (TypeError, ValueError):
            pass
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    return value


class TdxClient:
    """One connection per API process; only documented read methods are exposed."""

    def __init__(self, tdx_home: str | Path | None = None) -> None:
        self.home = Path(tdx_home or os.environ.get("HZ_TDX_HOME", r"C:\zd_zyb"))
        self.user_dir = self.home / "PYPlugins" / "user"
        self.bridge_file = self.user_dir / "tqcenter.py"
        self._tq: Any = None
        self._lock = threading.RLock()

    @property
    def available(self) -> bool:
        return self.bridge_file.is_file() and (self.home / "tdxw.exe").is_file()

    def _connection_file(self) -> str:
        return str(self.user_dir / f"hzstock_data_service_{os.getpid()}.py")

    def connect(self) -> None:
        with self._lock:
            if not self.available:
                raise RuntimeError(f"未找到通达信数据桥：{self.bridge_file}")
            if self._tq is None:
                module_name = "hzstock_tqcenter"
                module = sys.modules.get(module_name)
                if module is None:
                    spec = importlib.util.spec_from_file_location(module_name, self.bridge_file)
                    if spec is None or spec.loader is None:
                        raise RuntimeError("无法加载通达信 tqcenter.py")
                    module = importlib.util.module_from_spec(spec)
                    sys.modules[module_name] = module
                    spec.loader.exec_module(module)
                self._tq = module.tq
            # Always call initialize, even while the vendor wrapper still says
            # it is initialized.  initialize() refreshes _connection_path but
            # is otherwise a no-op for a healthy connection.  This matters
            # when tdxw.exe was restarted while the API process stayed alive:
            # the old DLL run id becomes invalid and the vendor library would
            # otherwise misleadingly report "连接路径为空" on reconnect.
            self._tq.initialize(self._connection_file())

    def _reconnect(self) -> None:
        if self._tq is None:
            self.connect()
            return
        try:
            self._tq.close()
        except Exception:
            # A stale DLL handle may itself fail to close after tdxw restarts.
            pass
        self._tq.initialize(self._connection_file())

    def close(self) -> None:
        with self._lock:
            if self._tq is not None:
                self._tq.close()

    def call(self, method: str, *args: Any, **kwargs: Any) -> Any:
        """Call an allow-listed read endpoint while serialising DLL access."""
        allowed = {
            "get_stock_list", "get_pricevol", "get_market_snapshot", "get_market_data",
            "get_sector_list", "get_stock_list_in_sector", "get_stock_info", "get_more_info",
            "get_relation", "get_trackzs_etf_info", "get_kzz_info", "get_ipo_info",
            "formula_get_all", "formula_get_info", "get_divid_factors", "get_gb_info_by_date",
            "get_exday_data", "get_trading_dates", "refresh_cache", "get_financial_data",
            "formula_process_mul",
        }
        if method not in allowed:
            raise ValueError(f"禁止调用非只读通达信接口：{method}")
        with self._lock:
            try:
                self.connect()
            except Exception:
                try:
                    self._reconnect()
                except Exception as retry_exc:
                    raise RuntimeError("通达信连接初始化失败，请确认客户端已登录并保持运行") from retry_exc
            target = getattr(self._tq, method)
            try:
                value = target(*args, **kwargs)
            except Exception:
                if bool(getattr(self._tq, "_initialized", False)):
                    raise
                self._reconnect()
                value = target(*args, **kwargs)
            # Most tqcenter methods swallow a stale-run exception, mark the
            # class uninitialized and return an empty value.  Detect that
            # state and transparently repeat the read once on a fresh run id.
            if not bool(getattr(self._tq, "_initialized", False)):
                self._reconnect()
                value = target(*args, **kwargs)
            return json_safe(value)
