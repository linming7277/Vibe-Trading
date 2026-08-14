"""Run one complete TDX fundamental refresh and wait for its terminal state."""

from __future__ import annotations

import json
import time

from src.tdx_data.service import get_tdx_service


def main() -> int:
    service = get_tdx_service()
    job = service.start_update("fundamental")
    print(json.dumps(job, ensure_ascii=False), flush=True)
    last_marker: tuple[int, int, str] | None = None
    while True:
        current = service.store.get_job(job["id"])
        if not current:
            print("TDX job disappeared", flush=True)
            return 1
        state = next(
            (item for item in service.store.module_states() if item["module"] == "fundamental"),
            {},
        )
        marker = (
            int(state.get("progress") or 0),
            int(state.get("total") or 0),
            str(current.get("status") or ""),
        )
        if marker != last_marker:
            print(
                json.dumps(
                    {
                        "job_id": job["id"],
                        "status": current.get("status"),
                        "progress": state.get("progress"),
                        "total": state.get("total"),
                        "message": state.get("message") or current.get("message"),
                        "error": current.get("error") or state.get("error"),
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
            last_marker = marker
        if current["status"] not in {"queued", "running"}:
            return 0 if current["status"] == "completed" else 1
        time.sleep(1)


if __name__ == "__main__":
    raise SystemExit(main())
