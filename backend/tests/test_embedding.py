"""埋め込みの検証。

初回はモデル読み込みに20秒ほどかかる（キャッシュ済みの場合）。
"""

import numpy as np

from app.config import get_settings
from app.services import embedding


def test_次元数が設定と一致する() -> None:
    """DBの vector(N) と食い違うと、挿入する瞬間まで気づけない。"""
    assert len(embedding.embed_query("テスト")) == get_settings().embedding_dim


def test_正規化されている() -> None:
    """pgvector のコサイン距離と整合させるため、長さ1でなければならない。"""
    v = np.array(embedding.embed_query("テスト"))
    assert np.isclose(np.linalg.norm(v), 1.0, atol=1e-5)


def test_保存用と検索用でプレフィックスが違う() -> None:
    """e5 は passage: / query: を使い分ける前提で学習されている。

    取り違えても例外は出ず精度だけ落ちるため、ここで固定しておく。
    """
    same_text = "A社はサポートを重視する"
    passage = np.array(embedding.embed_passages([same_text])[0])
    query = np.array(embedding.embed_query(same_text))
    # 同じ文でもプレフィックスが違えば別のベクトルになる
    assert not np.allclose(passage, query)


def test_空リストはモデルを呼ばずに空を返す() -> None:
    assert embedding.embed_passages([]) == []


def test_複数件をまとめて処理できる() -> None:
    vectors = embedding.embed_passages(["一件目", "二件目", "三件目"])
    assert len(vectors) == 3
    assert all(len(v) == get_settings().embedding_dim for v in vectors)


def test_意味が近い文ほど距離が近い() -> None:
    """埋め込みが意味を捉えているかの最低限の確認。"""
    base = np.array(embedding.embed_passages(["値引きを求められたら根拠を示す"])[0])
    near = np.array(embedding.embed_query("価格交渉への対応"))
    far = np.array(embedding.embed_query("明日の天気は晴れ"))
    assert float(base @ near) > float(base @ far)
