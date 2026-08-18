"""Deterministic classification from existing TDX business text only.

This path is intentionally conservative: labels are medoid phrases taken from
the database, not invented product names, and every membership remains
NEEDS_REVIEW (confidence below 0.8).
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from typing import Any

from sklearn.cluster import KMeans
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from .classifier import eligible_profile
from .models import canonical_track_name, normalize_track_name, track_semantic_key

MIN_CLUSTER_SIMILARITY = .30

_SPLIT = re.compile(r"[，,、；;：:/\\|（）()\[\]【】]+")
_NOISE = {
    "其他", "其他业务", "其他收入", "非经营分部", "未分配项目", "分部间抵销",
    "主营业务", "主营产品", "相关业务", "合计",
}
_GENERIC_LABELS = {
    "产品", "业务", "服务", "收入", "销售", "制造", "相关", "公司", "方案",
    "系统", "智能", "综合", "经营", "主营", "其他收入",
}


def _phrases(profile: dict[str, Any]) -> list[str]:
    raw_fields = [profile.get(key) for key in (
        "main_business", "main_products", "business_scope", "company_description",
    )]
    result: list[str] = []
    for raw in raw_fields:
        for part in _SPLIT.split(str(raw or "")):
            text = normalize_track_name(part)
            text = re.sub(r"^(主营|自产商品|自产|主要|其中|销售)", "", text)
            text = re.sub(r"(收入|业务分部|分部)$", "", text)
            if text in _NOISE or len(text) < 2 or not re.search(r"[\u3400-\u9fffA-Za-z]", text):
                continue
            if text not in result:
                result.append(text[:30])
    return result


def _cluster_count(matrix: Any, unique_count: int, company_count: int) -> int:
    if unique_count <= 1 or matrix.shape[0] <= 2:
        return 1
    # A terminal TDX industry can still cover several operating models. Use a
    # bounded deterministic diversity target; labels still come only from
    # source business text and never from stock names or market prices.
    return min(12, unique_count, max(2, round(company_count / 3)))


def _short_common_label(representative: str, phrases: list[str]) -> str:
    """Prefer a shared source substring for short ambiguous medoid labels."""
    if len(representative) > 4 or len(phrases) < 2:
        return representative
    counts: Counter[str] = Counter()
    for phrase in set(phrases):
        seen: set[str] = set()
        for size in range(2, min(6, len(phrase)) + 1):
            seen.update(phrase[start:start + size] for start in range(len(phrase) - size + 1))
        counts.update(value for value in seen if value not in _GENERIC_LABELS)
    candidates = [value for value, count in counts.items()
                  if value in representative and count / len(set(phrases)) >= .5]
    return max(candidates, key=lambda value: (len(value), counts[value], value), default=representative)


def classify_profiles(industry: dict[str, Any], profiles: list[dict[str, Any]]) -> dict[str, Any]:
    eligible = [row for row in profiles if eligible_profile(row)]
    unclassified = [{
        "stock_code": row["stock_code"], "classification_status": "INSUFFICIENT_DATA",
        "reason": "数据库中没有可用于业务归类的真实主营、经营范围、公司描述或产品文本",
    } for row in profiles if not eligible_profile(row)]
    phrase_rows: list[tuple[str, str]] = []
    company_phrases: dict[str, list[str]] = {}
    for profile in eligible:
        values = _phrases(profile)
        if not values:
            unclassified.append({
                "stock_code": profile["stock_code"], "classification_status": "INSUFFICIENT_DATA",
                "reason": "业务文本经清理后只剩无业务含义的通用词",
            })
            continue
        company_phrases[profile["stock_code"]] = values
        phrase_rows.extend((profile["stock_code"], phrase) for phrase in values)
    if not phrase_rows:
        return {"industry_code": industry["industry_code"], "industry_name": industry["industry_name"],
                "tracks": [], "unclassified": unclassified}

    phrases = [row[1] for row in phrase_rows]
    vectorizer = TfidfVectorizer(analyzer="char", ngram_range=(2, 4), min_df=1, sublinear_tf=True)
    matrix = vectorizer.fit_transform(phrases)
    k = _cluster_count(matrix, len(set(phrases)), len(company_phrases))
    model = KMeans(n_clusters=k, random_state=17, n_init=20).fit(matrix)
    labels = model.labels_.tolist()
    similarities = cosine_similarity(matrix, model.cluster_centers_)
    phrase_frequency = Counter(phrases)
    indexes_by_cluster: dict[int, list[int]] = defaultdict(list)
    for index, label in enumerate(labels):
        indexes_by_cluster[int(label)].append(index)

    cluster_names: dict[int, str] = {}
    cluster_descriptions: dict[int, str] = {}
    ancestor_keys = {
        track_semantic_key(str(industry.get(field) or ""))
        for field in ("industry_name", "level2_name", "level1_name")
        if str(industry.get(field) or "").strip()
    }
    for cluster, indexes in sorted(indexes_by_cluster.items()):
        qualified = [index for index in indexes if similarities[index, cluster] >= MIN_CLUSTER_SIMILARITY]
        if not qualified:
            continue
        ranked = sorted(qualified, key=lambda index: (
            similarities[index, cluster] * .6 + min(len(phrases[index]), 12) / 200
            + min(phrase_frequency[phrases[index]], 8) * .05,
            len(phrases[index]), phrases[index],
        ), reverse=True)
        qualified_phrases = [canonical_track_name(phrases[index]) for index in ranked]
        representative = canonical_track_name(phrases[ranked[0]])
        representative = _short_common_label(representative, qualified_phrases)
        if track_semantic_key(representative) in ancestor_keys or representative in _GENERIC_LABELS:
            # Repeating an ancestor name is not a finer operating track. Keep
            # these companies explicitly unclassified until richer business
            # data can support a real subdivision.
            continue
        cluster_names[cluster] = representative[:40]
        examples = []
        for index in ranked:
            if phrases[index] not in examples:
                examples.append(phrases[index])
            if len(examples) == 3:
                break
        cluster_descriptions[cluster] = (
            f"依据通达信主营业务文本归并，主要覆盖{'、'.join(examples)}等产品或业务。"
        )

    tracks_by_key: dict[str, dict[str, Any]] = {}
    profile_map = {row["stock_code"]: row for row in profiles}
    for symbol, values in company_phrases.items():
        phrase_indexes = [index for index, row in enumerate(phrase_rows) if row[0] == symbol]
        assigned: list[tuple[int, int]] = []
        for position, index in enumerate(phrase_indexes):
            cluster = int(labels[index])
            if (cluster in cluster_names and similarities[index, cluster] >= MIN_CLUSTER_SIMILARITY
                    and cluster not in {item[0] for item in assigned}):
                assigned.append((cluster, index))
        if not assigned:
            unclassified.append({
                "stock_code": symbol, "classification_status": "UNCLASSIFIED",
                "reason": "经营文本只到行业通用层级，或与行业内细分业务组相似度不足，未强行生成赛道",
            })
            continue
        for position, (cluster, index) in enumerate(assigned[:3]):
            name = cluster_names[cluster]
            key = track_semantic_key(name)
            track = tracks_by_key.setdefault(key, {
                "track_name": name, "description": cluster_descriptions[cluster], "companies": [],
            })
            similarity = float(similarities[index, cluster])
            confidence = round(min(.79, max(.60, .60 + .19 * similarity)), 4)
            source_text = str(profile_map[symbol].get("main_business") or values[0])
            track["companies"].append({
                "stock_code": symbol,
                "membership_type": "PRIMARY" if position == 0 else "SECONDARY",
                "confidence": confidence,
                "reason": f"通达信主营业务为“{source_text[:100]}”，其中“{values[min(position, len(values)-1)]}”与该赛道业务文本最接近。",
            })
    tracks = sorted(tracks_by_key.values(), key=lambda row: (-len(row["companies"]), row["track_name"]))
    return {
        "industry_code": industry["industry_code"], "industry_name": industry["industry_name"],
        "tracks": tracks, "unclassified": unclassified,
    }
