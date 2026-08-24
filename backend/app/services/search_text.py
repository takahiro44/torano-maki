"""検索専用フラットテキスト。人間の画面には出さない。"""


def generate_search_text(
    title: str,
    situation: str | None,
    customer_issue: str | None,
    sales_action: str | None,
    action_reason: str | None,
    result: str | None,
    learning: str | None,
) -> str:
    """CBR フィールドをフラットテキストに結合。embedding 用。"""
    parts = [title]
    if situation:
        parts.append(f"状況: {situation}")
    if customer_issue:
        parts.append(f"顧客課題: {customer_issue}")
    if sales_action:
        parts.append(f"営業対応: {sales_action}")
    if action_reason:
        parts.append(f"対応理由: {action_reason}")
    if result:
        parts.append(f"結果: {result}")
    if learning:
        parts.append(f"学び: {learning}")
    return " ".join(parts)
