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
import json, os, re, html, sys, time, tempfile, random, urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB = os.path.join(ROOT, "data", "library")
SHARD_DIR = os.path.join(LIB, "s")
SHARD_SIZE = 60
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
      "Accept-Language": "en-US,en;q=0.9"}


def fetch(url, timeout=20, tries=4):
    """带指数退避：源站对高频并发会返回 503，需退避后重试。"""
    last, delay = None, 2.0
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8", "ignore")
        except Exception as e:
            last = e
            if i < tries - 1:
                time.sleep(delay)
                delay = min(delay * 2, 20)
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
SITEMAP_CACHE = os.path.join(tempfile.gettempdir(), "nil_sitemap.json")
SITEMAP_TTL = 6 * 3600


def _crawl_sitemap():
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


def nil_sitemap_urls():
    """返回 [(url, lastmod)]，按 lastmod 倒序（新文章优先）；本地缓存 6 小时内复用，抓空则多轮重试。"""
    try:
        if time.time() - os.path.getmtime(SITEMAP_CACHE) < SITEMAP_TTL:
            with open(SITEMAP_CACHE, encoding="utf-8") as f:
                cached = json.load(f)
            if len(cached) > 1000:
                print("sitemap from cache:", len(cached))
                return [tuple(x) for x in cached]
    except Exception:
        pass
    for attempt in range(6):
        got = _crawl_sitemap()
        if len(got) > 1000:
            try:
                with open(SITEMAP_CACHE, "w", encoding="utf-8") as f:
                    json.dump(got, f)
            except Exception:
                pass
            return got
        print("sitemap empty/tiny (%d), retry %d" % (len(got), attempt + 1))
        time.sleep(3 + attempt * 4)
    return []


# ---------- 正文：单篇 ----------
def parse_nil_page(url, vocab):
    t = fetch(url, timeout=20, tries=3)
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


def _throttled(u, vocab):
    """源站限流较严：请求前抖动间隔，避免 503。"""
    time.sleep(random.uniform(0.5, 0.9))
    return parse_nil_page(u, vocab)


# ---------- 索引读写：v3 瘦身格式 ----------
# 目标：把 5.4MB 单文件索引拆成「极小元信息 + 按年分文件」，并把 title/url/src 从索引里彻底删掉
#   （title 与 url 均可由 id（nil-<topic>-l<n>）在前端派生，src 走顶层 sources）
#   data/library/index.json   {v,updated,count,shards,total_available,sources,years:{2026:1329,...}}
#   data/library/i/YYYY.json  {v,year,items:[[id,level,date,words,shard,"kw kw"],...]}
# 每日 CI 只重写「当年」那一个文件（约 200KB），仓库日增长从 ~5.4MB 降到 ~0.2MB
IDX_DIR = os.path.join(LIB, "i")


def _row_to_item(r):
    return {"id": r[0], "level": r[1], "date": r[2], "words": r[3], "shard": r[4],
            "kw": (r[5] or "").split() if len(r) > 5 else []}


def _item_to_row(it):
    d = str(it.get("date") or "0000-00-00")
    return [it["id"], it.get("level"), d, it.get("words"), it.get("shard"),
            " ".join(it.get("kw") or [])]


def load_index(index_path):
    """兼容读取 v1(对象) / v2(单文件数组) / v3(按年拆分)，统一为内部对象列表。"""
    try:
        with open(index_path, encoding="utf-8") as f:
            index = json.load(f)
    except Exception:
        return {"v": 3, "updated": "", "count": 0, "items": []}
    items = index.get("items") or []
    if items and isinstance(items[0], list):
        index["items"] = [_row_to_item(r) for r in items]
    elif not items:
        rows = []
        for y in sorted((index.get("years") or {}), reverse=True):
            try:
                with open(os.path.join(IDX_DIR, "%s.json" % y), encoding="utf-8") as f:
                    rows.extend(json.load(f).get("items") or [])
            except Exception:
                pass
        index["items"] = [_row_to_item(r) for r in rows]
    for it in index["items"]:
        it.setdefault("kw", [])
    return index


def write_index(index, index_path):
    """写 v3：按年拆文件 + 极小元信息。返回 (总篇数, 索引总体积, 当年文件体积)。"""
    os.makedirs(IDX_DIR, exist_ok=True)
    groups = {}
    for it in index["items"]:
        groups.setdefault(str(it.get("date") or "0000-00-00")[:4], []).append(_item_to_row(it))
    years, total, size = {}, 0, 0
    for y, rows in groups.items():
        rows.sort(key=lambda r: (r[2], r[0]), reverse=True)
        fp = os.path.join(IDX_DIR, "%s.json" % y)
        with open(fp, "w", encoding="utf-8") as f:
            json.dump({"v": 3, "year": y, "items": rows}, f, ensure_ascii=False, separators=(",", ":"))
        years[y] = len(rows)
        total += len(rows)
        size += os.path.getsize(fp)
    for fn in os.listdir(IDX_DIR):
        if fn.endswith(".json") and fn[:-5] not in years:
            os.remove(os.path.join(IDX_DIR, fn))
    cur_year = str(datetime.now().year)
    meta = {"v": 3, "updated": index.get("updated", ""), "count": total,
            "shards": index.get("shards", 0), "total_available": index.get("total_available", 0),
            "sources": ["News in Levels"], "years": dict(sorted(years.items(), reverse=True))}
    with open(index_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, separators=(",", ":"))
    size += os.path.getsize(index_path)
    cur_fp = os.path.join(IDX_DIR, "%s.json" % cur_year)
    return total, size, (os.path.getsize(cur_fp) if os.path.exists(cur_fp) else 0)


def main():
    limit = 0
    for a in sys.argv[1:]:
        if a.startswith("--limit"):
            limit = int(a.split("=")[1]) if "=" in a else int(sys.argv[sys.argv.index(a) + 1])
    os.makedirs(SHARD_DIR, exist_ok=True)

    index_path = os.path.join(LIB, "index.json")
    index = load_index(index_path)
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
    if not pairs:
        print("sitemap unavailable, abort without touching data")
        sys.exit(3)
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
    touched = set()
    with ThreadPoolExecutor(max_workers=int(os.environ.get("LIB_WORKERS", "3"))) as ex:
        for it in ex.map(lambda u: _throttled(u, vocab), todo):
            if not it:
                continue
            got += 1
            body = {k: it[k] for k in ("id", "text", "key")}
            shard_no = len(index["items"]) // SHARD_SIZE
            it["shard"] = shard_no
            index["items"].append({"id": it["id"], "level": it.get("level", 1), "date": it.get("date", ""),
                                   "words": it.get("words", 0), "shard": shard_no, "kw": it.get("kw") or []})
            shards.setdefault(shard_no, {"v": 1, "items": []})["items"].append(body)
            touched.add(shard_no)
            if len(shards[shard_no]["items"]) >= SHARD_SIZE:
                shard_no += 1
    print("fetched:", got, "in", round(time.time() - t0, 1), "s")

    # 默认增量模式：只写本次新增的分片，不重排不删除，避免每天把 255 个分片全量重写
    # （每次约 21MB）导致仓库体积暴涨。需要按日期重排整体分片时，用 LIB_REPACK=1 或 --repack。
    # 前端已按日期倒序排序，因此索引顺序不再影响首屏最新。
    repack = os.environ.get("LIB_REPACK", "0") == "1" or "--repack" in sys.argv
    if not repack:
        for no in sorted(touched):
            with open(os.path.join(SHARD_DIR, "%02d.json" % no), "w", encoding="utf-8") as f:
                json.dump(shards[no], f, ensure_ascii=False, separators=(",", ":"))
        now = datetime.now(timezone.utc)
        index["updated"] = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        index["count"] = len(index["items"])
        index["shards"] = len([k for k, v in shards.items() if v["items"]])
        index["total_available"] = len(seen_topic) * 3
        if not got:
            print("no new articles, index untouched:", index["count"], "items /", index["shards"], "shards")
            return
        n, size, cur = write_index(index, index_path)
        print("wrote (incremental) index:", n, "items /", index["shards"], "shards, touched shards:",
              sorted(touched), "| total", size, "bytes | daily delta ~", cur, "bytes")
        return

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
    n, size, cur = write_index(index, index_path)
    print("wrote index:", n, "items /", len(new_shards), "shards, available:", index["total_available"],
          "| total", size, "bytes | daily delta ~", cur, "bytes")
    for it in index["items"][:5]:
        print("  ", it["date"], it["level"], it["id"], "|", it["words"], "words")


if __name__ == "__main__":
    main()
