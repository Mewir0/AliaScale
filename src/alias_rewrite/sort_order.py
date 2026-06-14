from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import unicodedata

from .aliases import extract_leading_mora


MOJIBAKE_MARKERS = ("縺", "繧", "譁", "荳", "驥", "�")
WHITESPACE_RE = re.compile(r"[\s\u3000]+")


# カタカナをひらがなへ寄せる
def kana_sort_key_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text or "")
    result: list[str] = []
    for char in normalized:
        code = ord(char)
        if 0x30A1 <= code <= 0x30F6:
            result.append(chr(code - 0x60))
        else:
            result.append(char)
    return "".join(result)


@dataclass(frozen=True)
# ユーザー定義順を保持する
class SortTextOrder:
    ranks: dict[str, int]

    # 文字列を並べ替え用キーへ変換する
    def key(self, text: str) -> tuple[tuple[int, object], ...]:
        normalized = kana_sort_key_text(text)
        result: list[tuple[int, object]] = []
        rest = normalized
        while rest:
            token, suffix = extract_leading_mora(rest)
            if not token:
                token, suffix = rest[0], rest[1:]
            if token in self.ranks:
                result.append((0, self.ranks[token]))
            else:
                result.append((1, token.casefold()))
            rest = suffix
        return tuple(result)


# 文字化けらしさを判定する
def looks_mojibake(text: str) -> bool:
    if not text:
        return False
    marker_count = sum(text.count(marker) for marker in MOJIBAKE_MARKERS)
    return marker_count >= 3 or marker_count / max(len(text), 1) > 0.02


# 五十音ファイルを読み取る
def read_otolist_text(path: str | Path) -> str:
    data = Path(path).read_bytes()
    try:
        cp932_text = data.decode("cp932")
    except UnicodeDecodeError:
        return data.decode("utf-8-sig")
    if looks_mojibake(cp932_text):
        try:
            return data.decode("utf-8-sig")
        except UnicodeDecodeError:
            return cp932_text
    return cp932_text


# 五十音テキストを順序表へ変換する
def parse_otolist_order(text: str) -> SortTextOrder:
    ranks: dict[str, int] = {}
    rank = 0
    for raw_line in text.splitlines():
        line = WHITESPACE_RE.sub("", kana_sort_key_text(raw_line))
        if not line:
            continue
        rest = line
        while rest:
            token, suffix = extract_leading_mora(rest)
            if not token:
                token, suffix = rest[0], rest[1:]
            if token not in ranks:
                ranks[token] = rank
                rank += 1
            rest = suffix
    return SortTextOrder(ranks)


# 五十音順序表を読み込む
def load_otolist_order(path: str | Path | None) -> SortTextOrder | None:
    if not path:
        return None
    candidate = Path(path)
    if not candidate.is_file():
        return None
    return parse_otolist_order(read_otolist_text(candidate))
