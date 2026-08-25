"""検索専用フラットテキスト。人間の画面には出さない。"""

from __future__ import annotations

from typing import Any


def generate_search_text(
    title: str,
    situation: str | None = None,
    problem: str | None = None,
    judgment: str | None = None,
    action: str | None = None,
    reasoning: str | None = None,
    outcome: str | None = None,
    lesson: str | None = None,
    applicable_situations: str | None = None,
    limitations: str | None = None,
    industry: str | None = None,
    product: str | None = None,
    sales_stage: str | None = None,
) -> str:
    """全フィールドをフラットテキストに結合。embedding 用。"""
    parts = [title]
    for label, value in [
        ("状況", situation),
        ("顧客課題", problem),
        ("判断", judgment),
        ("行動", action),
        ("理由", reasoning),
        ("結果", outcome),
        ("学び", lesson),
        ("適用場面", applicable_situations),
        ("制約", limitations),
    ]:
        if value:
            parts.append(f"{label}: {value}")

    meta: list[str] = []
    if industry:
        meta.append(f"業界: {industry}")
    if product:
        meta.append(f"商材: {product}")
    if sales_stage:
        meta.append(f"フェーズ: {sales_stage}")
    if meta:
        parts.append(" ".join(meta))

    return " ".join(parts)


def generate_search_text_from_mapping(data: dict[str, Any]) -> str:
    """ORM / Pydantic の dump から search_text を作る。"""
    return generate_search_text(
        title=str(data["title"]),
        situation=data.get("situation"),
        problem=data.get("problem"),
        judgment=data.get("judgment"),
        action=data.get("action"),
        reasoning=data.get("reasoning"),
        outcome=data.get("outcome"),
        lesson=data.get("lesson"),
        applicable_situations=data.get("applicable_situations"),
        limitations=data.get("limitations"),
        industry=data.get("industry"),
        product=data.get("product"),
        sales_stage=data.get("sales_stage"),
    )
