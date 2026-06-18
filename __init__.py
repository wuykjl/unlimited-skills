"""Skills-catalog router - keyword match, score rank, LLM reason, user confirm.

Architecture:
  Phase 0: Detect user confirmation on prior recommendation -> inject execute context
  Phase 1: Task gate heuristic -> skip greetings / casual talk
  Phase 2: Score-rank keyword match (coverage + specificity) -> inject Top-6
  Phase 2b: Fallback when no keyword match -> inject limited skill list (Top-25)
  Phase 3: LLM semantically reasons best fit from injected context
  Phase 4: User confirms -> execute the skill

No BM25, no trigger-index, no ngram, no Levenshtein, no external LLM classify.
Zero external dependencies. Single md file for catalog maintenance.
"""

import os, re, threading
from pathlib import Path

_CATALOG = None
_CATALOG_LOCK = threading.Lock()
_last_recommendation = None
_last_lock = threading.Lock()

# -- Catalog --

def _get_catalog_path():
    plugin_dir = Path(__file__).parent
    for c in [plugin_dir / "skills-catalog.md", Path.cwd() / "skills-catalog.md"]:
        if c.exists(): return str(c)
    return str(plugin_dir / "skills-catalog.md")

def _load_catalog():
    global _CATALOG
    p = _get_catalog_path()
    if not os.path.isfile(p): _CATALOG = []; return
    with open(p, encoding="utf-8", errors="ignore") as f:
        text = f.read()
    skills = []
    for line in text.split("\n"):
        line = line.strip()
        if line.startswith("|") and line.endswith("|") and not line.startswith("|---"):
            parts = [p.strip() for p in line.split("|")[1:-1]]
            if len(parts) >= 3:
                name, desc, kw_str = parts[0], parts[1], parts[2]
                # Skip header row and invalid entries (placeholder desc like "\")
                if not name or name.startswith("技能名"): continue
                if not desc or desc.strip() in ("", "\\", "-", ">", ">-"): continue
                keywords = [k.strip().lower() for k in kw_str.split(",") if k.strip()]
                # Pre-compute keyword token set for O(1) matching
                kw_tokens = frozenset(keywords)
                skills.append({
                    "name": name, "desc": desc,
                    "keywords": keywords,
                    "kw_tokens": kw_tokens,
                })
    _CATALOG = skills

# -- Keyword matching --

STOP = {"the","this","that","with","from","have","been","what","when",
        "where","which","about","than","then","just","also","very",
        "and","for","not","are","but","can","all","has","was","use",
        "how","why","who","its","into","over","such","each","one",
        "to","is","it","as","at","by","on","do","up","if","or","no",
        # Chinese common stop words - filter out noise from CN bigrams
        "一个","这个","那个","什么","怎么","如何","可以","需要",
        "我们","你们","他们","已经","没有","还是","因为","所以",
        "但是","如果","虽然","而且","或者","只是","就是","不是",
        "所有","这些","那些","那里","这里","哪个","哪些",
        # Additional Chinese noise bigrams from real-query evaluation
        "有没有","好不好","能不能","要不要","会不会","是不是",
        "怎么样","怎么做","做什么","在哪里","什么时","什么时候",
        "不知道","看一下","看看","了解","推荐","求推荐",
        "各位","大家","请教","请问","新人","小白"}

def _tokenize_query(text):
    """Overlapping CN bigrams/trigrams + English words."""
    lower = text.lower().strip()
    tokens = set()
    for m in re.findall(r"[a-z0-9]{2,}", lower):
        if m not in STOP: tokens.add(m)
    # Chinese: sliding window 2-3 gram, filtered by STOP
    cn_chars = [c for c in lower if "一" <= c <= "鿿"]
    for i in range(len(cn_chars) - 1):
        b = cn_chars[i] + cn_chars[i+1]
        if b not in STOP:
            tokens.add(b)
        if i + 2 < len(cn_chars):
            t = cn_chars[i] + cn_chars[i+1] + cn_chars[i+2]
            if t not in STOP:
                tokens.add(t)
    return tokens


def _keyword_match(query):
    """Score-ranked keyword match. Returns list of matched skill dicts."""
    if not _CATALOG: return []
    qt = _tokenize_query(query)
    if not qt: return []
    qt_len = len(qt)
    min_match = 2 if qt_len >= 6 else 1  # longer queries need more evidence

    matched = []
    for skill in _CATALOG:
        common = qt & skill["kw_tokens"]
        if not common:
            continue
        c = len(common)
        if c < min_match:
            continue
        # Coverage: how much of the query tokens are matched (recall)
        coverage = c / max(1, qt_len)
        # Specificity: how focused this skill is on the matched terms (precision)
        specificity = c / max(1, len(skill["keywords"]))
        # Combined score, weighted toward coverage
        score = coverage * 0.65 + specificity * 0.35

        matched.append({
            "name": skill["name"],
            "desc": skill["desc"],
            "matched_keywords": sorted(common),
            "score": round(score, 4),
        })

    matched.sort(key=lambda x: -x["score"])  # best first
    return matched

# -- Confirmation --

_CONFIRM = {"确认","好的","好","是","嗯","对","行","可以","执行","用",
            "yes","ok","okay","sure","yeah","go","do it","go ahead",
            "用这个","就用","确认执行","开始"}

def _is_confirmation(text):
    if _last_recommendation is None: return False
    tl = text.strip().lower()
    if tl in _CONFIRM: return True
    for rn in _last_recommendation.get("skills", []):
        # Only match when user explicitly names the skill
        if rn.lower() in tl:
            return True
    return False

# -- Task gate --

_TASK_STARTERS = ("fix","debug","build","run","test","deploy","make",
    "write","create","show","list","find","check","scan",
    "help","audit","review","refactor","optimize","migrate",
    "帮我","给我","我要","写一个","实现","搭建",
    "修复","优化","检查","审查","设计","配置",
    "分析","研究","调试","设置")

_NON_TASK = ("hi","hello","hey","thanks","thank","good","great",
    "yes","no","ok","okay","sure",
    "what","why","who","when","where","how come",
    "真的吗","好的","明白了","是的","为什么","继续","然后呢",
    "tell me more","go on","proceed","continue")

_TECH = {"fastapi","springboot","django","kubernetes","docker",
    "react","vue","angular","node","python","golang",
    "rust","swift","kotlin","java","typescript",
    "postgres","mysql","redis","mongodb","graphql",
    "owasp","api","sql","cicd","terraform",
    "performance","security","database","testing","deploy",
    "dockerfile","migration","schema","container","spring","springboot"}

def _is_task_query(text):
    t = text.strip().lower()
    if len(t) <= 2: return False
    # Tokenize: English words + Chinese chars
    words = re.findall(r"[a-z0-9]+|[一-鿿]+", t)
    if not words: return False
    first = words[0]
    # English "why/what/how" question with tech context -> let through
    if first in ("what", "why", "how"):
        if any(kw in t for kw in _TECH):
            pass  # has tech context -> don't block
        else:
            if first in _NON_TASK or t in _NON_TASK: return False
    else:
        # Acknowledgment
        if first in _NON_TASK or t in _NON_TASK: return False
    # Task verb starter
    if first in _TASK_STARTERS: return True
    if any(t.startswith(p) for p in _TASK_STARTERS): return True
    # Chinese task chars (single char) - check all chars, not just first 3
    cn_chars = re.findall(r"[一-鿿]", t)
    cn_task_single = {"写","审","检","部",
                      "搭","配","设","优",
                      "分","用"}
    # (write/audit/check/deploy/design/optimize/analyze/use)
    if any(c in cn_task_single for c in cn_chars[:3]): return True
    # Chinese overlapping bigrams from the full text
    cn_bigrams = set()
    for i in range(len(t)-1):
        if '一' <= t[i] <= '鿿' and '一' <= t[i+1] <= '鿿':
            cn_bigrams.add(t[i:i+2])
    cn_task_bi = {"数据","网站","性能",
                  "检查","审查","部署","配置","优化","分析",
                  "搭建","修复","调试","设计","研究","编写",
                  "安全","漏洞","扫描","代码"}
    if cn_bigrams & cn_task_bi: return True
    # Question pattern -> non-task (with how-to exception)
    qw = {"什么","为什么","谁","什么时候","哪里","怎么","如何",
          "有没有","是不是","能不能","会不会","吗","怎么样"}
    if first in qw:
        # "如何/怎么 X" with >2 words -> how-to query, let through
        if first in ("如何", "怎么", "怎样") and len(words) >= 3:
            pass
        else:
            return False
    if t.endswith("吗") or t.endswith("?") or t.endswith("？"): return False
    # Tech keyword in text
    if any(kw in t for kw in _TECH): return True
    # 4+ tokens -> assume task
    if len(words) >= 4: return True
    return False

# -- Context formatters --

def _fmt_recommend(matched):
    lines = ["[技能路由] 根据关键词匹配，以下技能可能与您的请求相关（按匹配度排序）：", ""]
    for i, m in enumerate(matched, 1):
        kw_str = ", ".join(m["matched_keywords"][:5])
        score_pct = int(m["score"] * 100)
        lines.append(f"  {i}. {m['name']} [{score_pct}%]: {m['desc']}")
        lines.append(f"     匹配词: {kw_str}")
        lines.append("")
    lines.append("分析用户请求：如果某个技能匹配，列出选项让用户选择。如果1个且明确，直接推荐并询问是否确认。")
    lines.append("若无技能匹配，忽略此列表，直接用自身能力处理请求。")
    return {"context": "\n".join(lines)}

def _fmt_fallback():
    if not _CATALOG: return {}
    total = len(_CATALOG)
    show_n = min(total, 25)
    lines = ["[技能路由] 未找到精确匹配，以下可用技能（共{}个）：".format(total), ""]
    for i, s in enumerate(_CATALOG[:show_n], 1):
        d = s["desc"][:55] + "..." if len(s["desc"]) > 55 else s["desc"]
        lines.append(f"  {i}. {s['name']}: {d}")
    if total > show_n:
        names = ", ".join(s["name"] for s in _CATALOG[show_n:])
        lines.append(f"  ... {names}")
    lines.append("")
    lines.append("分析用户请求：如果某个技能匹配，列出选项让用户选择。如无匹配，忽略此列表，直接用自身能力处理。")
    return {"context": "\n".join(lines)}

def _fmt_execute():
    if _last_recommendation is None: return {}
    skill_names = _last_recommendation.get("skills", [])
    top = skill_names[0] if skill_names else ""
    lines = [
        "[用户确认] 用户确认请求。",
        "",
        "检查推荐技能【{}】是否真正匹配用户请求：".format(top),
        "  - 匹配 → 加载并执行该技能",
        "  - 不匹配 → 忽略技能推荐，直接用自己的能力完成用户请求",
    ]
    return {"context": "\n".join(lines)}

# -- Hooks --

def on_session_start(**kwargs):
    global _CATALOG
    with _CATALOG_LOCK:
        if _CATALOG is not None: return
        _load_catalog()

def pre_llm_call(**kwargs) -> dict:
    global _last_recommendation
    msg = kwargs.get("user_message", "")
    if not msg or not msg.strip(): return {}
    if _CATALOG is None: on_session_start()
    if not _CATALOG: return {}

    # Phase 0: User confirmation?
    if _is_confirmation(msg):
        with _last_lock:
            if _last_recommendation:
                return _fmt_execute()

    # Phase 1: Task gate
    if not _is_task_query(msg):
        return {}

    # Phase 2: Keyword match
    matched = _keyword_match(msg)
    if matched:
        with _last_lock:
            _last_recommendation = {"query": msg, "skills": [m["name"] for m in matched], "count": len(matched)}
        return _fmt_recommend(matched[:6])
    else:
        return _fmt_fallback()
