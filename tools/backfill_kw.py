"""一次性维护：清洗分片正文/重点词，回填索引搜索词 kw。

用法：python tools/backfill_kw.py
仅标准库。
"""
import json, os, re, glob, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fetch_library import LIB, SHARD_DIR, clean_text, key_words, kw_of, words_of, load_vocab


def main():
    vocab = load_vocab()
    index_path = os.path.join(LIB, "index.json")
    with open(index_path, encoding="utf-8") as f:
        index = json.load(f)

    meta = {}
    changed = 0
    for fn in sorted(glob.glob(os.path.join(SHARD_DIR, "*.json"))):
        with open(fn, encoding="utf-8") as f:
            shard = json.load(f)
        for b in shard.get("items", []):
            old = b.get("text", "")
            new = clean_text(old)
            if new != old:
                changed += 1
            b["text"] = new
            b["words"] = len(words_of(new))
            b["key"] = key_words(new, vocab, None)
            b["kw"] = kw_of(new)
            meta[b["id"]] = (b["words"], b["kw"])
        with open(fn, "w", encoding="utf-8") as f:
            json.dump(shard, f, ensure_ascii=False, separators=(",", ":"))

    miss = 0
    for it in index["items"]:
        if it["id"] in meta:
            it["words"], it["kw"] = meta[it["id"]]
        else:
            miss += 1
            it.setdefault("kw", [])
    with open(index_path, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, separators=(",", ":"))
    print("cleaned bodies:", changed, "| index items:", len(index["items"]),
          "| index without body:", miss, "| size", os.path.getsize(index_path))


if __name__ == "__main__":
    main()
