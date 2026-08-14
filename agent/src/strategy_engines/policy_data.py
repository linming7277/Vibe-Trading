"""Official-policy ingestion and deterministic policy-fit calculation."""

from __future__ import annotations

import hashlib
import json
import math
import re
import time
import uuid
from datetime import date
from typing import Any, Callable
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx
from bs4 import BeautifulSoup

from .value_data_store import ValueDataStore, now


POLICY_SOURCES = (
    ("国务院政策文件库", "https://sousuo.www.gov.cn/zcwjk/"),
    ("国家发展和改革委员会", "https://zfxxgk.ndrc.gov.cn/web/dirlist.jsp"),
    ("工业和信息化部", "https://www.miit.gov.cn/zwgk/"),
)
ALLOWED_HOSTS = ("www.gov.cn", "sousuo.www.gov.cn", "zfxxgk.ndrc.gov.cn", "www.ndrc.gov.cn", "www.miit.gov.cn")
CLASSIFIER_VERSION = "value-policy-classifier-v1.0.0"

POLICY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "半导体": ("半导体", "集成电路", "芯片"),
    "电子": ("电子信息", "消费电子", "电子元器件"),
    "计算机": ("人工智能", "算力", "软件", "数字经济", "数据要素"),
    "通信": ("通信", "5G", "光通信", "卫星互联网"),
    "汽车": ("新能源汽车", "智能网联汽车", "汽车消费"),
    "电力设备": ("光伏", "风电", "储能", "电网", "新能源装备"),
    "医药": ("医药", "医疗器械", "生物医药", "创新药"),
    "食品饮料": ("食品安全", "酒业", "饮料"),
    "银行": ("银行", "信贷", "金融支持", "贷款"),
    "证券": ("资本市场", "证券", "上市公司", "并购重组"),
    "房地产": ("房地产", "保障性住房", "城市更新"),
    "建筑": ("基础设施", "城市更新", "重大工程"),
    "煤炭": ("煤炭", "煤电"),
    "有色": ("有色金属", "稀土", "战略性矿产"),
    "机械": ("高端装备", "工业母机", "机器人"),
    "军工": ("国防科技", "航空航天", "低空经济"),
    "环保": ("节能降碳", "污染防治", "循环经济", "绿色发展"),
    "农业": ("农业", "种业", "粮食安全", "乡村振兴"),
    "传媒": ("文化产业", "网络游戏", "出版"),
    "旅游": ("旅游", "文旅", "消费促进"),
}
def _normalize_url(value: str) -> str:
    parts = urlsplit(value)
    scheme = "https" if parts.scheme in {"http", "https"} else parts.scheme
    return urlunsplit((scheme, parts.netloc.lower(), parts.path.rstrip("/"), parts.query, ""))


def _text(soup: BeautifulSoup) -> str:
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    return re.sub(r"\s+", " ", soup.get_text(" ", strip=True)).strip()


def _published(text: str) -> str | None:
    match = re.search(r"(20\d{2})[年\-/.](\d{1,2})[月\-/.](\d{1,2})日?", text)
    if not match:
        return None
    try:
        return date(*map(int, match.groups())).isoformat()
    except ValueError:
        return None


class PolicyDataService:
    def __init__(
        self,
        store: ValueDataStore | None = None,
        *,
        fetcher: Callable[[str, dict[str, str]], tuple[str, dict[str, str]]] | None = None,
        model_classifier: Callable[[dict[str, Any], list[dict[str, str]]], list[dict[str, Any]]] | None = None,
    ) -> None:
        self.store = store or ValueDataStore()
        self.fetcher = fetcher or self._fetch
        self.model_classifier = model_classifier or self._model_classify

    @staticmethod
    def _fetch(url: str, headers: dict[str, str]) -> tuple[str, dict[str, str]]:
        error: Exception | None = None
        for attempt in range(3):
            try:
                with httpx.Client(timeout=15, follow_redirects=True, headers={"User-Agent": "hzstock-value-research/1.0", **headers}) as client:
                    response = client.get(url)
                    response.raise_for_status()
                    return response.text, {**dict(response.headers), "x-hz-status": str(response.status_code)}
            except Exception as exc:
                error = exc
                if attempt < 2:
                    time.sleep(.25 * (2 ** attempt))
        raise RuntimeError(f"policy_source_unavailable:{url}:{error}")

    def refresh(self, industries: list[dict[str, str]], *, limit_per_source: int = 30) -> dict[str, Any]:
        events: list[dict[str, Any]] = []
        classifications: list[dict[str, Any]] = []
        errors: list[str] = []
        seen_urls: set[str] = set()
        seen_hashes: set[str] = set()
        seen_document_numbers: set[str] = set()
        for source_name, start_url in POLICY_SOURCES:
            try:
                listing, _ = self.fetcher(start_url, {})
                soup = BeautifulSoup(listing, "html.parser")
                links: list[str] = []
                for anchor in soup.select("a[href]"):
                    url = _normalize_url(urljoin(start_url, str(anchor.get("href") or "")))
                    if urlsplit(url).hostname in ALLOWED_HOSTS and url not in links and url not in seen_urls:
                        links.append(url)
                if not links:
                    raise RuntimeError("page_structure_changed:no_policy_links")
                for url in links[:limit_per_source]:
                    try:
                        seen_urls.add(url)
                        request_headers = self.store.policy_request_headers(url)
                        html, response_headers = self.fetcher(url, request_headers)
                        if str(response_headers.get("x-hz-status") or "") == "304":
                            continue
                        detail = BeautifulSoup(html, "html.parser")
                        content = _text(detail)
                        if len(content) < 80:
                            continue
                        title = (detail.title.get_text(" ", strip=True) if detail.title else content[:80]).strip()
                        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
                        if content_hash in seen_hashes:
                            continue
                        seen_hashes.add(content_hash)
                        document_match = re.search(r"([\u4e00-\u9fff]{1,12}〔20\d{2}〕\d+号)", content)
                        document_number = document_match.group(1) if document_match else ""
                        if document_number and document_number in seen_document_numbers:
                            continue
                        if document_number:
                            seen_document_numbers.add(document_number)
                        existing = self.store.find_policy_event(
                            document_number=document_number, normalized_url=url, content_hash=content_hash,
                        )
                        event_id = str(existing["id"]) if existing else f"policy_{hashlib.sha256((url + content_hash).encode()).hexdigest()[:16]}"
                        event = {
                            "id": event_id, "document_number": document_number,
                            "title": title[:500], "normalized_url": url, "content_hash": content_hash,
                            "source": source_name, "published_at": _published(content), "fetched_at": now(),
                            "etag": response_headers.get("etag", "") or response_headers.get("ETag", ""),
                            "last_modified": response_headers.get("last-modified", "") or response_headers.get("Last-Modified", ""),
                            "status": "pending", "content_text": content[:100_000], "metadata": {},
                        }
                        candidates = self._candidate_industries(content, industries)
                        labeled = self._classify(event, candidates)
                        valid = [row for row in labeled if float(row.get("confidence") or 0) >= .65 and row.get("industry_code")]
                        event["status"] = "ready" if valid else "pending"
                        event["metadata"] = {"candidate_count": len(candidates), "classifier_version": CLASSIFIER_VERSION}
                        events.append(event)
                        for row in labeled:
                            confidence = float(row.get("confidence") or 0)
                            classifications.append({
                                "id": f"policy_class_{uuid.uuid4().hex[:16]}", "event_id": event_id,
                                "industry_code": str(row.get("industry_code") or ""),
                                "industry_name": str(row.get("industry_name") or ""),
                                "direction": 1 if int(row.get("direction") or 0) > 0 else -1,
                                "strength": max(1, min(3, int(row.get("strength") or 1))),
                                "sensitivity": max(0.0, min(1.0, float(row.get("sensitivity") or 0))),
                                "horizon_days": max(1, int(row.get("horizon_days") or 90)),
                                "evidence": str(row.get("evidence") or "")[:1000], "confidence": confidence,
                                "classifier_version": CLASSIFIER_VERSION,
                                "status": "ready" if confidence >= .65 else "pending", "created_at": now(),
                            })
                        time.sleep(.05)
                    except Exception as exc:
                        errors.append(f"{source_name}:{url}:{exc}")
            except Exception as exc:
                errors.append(f"{source_name}:{exc}")
        if events:
            self.store.upsert_policy_events(events, classifications)
        return {"status": "partial" if errors else "ready", "events": len(events), "classifications": len(classifications), "errors": errors[:50]}

    @staticmethod
    def _candidate_industries(content: str, industries: list[dict[str, str]]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for industry in industries:
            name = str(industry.get("name") or "")
            matched = name and name in content
            evidence = name if matched else ""
            sensitivity = 1.0 if matched else 0.0
            if not matched:
                for label, keywords in POLICY_KEYWORDS.items():
                    if label in name and any(keyword in content for keyword in keywords):
                        matched, evidence = True, next(keyword for keyword in keywords if keyword in content)
                        sensitivity = .75
                        break
            if matched:
                result.append({
                    "industry_code": str(industry.get("code") or ""), "industry_name": name,
                    "evidence": evidence, "sensitivity": sensitivity,
                })
        return result

    def _classify(self, event: dict[str, Any], candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not candidates:
            return []
        if self.model_classifier is not None:
            try:
                return self.model_classifier(event, candidates)
            except Exception:
                # Model outages are explicitly represented as pending below;
                # they never become a fabricated policy score.
                return [{**row, "direction": 1, "strength": 1, "horizon_days": 90, "confidence": 0.0} for row in candidates]
        return []

    @staticmethod
    def _model_classify(event: dict[str, Any], candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Use the configured research model only for structured classification.

        The prompt explicitly forbids an aggregate Policy Fit score.  All
        scoring and time decay remain deterministic in :meth:`policy_fit`.
        """
        from src.providers.chat import ChatLLM

        allowed = {row["industry_code"]: row for row in candidates}
        response = ChatLLM().chat([
            {
                "role": "system",
                "content": (
                    "你是中国官方产业政策分类器。只返回JSON数组，不要Markdown。"
                    "只能从候选行业中选择；每项必须包含industry_code、industry_name、"
                    "direction(-1或1)、strength(1至3)、horizon_days(30/90/365)、"
                    "evidence和confidence(0至1)。不要计算Policy Fit总分。"
                ),
            },
            {
                "role": "user",
                "content": json.dumps({
                    "title": event.get("title"), "source": event.get("source"),
                    "content": str(event.get("content_text") or "")[:12_000],
                    "candidates": candidates,
                }, ensure_ascii=False),
            },
        ], timeout=60)
        text = str(response.content or "").strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I | re.S)
        parsed = json.loads(text)
        if not isinstance(parsed, list):
            raise ValueError("policy classifier output must be an array")
        result: list[dict[str, Any]] = []
        for item in parsed:
            if not isinstance(item, dict):
                continue
            code = str(item.get("industry_code") or "")
            candidate = allowed.get(code)
            direction = int(item.get("direction") or 0)
            strength = int(item.get("strength") or 0)
            horizon = int(item.get("horizon_days") or 0)
            confidence = float(item.get("confidence") or 0)
            if not candidate or direction not in {-1, 1} or strength not in {1, 2, 3} or horizon not in {30, 90, 365} or not 0 <= confidence <= 1:
                continue
            result.append({
                **candidate, "direction": direction, "strength": strength,
                "horizon_days": horizon, "evidence": str(item.get("evidence") or candidate["evidence"])[:1000],
                "confidence": confidence,
            })
        return result

    def policy_fit(self, sector_code: str, as_of: str) -> dict[str, Any]:
        events = self.store.policies(status="ready", limit=500)
        contributions: list[dict[str, Any]] = []
        target = date.fromisoformat(as_of)
        for event in events:
            event_date = event.get("published_at") or str(event.get("fetched_at") or "")[:10]
            try:
                age = max(0, (target - date.fromisoformat(event_date)).days)
            except (TypeError, ValueError):
                continue
            if age < 0:
                continue
            for item in event.get("classifications", []):
                if item.get("industry_code") != sector_code or item.get("status") != "ready":
                    continue
                horizon = int(item.get("horizon_days") or 90)
                half_life = 30 if horizon <= 60 else 90 if horizon <= 180 else 365
                decay = .5 ** (age / half_life)
                sensitivity = max(0.0, min(1.0, float(item.get("sensitivity") or 0)))
                raw = int(item["direction"]) * int(item["strength"]) / 3 * sensitivity * float(item["confidence"]) * decay
                contributions.append({
                    "event_id": event["id"], "title": event["title"], "raw": raw,
                    "sensitivity": sensitivity, "age_days": age, "half_life": half_life,
                })
        if not contributions:
            return {"score": None, "events": [], "status": "no_effective_policy"}
        total = sum(item["raw"] for item in contributions)
        score = max(0.0, min(100.0, 50 + 50 * math.tanh(total)))
        return {"score": round(score, 4), "events": contributions, "status": "ready"}

    def list(self, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        return self.store.policies(status=status, limit=limit)
