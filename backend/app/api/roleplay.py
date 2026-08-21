"""ロープレのエンドポイント。担当: CLAUDE.md 1.1 を参照。

蓄積されたナレッジから顧客ペルソナを生成し、対話練習に使う。
"""

from fastapi import APIRouter

router = APIRouter(prefix="/roleplay", tags=["roleplay"])

# TODO: 以下を実装する
#   POST /roleplay/personas  ナレッジから顧客ペルソナを生成
#   POST /roleplay/sessions  ロープレの対話を開始・継続
