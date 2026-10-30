#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""EngLab 每日素材抓取：News in Levels / Breaking News English (+ VOA 尝试)
输出 data/daily.json（单文件站点直接 fetch），并留档 data/archive/YYYY-MM-DD.json
仅用标准库，供 GitHub Actions 每日定时执行。"""
import json, os, re, html, sys, time, urllib.request, urllib.error
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
ARCH = os.path.join(DATA, "archive")
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
      "Accept-Language": "en-US,en;q=0.9"}

def fetch(url, timeout=25, tries=2):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8", "ignore")
        except Exception as e:
            last = e
            time.sleep(1.2)
    print("  fetch fail:", url, type(last).__name__, str(last)[:80])
    return ""

def strip_tags(s):
    s = re.sub(r"(?is)<(script|style).*?</\1>", " ", s or "")
    s = re.sub(r"(?s)<br\s*/?>", " ", s)
    s = re.sub(r"(?s)<[^>]+>", " ", s)
    s = html.unescape(s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()

def words_of(t):
    return re.findall(r"[A-Za-z][A-Za-z'-]+", t)

def load_vocab():
    p = os.path.join(ROOT, "voice-pack.json")
    try:
        with open(p, encoding="utf-8") as f:
            j = json.load(f)
        return set(k.lower() for k in (j.get("d") or {}).keys())
    except Exception as e:
        print("vocab load fail:", e)
        return set()

def key_words(text, vocab, extra=None):
    out, seen = [], set()
    def add(w):
        w = w.strip().strip(".,;:!?\"'()").lower()
        if len(w) > 2 and w not in seen and w.isascii():
            seen.add(w); out.append(w)
    for w in (extra or []):
        add(w)
    ws = words_of(text)
    for w in ws:
        if w.lower() in vocab:
            add(w)
    # 补充长词作为重点词（前端按自身词库判断释义，未收录则给查词入口）
    for w in ws:
        if len(out) >= 14:
            break
        if len(w) >= 8 and w.lower() not in vocab:
            add(w)
    return out[:14]

def clean_title(t):
    t = strip_tags(t)
    t = re.sub(r"\s*[–\-]\s*level\s*\d+\s*$", "", t, flags=re.I)
    return t.strip()

# ---------- News in Levels ----------
def parse_nil(vocab):
    items = []
    xml = fetch("https://www.newsinlevels.com/feed/")
    if not xml:
        return items
    for m in re.finditer(r"(?s)<item>(.*?)</item>", xml):
        it = m.group(1)
        title_raw = (re.search(r"(?s)<title>(.*?)</title>", it) or [None, ""])[1]
        title = strip_tags(title_raw.replace("<![CDATA[", "").replace("]]>", ""))
        lvl_m = re.search(r"level\s*(\d)", title, re.I)
        if not lvl_m:
            continue
        level = int(lvl_m.group(1))
        if level > 3:
            continue
        link = strip_tags((re.search(r"(?s)<link>(.*?)</link>", it) or [None, ""])[1])
        pub = strip_tags((re.search(r"(?s)<pubDate>(.*?)</pubDate>", it) or [None, ""])[1])
        body = (re.search(r"(?s)<content:encoded>(.*?)</content:encoded>", it) or [None, ""])[1]
        strongs = [strip_tags(x) for x in re.findall(r"(?is)<strong>(.*?)</strong>", body)]
        text = strip_tags(body)
        text = re.sub(r"^\d{1,2}-\d{1,2}-\d{4}\s+\d{1,2}:\d{2}\s*", "", text)
        text = re.sub(r"The post .*? appeared first on .*?$", "", text).strip()
        if len(text) < 80:
            continue
        try:
            dt = datetime.strptime(pub, "%a, %d %b %Y %H:%M:%S %z").astimezone(timezone.utc)
            date = dt.strftime("%Y-%m-%d")
        except Exception:
            date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        items.append({
            "id": "nil-" + re.sub(r"[^a-z0-9]+", "-", clean_title(title).lower())[:40] + "-l%d" % level,
            "src": "News in Levels", "level": level,
            "title": clean_title(title), "date": date,
            "url": link, "audio": "",
            "text": text[:2600],
            "key": key_words(text, vocab, strongs),
            "words": len(words_of(text)),
        })
    # 取最近 2 个话题，每个话题保留 level 1/2/3 三档，供前端切换难度
    items.sort(key=lambda x: x["date"], reverse=True)
    topics, picked = [], []
    for it in items:
        if it["title"] not in topics:
            if len(topics) >= 2:
                continue
            topics.append(it["title"])
        picked.append(it)
    picked.sort(key=lambda x: (x["date"], x["title"], x["level"]), reverse=True)
    return picked

# ---------- Breaking News English ----------
def parse_bne(vocab):
    items = []
    xml = fetch("https://breakingnewsenglish.com/rss.xml")
    if not xml:
        return items
    for m in list(re.finditer(r"(?s)<item>(.*?)</item>", xml))[:6]:
        it = m.group(1)
        title = strip_tags((re.search(r"(?s)<title>(.*?)</title>", it) or [None, ""])[1])
        link = strip_tags((re.search(r"(?s)<link>(.*?)</link>", it) or [None, ""])[1])
        pub = strip_tags((re.search(r"(?s)<pubDate>(.*?)</pubDate>", it) or [None, ""])[1])
        audio = (re.search(r'enclosure[^>]*url="([^"]+)"', it) or [None, ""])[1]
        if not link or "/2610/" not in link and "/26" not in link:
            continue
        try:
            dt = datetime.strptime(pub, "%a, %d %b %Y %H:%M:%S %Z").replace(tzinfo=timezone.utc)
            date = dt.strftime("%Y-%m-%d")
        except Exception:
            date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        page = fetch(link, timeout=30)
        text = ""
        if page:
            body = re.sub(r"(?is)<(script|style).*?</\1>", " ", page)
            paras = re.findall(r"(?is)<p[^>]*>(.*?)</p>", body)
            cand = [strip_tags(p) for p in paras]
            cand = [c for c in cand if 120 <= len(c) <= 2600 and c.count(".") >= 2 and not c.lower().startswith(("home", "print", "listen"))]
            if cand:
                text = " ".join(cand[:3])
        if len(text) < 120:
            desc = strip_tags((re.search(r"(?s)<description>(.*?)</description>", it) or [None, ""])[1])
            text = desc
        if len(text) < 120:
            continue
        items.append({
            "id": "bne-" + re.sub(r"[^a-z0-9]+", "-", title.lower())[:40],
            "src": "Breaking News English", "level": 3,
            "title": title, "date": date,
            "url": link, "audio": audio,
            "text": text[:2600],
            "key": key_words(text, vocab),
            "words": len(words_of(text)),
        })
    return items[:4]

# ---------- VOA (可选源，网络不通时自动跳过) ----------
def parse_voa(vocab):
    items = []
    for feed in ("https://learningenglish.voanews.com/api/zq$omekvi_",
                 "https://learningenglish.voanews.com/api/z$mqomt-vomx-tpeqy_"):
        xml = fetch(feed, timeout=20, tries=1)
        if not xml or "<item" not in xml:
            continue
        for m in list(re.finditer(r"(?s)<item>(.*?)</item>", xml))[:5]:
            it = m.group(1)
            title = strip_tags((re.search(r"(?s)<title>(.*?)</title>", it) or [None, ""])[1])
            link = strip_tags((re.search(r"(?s)<link>(.*?)</link>", it) or [None, ""])[1])
            if not link.startswith("http"):
                continue
            desc = strip_tags((re.search(r"(?s)<description>(.*?)</description>", it) or [None, ""])[1])
            if len(desc) < 150:
                continue
            items.append({
                "id": "voa-" + re.sub(r"[^a-z0-9]+", "-", title.lower())[:40],
                "src": "VOA Learning English", "level": 2,
                "title": title, "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                "url": link, "audio": "",
                "text": desc[:2600],
                "key": key_words(desc, vocab),
                "words": len(words_of(desc)),
            })
        if items:
            break
    return items[:3]

def main():
    os.makedirs(ARCH, exist_ok=True)
    vocab = load_vocab()
    all_items = []
    seen_titles = set()
    for fn in (parse_nil, parse_bne, parse_voa):
        try:
            got = fn(vocab)
        except Exception as e:
            print("parser fail", fn.__name__, e)
            got = []
        print(fn.__name__, "->", len(got))
        for g in got:
            k = g["id"]
            if k in seen_titles:
                continue
            seen_titles.add(k)
            all_items.append(g)
    now = datetime.now(timezone.utc)
    payload = {"v": 1,
               "updated": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
               "generated": now.strftime("%Y-%m-%d"),
               "count": len(all_items),
               "items": all_items}
    old_items = []
    try:
        with open(os.path.join(DATA, "daily.json"), encoding="utf-8") as f:
            old = json.load(f)
        old_items = old.get("items") or []
    except Exception:
        pass
    # 合并：新条目优先，旧条目作为断网/抓取失败时的历史素材补齐（最多 18 条）
    merged, ids = [], set()
    for it in all_items + old_items:
        if it.get("id") in ids:
            continue
        ids.add(it.get("id"))
        merged.append(it)
    payload["items"] = merged[:18]
    payload["count"] = len(payload["items"])
    out = os.path.join(DATA, "daily.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    with open(os.path.join(ARCH, now.strftime("%Y-%m-%d") + ".json"), "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    print("wrote", out, os.path.getsize(out), "bytes, items:", payload["count"])
    for it in payload["items"][:8]:
        print("  ", it["src"], it["level"], it["date"], it["title"][:50], "|", it["words"], "words")

if __name__ == "__main__":
    main()
    # 每日顺带做素材库增量扩容：让「素材库」的资源随天数持续增长，
    # 不依赖单独的 workflow（失败不影响当日素材写入）
    try:
        import subprocess, sys
        limit = os.environ.get("LIBRARY_LIMIT", "300")
        print("--- library top-up, limit =", limit, "---")
        r = subprocess.run([sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                         "fetch_library.py"), "--limit=" + limit],
                           timeout=600)
        print("library top-up exit:", r.returncode)
    except Exception as e:
        print("library top-up skipped:", e)
