#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""EngLab 素材库抓取：把可联网真实素材源全量建库，前端按需加载。
数据源：News in Levels（wordpress sitemap 全量约 1.5 万篇，level 1/2/3 各 5100）
输出：
  data/library/index.json     索引（id/src/level/title/date/url/words/shard）
  data/library/s/NN.json      正文分片（每片 60 篇）
增量：已存在于索引的 id 跳过；--limit N 限制本次抓取篇数（供 CI 分批推进）
仅用标准库。
"""
import json, os, re, html, sys, time, urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB = os.path.join(ROOT, "data", "library")
SHARD_DIR = os.path.join(LIB, "s")
SHARD_SIZE = 60
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
      "Accept-Language": "en-US,en;q=0.9"}


def fetch(url, timeout=20, tries=2):
    last = None
    for _ in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8", "ignore")
        except Exception as e:
            last = e
            time.sleep(0.6)
    print("  fail:", url[-60:], type(last).__name__, str(last)[:60])
    return ""


def strip_tags(s):
    s = re.sub(r"(?is)<(script|style).*?</\1>", " ", s or "")
    s = re.sub(r"(?s)<br\s*/?>", " ", s)
    s = re.sub(r"(?s)<[^>]+>", " ", s)
    s = html.unescape(s)
    return re.sub(r"\s+", " ", s).strip()


def words_of(t):
    return re.findall(r"[A-Za-z][A-Za-z'-]+", t)


def clean_text(text):
    """去掉抓取时残留的标签片段与站点尾部废话。"""
    text = text or ""
    text = re.sub(r'(?is)^\s*id="[^"]*">', "", text)
    text = re.sub(r'(?is)\s*<[a-zA-Z/][^>]*>\s*$', "", text)
    text = re.sub(r'(?is)\s*<[a-zA-Z/][^>]*$', "", text)
    text = re.sub(r'(?is)^\s*(?:class|id)="[^"]*"\s*>', "", text)
    text = re.sub(r"^\s*\d{1,2}-\d{1,2}-\d{4}(?:\s+\d{1,2}:\d{2})?\s*", "", text)
    text = re.sub(r"The post .*? appeared first on .*?$", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def title_from_slug(slug):
    s = re.sub(r"-level-\d+$", "", slug)
    s = s.replace("-", " ").strip()
    return s[:1].upper() + s[1:]


def topic_id(slug):
    return re.sub(r"-level-\d+$", "", slug)


def load_vocab():
    try:
        with open(os.path.join(ROOT, "voice-pack.json"), encoding="utf-8") as f:
            j = json.load(f)
        return set(k.lower() for k in (j.get("d") or {}).keys())
    except Exception:
        return set()


BAD_WORDS = set("""ncontent class div span strong href src http https www html nbsp amp quot
article level section style script width height color font image photo video post comment""".split())


def kw_of(text, limit=12):
    """索引用的主题词：正文中较长实词，去停用词重复，供前端搜索。"""
    stop = set("""the and for that with this from have they will been when what your which their
about after before more other into over than then them these those such also just only very some most
many much there here would could should said says say because while where what who whom whose""".split())
    out, seen = [], set()
    for w in words_of(text):
        lw = w.lower()
        if len(lw) < 5 or lw in stop or lw in seen:
            continue
        if not re.fullmatch(r"[a-z][a-z\-']+", lw):
            continue
        seen.add(lw)
        out.append(lw)
        if len(out) >= limit:
            break
    return out


def key_words(text, vocab, extra=None, limit=14):
    out, seen = [], set()

    def add(w):
        w = w.strip().strip(".,;:!?\"'()").lower()
        if len(w) > 2 and w not in seen and w.isascii() and w not in BAD_WORDS and re.fullmatch(r"[a-z][a-z\-']+", w):
            seen.add(w)
            out.append(w)

    for w in (extra or []):
        add(w)
    ws = words_of(text)
    for w in ws:
        if len(out) >= limit:
            break
        if w.lower() in vocab:
            add(w)
    for w in ws:
        if len(out) >= limit:
            break
        if len(w) >= 8:
            add(w)
    return out[:limit]


# ---------- 索引：News in Levels 全量 sitemap ----------
def nil_sitemap_urls():
    """返回 [(url, lastmod)]，按 lastmod 倒序（新文章优先）"""
    urls = {}
    idx = fetch("https://www.newsinlevels.com/wp-sitemap.xml", timeout=25)
    maps = re.findall(r"<loc>(.*?)</loc>", idx)
    prod = [m for m in maps if "products" in m]
    if not prod:
        prod = ["https://www.newsinlevels.com/products-sitemap.xml"]
    for m in prod:
        t = fetch(m, timeout=25)
        for blk in re.findall(r"(?s)<url>(.*?)</url>", t):
            lm = re.search(r"<loc>(.*?)</loc>", blk)
            dm = re.search(r"<lastmod>([^<]+)</lastmod>", blk)
            if not lm:
                continue
            u = lm.group(1).strip()
            if re.search(r"/products/[^/]+-level-[123]/$", u):
                urls[u] = dm.group(1)[:10] if dm else ""
    return sorted(urls.items(), key=lambda kv: (kv[1], kv[0]), reverse=True)


# ---------- 正文：单篇 ----------
def parse_nil_page(url, vocab):
    t = fetch(url, timeout=20, tries=1)
    if not t:
        return None
    m = re.search(r'(?is)<[^>]*id="nContent"[^>]*>', t)
    if not m:
        return None
    seg = t[m.end():]
    end = re.search(r'(?is)<[^>]*class="article-lower"', seg)
    if end:
        seg = seg[:end.start()]
    strongs = [strip_tags(x) for x in re.findall(r"(?is)<strong>(.*?)</strong>", seg)]
    text = strip_tags(seg)
    text = clean_text(text)
    if len(text) < 80:
        return None
    dm = re.search(r'"datePublished":"([^"]+)"', t)
    date = dm.group(1)[:10] if dm else datetime.now(timezone.utc).strftime("%Y-%m-%d")
    slug = url.rstrip("/").split("/")[-1]
    lvl = int(re.search(r"-level-(\d)$", slug).group(1))
    return {
        "id": "nil-" + topic_id(slug) + "-l%d" % lvl,
        "src": "News in Levels",
        "level": lvl,
        "title": title_from_slug(slug),
        "date": date,
        "url": url,
        "text": text[:2600],
        "key": key_words(text, vocab, strongs),
        "kw": kw_of(text),
        "words": len(words_of(text)),
    }


def main():
    limit = 0
    for a in sys.argv[1:]:
        if a.startswith("--limit"):
            limit = int(a.split("=")[1]) if "=" in a else int(sys.argv[sys.argv.index(a) + 1])
    os.makedirs(SHARD_DIR, exist_ok=True)

    index_path = os.path.join(LIB, "index.json")
    try:
        with open(index_path, encoding="utf-8") as f:
            index = json.load(f)
    except Exception:
        index = {"v": 1, "updated": "", "count": 0, "items": []}
    have = {it["id"] for it in index["items"]}

    shards = {}
    for fn in os.listdir(SHARD_DIR):
        if fn.endswith(".json"):
            try:
                with open(os.path.join(SHARD_DIR, fn), encoding="utf-8") as f:
                    shards[int(fn[:-5])] = json.load(f)
            except Exception:
                pass

    print("crawl sitemap ...")
    pairs = nil_sitemap_urls()
    urls = [u for u, _ in pairs]
    print("sitemap urls:", len(urls))
    todo = []
    seen_topic = set()
    for u in urls:
        slug = u.rstrip("/").split("/")[-1]
        tid = topic_id(slug)
        if tid in seen_topic:
            continue
        seen_topic.add(tid)
        for lv in (1, 2, 3):
            iid = "nil-" + tid + "-l%d" % lv
            if iid not in have:
                todo.append("https://www.newsinlevels.com/products/%s-level-%d/" % (tid, lv))
    print("topics:", len(seen_topic), "missing bodies:", len(todo))
    if limit:
        todo = todo[:limit]

    vocab = load_vocab()
    got, t0 = 0, time.time()
    with ThreadPoolExecutor(max_workers=8) as ex:
        for it in ex.map(lambda u: parse_nil_page(u, vocab), todo):
            if not it:
                continue
            got += 1
            body = {k: it[k] for k in ("id", "text", "key")}
            shard_no = len(index["items"]) // SHARD_SIZE
            it["shard"] = shard_no
            index["items"].append({k: it.get(k, [] if k == "kw" else "") for k in ("id", "src", "level", "title", "date", "url", "words", "shard", "kw")})
            shards.setdefault(shard_no, {"v": 1, "items": []})["items"].append(body)
            if len(shards[shard_no]["items"]) >= SHARD_SIZE:
                shard_no += 1
    print("fetched:", got, "in", round(time.time() - t0, 1), "s")

    # 重排：按日期倒序，再重新分片，保证前端首屏拿最新
    index["items"].sort(key=lambda x: (x["date"], x["id"]), reverse=True)
    allbodies = {}
    for s in shards.values():
        for b in s["items"]:
            allbodies[b["id"]] = b
    index["items"] = [it for it in index["items"] if it["id"] in allbodies]
    for s in shards.values():
        s["items"] = []
    new_shards = {}
    for n, it in enumerate(index["items"]):
        it["shard"] = n // SHARD_SIZE
        new_shards.setdefault(it["shard"], {"v": 1, "items": []})["items"].append(allbodies[it["id"]])

    for fn in os.listdir(SHARD_DIR):
        if fn.endswith(".json"):
            os.remove(os.path.join(SHARD_DIR, fn))
    for no, s in new_shards.items():
        with open(os.path.join(SHARD_DIR, "%02d.json" % no), "w", encoding="utf-8") as f:
            json.dump(s, f, ensure_ascii=False, separators=(",", ":"))

    now = datetime.now(timezone.utc)
    index["updated"] = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    index["count"] = len(index["items"])
    index["shards"] = len(new_shards)
    index["total_available"] = len(seen_topic) * 3
    with open(index_path, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, separators=(",", ":"))
    print("wrote index:", index["count"], "items /", len(new_shards), "shards, available:", index["total_available"],
          "| size", os.path.getsize(index_path), "bytes")
    for it in index["items"][:5]:
        print("  ", it["date"], it["level"], it["title"][:46], "|", it["words"], "words")


if __name__ == "__main__":
    main()
