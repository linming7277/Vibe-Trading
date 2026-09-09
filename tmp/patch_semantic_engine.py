from pathlib import Path

# 1) engine: semantic guard after structural validation
p = Path('src/macro_forecast/engine.py')
text = p.read_text(encoding='utf-8')
old = '''        report = validator.validate(model_output)
        abstained = bool(model_output.get("abstain"))'''
new = '''        report = validator.validate(model_output)
        # 语义守则 V1：结构校验后追加确定性语义审计（0 LLM）。
        from src.macro_forecast.semantic import SemanticGuard, annotate_catalog

        catalog_types = annotate_catalog(prep["evidence_catalog"])
        semantic_report = SemanticGuard(
            catalog_types=catalog_types, alias_map=prep.get("alias_map") or {},
        ).run(model_output, report.get("industry_entries") or [])
        for entry, audit in zip(report.get("industry_entries") or [], semantic_report["industries"]):
            entry["reason_basis"] = audit["reason_basis"]
        abstained = bool(model_output.get("abstain"))'''
assert old in text, 'validation anchor'
text = text.replace(old, new)

# INVALID_OUTPUT path payload call
old2 = '''        payload = self._structured_payload(
            prep=prep, bundle=bundle, model_output=model_output, report=report,
            macro_summary=macro_summary,
        )'''
new2 = '''        payload = self._structured_payload(
            prep=prep, bundle=bundle, model_output=model_output, report=report,
            macro_summary=macro_summary, semantic_report=semantic_report,
        )'''
count2 = text.count(old2)
assert count2 >= 1, f'payload call sites: {count2}'
text = text.replace(old2, new2)

old4 = '''                            abstain_reason: str | None = None,
                            model_error: str | None = None) -> dict[str, Any]:'''
new4 = '''                            abstain_reason: str | None = None,
                            model_error: str | None = None,
                            semantic_report: dict[str, Any] | None = None) -> dict[str, Any]:'''
assert old4 in text, 'payload signature'
text = text.replace(old4, new4)

old5 = '''            "model_output": model_output,
            "validation": report,'''
new5 = '''            "model_output": model_output,
            "validation": report,
            "semantic": semantic_report,'''
assert old5 in text, 'payload body'
p.write_text(text.replace(old5, new5))
print('engine semantic wiring done')

# 2) forecast_store: semantic validations table + api
p2 = Path('src/macro_forecast/forecast_store.py')
t2 = p2.read_text(encoding='utf-8')
old6 = '''                CREATE TABLE IF NOT EXISTS forecast_provider_health ('''
new6 = '''                CREATE TABLE IF NOT EXISTS forecast_semantic_validations (
                    forecast_id TEXT NOT NULL,
                    guard_version TEXT NOT NULL,
                    report_json TEXT NOT NULL,
                    revised_narrative_md TEXT,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (forecast_id, guard_version)
                );
                CREATE TABLE IF NOT EXISTS forecast_provider_health ('''
assert old6 in t2, 'health table anchor'
t2 = t2.replace(old6, new6)

anchor = '    def close(self) -> None:\n        self._conn.close()\n'
addition = '''    def save_semantic_validation(self, forecast_id: str, *, guard_version: str,
                                 report: dict[str, Any], revised_narrative_md: str) -> dict[str, Any]:
        import json as _json

        existing = self._conn.execute(
            "SELECT guard_version FROM forecast_semantic_validations WHERE forecast_id=? AND guard_version=?",
            (forecast_id, guard_version),
        ).fetchone()
        if existing:
            return {"status": "ALREADY_SAVED", "written": 0}
        with self._conn:
            self._conn.execute(
                "INSERT INTO forecast_semantic_validations(forecast_id, guard_version, report_json, revised_narrative_md, created_at) VALUES(?,?,?,?,?)",
                (forecast_id, guard_version, _json.dumps(report, ensure_ascii=False, default=str),
                 revised_narrative_md, _now()),
            )
        return {"status": "SAVED", "written": 1}

    def load_semantic_validation(self, forecast_id: str, *, guard_version: str | None = None) -> dict[str, Any] | None:
        import json as _json

        if guard_version:
            row = self._conn.execute(
                "SELECT * FROM forecast_semantic_validations WHERE forecast_id=? AND guard_version=?",
                (forecast_id, guard_version),
            ).fetchone()
        else:
            row = self._conn.execute(
                "SELECT * FROM forecast_semantic_validations WHERE forecast_id=? ORDER BY created_at DESC LIMIT 1",
                (forecast_id,),
            ).fetchone()
        if not row:
            return None
        result = dict(row)
        try:
            result["report"] = _json.loads(result.pop("report_json") or "{}")
        except (TypeError, ValueError):
            result["report"] = {}
        return result

'''
assert anchor in t2, 'close anchor'
t2 = t2.replace(anchor, addition + anchor, 1)
p2.write_text(t2, encoding='utf-8')
print('semantic store table added')
