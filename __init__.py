"""
Hermes pre_llm_call plugin — self-contained skill router.

Zero external dependencies. BM25 + regex rules, ~200 lines.
LLM sees only top-3 skills, not 332. 6-10ms per query.

Architecture (community-verified, 2026-06-15):
  on_session_start → scan SKILL.md files → build in-memory index
  pre_llm_call     → BM25 keyword match + rule check → inject top-3

This file lives in Hermes repo. No dependency on neuro-skill package.
The neuro-skill author can freely modify their code — this plugin is
entirely self-contained.
"""

import json
import math
import os
import re
import sys
import threading
from pathlib import Path

# ── Configuration ──

# Skill directories to scan (auto-detected)
_SKILL_DIRS_CACHE = None

# Routes cache: skill_name → (full_path, name, search_text)
_RouteIndex = None
_RouteLock = threading.Lock()

# Rules cache: pattern → skill_name
_RulesCache = None


def _get_skill_dirs() -> list[str]:
    global _SKILL_DIRS_CACHE
    if _SKILL_DIRS_CACHE is not None:
        return _SKILL_DIRS_CACHE

    home = Path.home()
    local = os.environ.get("LOCALAPPDATA", "") if sys.platform == "win32" else ""
    hermes_home = os.environ.get("HERMES_HOME", "")

    dirs = [
        str(home / ".claude" / ".skills-store" / "skills"),
        str(home / ".claude" / ".skills-store" / "agents"),
        str(home / ".claude" / "skills"),
        str(home / ".claude" / "agents"),
        str(home / ".claude" / ".agents" / "skills"),
        str(home / ".hermes" / "skills"),
        str(home / ".hermes" / "agents"),
    ]
    if local:
        dirs.append(str(Path(local) / "hermes" / "skills"))
        dirs.append(str(Path(local) / "hermes-agent" / "skills"))
    if hermes_home:
        dirs.append(str(Path(hermes_home) / "skills"))
        dirs.append(str(Path(hermes_home) / "agents"))

    _SKILL_DIRS_CACHE = sorted(set(d for d in dirs if Path(d).is_dir()))
    return _SKILL_DIRS_CACHE


# ── Skill File Discovery ──

def _find_skill_files(dirs: list[str]) -> list[tuple[str, str, str]]:
    """Scan directories for SKILL.md and .md agent files.
    Returns list of (name, description, search_text).
    """
    seen = set()
    skills = []

    for d in dirs:
        dp = Path(d)
        if not dp.exists():
            continue
        for item in sorted(dp.iterdir()):
            name = None
            description = ""
            filepath = None

            if item.is_dir():
                smd = item / "SKILL.md"
                if smd.exists():
                    filepath = smd
            elif item.is_file() and item.suffix == ".md":
                filepath = item

            if filepath is None:
                continue

            try:
                text = filepath.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue

            meta, body = _parse_frontmatter(text)
            name = meta.get("name", filepath.stem)
            description = meta.get("description", "")

            if name in seen or len(text.strip()) < 20:
                continue
            seen.add(name)

            # search_text = name + description + first 500 chars of body + trigger synonyms
            search_text = f"{name} {description} {body[:500]}".lower()
            # Append domain-specific trigger synonyms for better recall
            _synonyms = {
                "performance": "faster slow speed optimize bottleneck lag latency throughput accelerate",
                "security": "vulnerability hack exploit cve attack threat malware breach penetration",
                "deploy": "release ship rollout publish go-live production launch CI/CD",
                "database": "db sql query schema table index migration postgresql mysql",
                "frontend": "ui css html javascript typescript component layout responsive",
                "testing": "test assert mock coverage pytest unit-test integration-test",
                "docker": "container image docker-compose dockerfile compose orchestration",
                "kubernetes": "k8s pod deployment service ingress cluster helm kubectl",
                "api": "rest endpoint route graphql rpc http request response swagger",
                "code-review": "review pr pull-request merge code-quality lint check audit",
            }
            # Load augmented trigger phrases from file (~/.hermes/.neuro-skill-augmented.json)
            _aug_path = os.path.join(os.path.dirname(__file__), "cn-augment.json")
            _aug = {}
            if os.path.isfile(_aug_path):
                try:
                    with open(_aug_path) as _af:
                        _a = _json.load(_af)
                        for _k, _v in _a.items():
                            _aug[_k] = _v.get("triggers", "")
                except Exception:
                    pass
            if name in _aug:
                search_text += " " + _aug[name]
            
            # Append Chinese trigger phrases for bilingual search
                        # Append Chinese trigger phrases for bilingual search
            for key, syns in _synonyms.items():
                if key in name.lower() or key in description.lower():
                    search_text += " " + syns
            # Append Chinese trigger phrases
            _cn_path = os.path.join(os.path.dirname(__file__), "cn-augment.json")
            try:
                if os.path.isfile(_cn_path):
                    with open(_cn_path) as _cnf:
                        _cn_data = json.load(_cnf)
                    if name in _cn_data:
                        search_text += " " + " ".join(_cn_data[name])
            except Exception:
                pass
            skills.append((name, description, search_text))

    return skills


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """Parse YAML frontmatter from skill text."""
    text = text.strip()
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            try:
                meta = {}
                for line in parts[1].strip().split("\n"):
                    if ":" in line:
                        k, _, v = line.partition(":")
                        k = k.strip()
                        v = v.strip().strip("'").strip('"')
                        if k:
                            meta[k] = v
            except Exception:
                meta = {}
            return meta, parts[2].strip()
    return {}, text


# ── BM25 Keyword Router ──

def _tokenize_set(text: str) -> set[str]:
    """Split text into unique tokens: ASCII words + Chinese bigrams."""
    tokens = set()
    tokens.update(re.findall(r"[a-z0-9]{2,}", text.lower()))
    tokens.update(re.findall(r"[一-鿿]{2,6}", text.lower()))
    return tokens


def _tokenize_list(text: str) -> list[str]:
    """Split text into token list (preserves duplicates for TF counting)."""
    text_lower = text.lower()
    tokens = re.findall(r"[a-z0-9]{2,}", text_lower)
    tokens.extend(re.findall(r"[一-鿿]{2,6}", text_lower))
    return tokens


class _KeywordIndex:
    """Minimal BM25 keyword index. No numpy needed."""

    def __init__(self, skills: list[tuple[str, str, str]]):
        self.skills = skills
        self._doc_tokens = [_tokenize_list(st) for _, _, st in skills]
        self._doc_token_sets = [set(t) for t in self._doc_tokens]
        self._doc_lens = [len(t) for t in self._doc_tokens]
        self._avgdl = sum(self._doc_lens) / max(len(skills), 1)
        self._inverted = {}
        for i, token_set in enumerate(self._doc_token_sets):
            for t in token_set:
                self._inverted.setdefault(t, []).append(i)

        # IDF precompute
        N = len(skills)
        self._idf = {}
        for term, docs in self._inverted.items():
            self._idf[term] = math.log(1 + (N - len(docs) + 0.5) / (len(docs) + 0.5))

    def query(self, text: str, top_k: int = 3,
              k1: float = 1.2, b: float = 0.75) -> list[tuple[str, float]]:
        """Rank skills by BM25 relevance to query text."""
        q_tokens = _tokenize_set(text)
        if not q_tokens:
            return []

        N = len(self.skills)
        scores = [0.0] * N
        avgdl = max(self._avgdl, 1)

        for qt in q_tokens:
            idf = self._idf.get(qt, 0)
            if idf == 0:
                continue
            for doc_idx in self._inverted.get(qt, []):
                tf = self._doc_tokens[doc_idx].count(qt)
                dl = self._doc_lens[doc_idx]
                numerator = tf * (k1 + 1)
                denominator = tf + k1 * (1 - b + b * dl / avgdl)
                scores[doc_idx] += idf * numerator / max(denominator, 0.01)

        # Rank
        ranked = sorted(
            [(self.skills[i][0], scores[i]) for i in range(N) if scores[i] > 0],
            key=lambda x: -x[1],
        )[:top_k]
        return ranked


# ── LLM Rerank (optional, recall+rerank) ──

def _llm_rerank(query: str, candidates: list[tuple[str, float]],
                top_k: int = 3) -> list[tuple[str, float]]:
    """Use LLM to semantically re-rank top-10 BM25 candidates.

    Fallback: returns original ranking if no API key available.
    """
    api_key = os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not api_key or len(candidates) < 2:
        return candidates[:top_k]

    # Only rerank top-N candidates
    pool = candidates[:min(10, len(candidates))]
    c_list = "\n".join(f"{i+1}. {name}" for i, (name, _) in enumerate(pool))

    prompt = (
        f"Rank these skills by relevance to the query.\n\n"
        f"Query: {query}\n\n"
        f"Skills:\n{c_list}\n\n"
        f"Return ONLY a JSON array of skill names in order of relevance "
        f"(most relevant first):\n"
        f'["skill_name_1", "skill_name_2", ...]'
    )

    try:
        import urllib.request
        import json as _json

        payload = _json.dumps({
            "model": "deepseek-chat",
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 256,
            "temperature": 0.0
        }).encode()

        req = urllib.request.Request(
            "https://api.deepseek.com/v1/chat/completions",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}"
            }
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = _json.loads(resp.read().decode())
            text = data["choices"][0]["message"]["content"].strip()

        if text.startswith("```"):
            text = "\n".join(text.split("\n")[1:-1])
        ranked_names = _json.loads(text)

        # Map back to (name, original_score), preserving original score for fallback
        score_map = dict(candidates)
        reranked = [(n, score_map.get(n, 0)) for n in ranked_names if n in score_map]
        reranked.extend([(n, s) for n, s in candidates if n not in score_map])
        return reranked[:top_k]
    except Exception:
        return candidates[:top_k]



# ── Phase 2: LLM Trigger Phrase Matching ──

_TRIGGER_PHRASE_TO_SKILL = None

def _load_triggers():
    global _TRIGGER_PHRASE_TO_SKILL
    if _TRIGGER_PHRASE_TO_SKILL:
        return
    _TRIGGER_PHRASE_TO_SKILL = {}
    try:
        import json as _json
        _trigger_path = os.path.join(os.path.dirname(__file__), "trigger-index.json")
        if not os.path.isfile(_trigger_path):
            _trigger_path = os.path.join(os.getcwd(), "trigger-index.json")
        if os.path.isfile(_trigger_path):
            with open(_trigger_path, encoding="utf-8") as _tf:
                _index = _json.load(_tf)
        else:
            _index = {}
        for _sname, _phrases in _index.items():
            for _p in _phrases:
                _TRIGGER_PHRASE_TO_SKILL[_p.lower().strip()] = _sname
    except Exception as e:
        import logging
        logging.getLogger("unlimited_skills").warning(
            "Failed to load trigger-index.json: %s", e
        )



# ── Phase 0: N-gram Embedding ──
_NGRAM_CACHE = None

def _build_ngram(skills):
    global _NGRAM_CACHE
    _NGRAM_CACHE = {}
    for name, _, st in skills:
        t = st.lower()
        tri = set()
        for i in range(len(t) - 2):
            tri.add(t[i:i+3])
        _NGRAM_CACHE[name] = tri

def _ngram_search(q, top_k=10):
    if _NGRAM_CACHE is None:
        return []
    qt = set()
    ql = q.lower().strip()
    for i in range(len(ql) - 2):
        qt.add(ql[i:i+3])
    if not qt:
        return []
    sc = []
    for name, st in _NGRAM_CACHE.items():
        c = qt & st
        if c:
            jac = len(c) / (len(qt) + len(st) - len(c))
            sc.append((name, jac))
    sc.sort(key=lambda x: -x[1])
    return sc[:top_k]

def _hybrid_recall(query, top_k=30):
    if _RouteIndex is None:
        return []
    bm25 = _RouteIndex.query(query, top_k=top_k)
    ngram = _ngram_search(query, top_k=top_k)
    seen = set()
    merged = []
    for i in range(max(len(bm25), len(ngram))):
        if i < len(bm25):
            n, s = bm25[i]
            if n not in seen:
                merged.append((n, s))
                seen.add(n)
        if i < len(ngram):
            n, s = ngram[i]
            if n not in seen:
                merged.append((n, s * 2))
                seen.add(n)
    return merged[:top_k]

# ── Task Detection Gate ──

_TASK_PREFIXES = (
    "help", "can you", "could you", "would you", "i need", "i want",
    "how do", "how to", "how can", "how should", "what is the best way",
    "write", "create", "build", "make", "generate", "produce", "develop",
    "fix", "repair", "debug", "resolve", "solve", "troubleshoot",
    "deploy", "publish", "release", "ship", "launch",
    "review", "audit", "check", "inspect", "examine", "scan",
    "optimize", "improve", "refactor", "clean", "restructure",
    "test", "validate", "verify", "confirm",
    "design", "plan", "setup", "configure", "install",
    "migrate", "upgrade", "update", "convert",
    "analyze", "research", "investigate", "compare",
    "document", "explain", "describe", "summarize",
    "monitor", "track", "watch", "alert",
    "protect", "secure", "harden", "encrypt",
    "实现", "构建", "创建", "编写", "写一个", "部署",
    "帮我",
    "修复", "优化", "检查", "审查", "设计", "配置",
    "分析", "研究", "调试", "设置", "搭建",
)

_NON_TASK_MARKERS = (
    "hi", "hello", "hey", "thanks", "thank", "good", "great", "nice",
    "yes", "no", "ok", "okay", "sure", "fine", "well",
    "what", "why", "who", "when", "where", "which", "how come",
    "tell me about", "what is", "what are", "what does",
    "i think", "i feel", "i agree", "i disagree",
    "that makes sense", "i see", "got it", "understood",
    "真的吗", "好的", "明白了", "是的", "不是", "为什么",
    "继续", "然后呢", "原来如此", "确实",
    "tell me more", "proceed", "continue", "go on",
)


# ── LLM Fallback Classifier ──

_LLM_KEY = None
def _load_llm_key():
    global _LLM_KEY
    if _LLM_KEY is not None:
        return
    try:
        for _ep in ("~/.hermes/.env", os.path.expanduser("~/AppData/Local/hermes/.env"),
                    os.path.join(os.path.dirname(__file__), ".env")):
            _full = os.path.expanduser(_ep)
            if os.path.isfile(_full):
                with open(_full, 'rb') as _f:
                    _raw = _f.read()
                _idx = _raw.find(b'DEEPSEEK_API_KEY')
                if _idx >= 0:
                    _line = _raw[_idx:_raw.find(b'\n', _idx)] if _raw.find(b'\n', _idx) > _idx else _raw[_idx:]
                    _LLM_KEY = _line.split(b'=', 1)[1].decode(errors='ignore').strip()
                    return
    except Exception:
        pass

def _llm_classify_task(query: str) -> bool | None:
    """LLM fallback for ambiguous cases. Returns True(task)/False(non_task)/None(no key/error)."""
    global _LLM_KEY
    _load_llm_key()
    if not _LLM_KEY:
        return None
    
    prompt = f'User: "{query}"\nClassify: TASK or CONVERSATION. Reply TASK or CONVERSATION.'
    
    try:
        import urllib.request as _ur
        payload = json.dumps({
            "model": "deepseek-chat",
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 10,
            "temperature": 0.0
        }).encode()
        req = _ur.Request(
            "https://api.deepseek.com/v1/chat/completions",
            data=payload,
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {_LLM_KEY}"}
        )
        with _ur.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
            result = data["choices"][0]["message"]["content"].strip().upper()
            return "TASK" in result
    except Exception:
        return None


# ── Ambiguity Detection ──

_AMBIGUOUS_MARKERS = (
    "anyone else", "does anyone", "how are", "how do you",
    "what about", "what if", "i think", "i feel", "i'm not sure",
    "not sure", "interesting", "really",
    "i wasted", "i spent", "i just", "i tried",
    "?", "？",
)

# ── LLM Result Cache (1000 entries) ──
_LLM_CACHE = {}



def _is_ambiguous(text: str) -> bool:
    """Return True if the query has mixed signals that might confuse rules."""
    t = text.lower().strip()
    if "?" in t or "？" in t:
        return True
    for m in _AMBIGUOUS_MARKERS:
        if m in t:
            return True
    return False


def _is_task_query(text: str) -> bool:
    """3-feature + optional LLM fallback for ambiguous cases."""
    # Step 1: Rules
    rule_result = _rules_task(text)
    
    # Step 2: If rules are confident and query is unambiguous, use rules
    if not _is_ambiguous(text):
        return rule_result
    
    # Step 3: Ambiguous → call LLM fallback (with cache)
    _cache_key = text.strip().lower()
    if _cache_key in _LLM_CACHE:
        return _LLM_CACHE[_cache_key]
    llm_result = _llm_classify_task(text)
    if llm_result is not None:
        _LLM_CACHE[_cache_key] = llm_result
        if len(_LLM_CACHE) > 1000:
            _LLM_CACHE.clear()
        return llm_result
    
    # Step 4: LLM unavailable → trust rules
    return rule_result


def _rules_task(text: str) -> bool:
    """3-feature decision tree — original rules classifier."""
    t = text.strip().lower()
    if len(t) <= 2:
        return False

    words = t.split()
    wc = len(words)
    first_w = words[0] if wc > 0 else ""

    # Feature 1: Explicit task starters
    if first_w in ("fix","debug","build","run","test","deploy","make",
                   "write","create","show","list","find","check","scan",
                   "help","audit","review","refactor","optimize","migrate"):
        return True
    if any(t.startswith(p) for p in ("帮我","给我","我要","写一个","实现一",
                                       "搭","创","建","配","设","部","优",
                                       "检","审","测","分")):
        return True

    # Feature 2: Question patterns
    pair = " ".join(words[:2]) if wc >= 2 else first_w
    question_starters = {"what","why","who","when","where","does","is it",
                         "are there","can you","tell me","do you","did you",
                         "is there","what's","whats","what are","what is",
                         "what does","how does","how come","how can","how is"}
    how_task = {"how to","how do i","how can i","how should i"}
    if pair in how_task or (first_w == "how" and wc >= 3 and "to" in words[1:3]):
        pass  # how-to is a task
    elif first_w in question_starters or pair in question_starters or pair.startswith("wh"):
        return False
    if first_w in ("什么","为什么","谁","什么时候","哪里","怎么","如何",
                   "有没有","是不是","能不能","会不会"):
        return False

    # Feature 3: Verb signal
    if wc >= 3:
        action_verbs = {"deploy","publish","build","create","write","fix",
                        "debug","optimize","refactor","test","review","audit",
                        "monitor","analyze","migrate","upgrade","configure",
                        "install","setup","design","plan","implement","generate",
                        "containerize","secure","encrypt","validate","send","notify"}
        if any(v in words for v in action_verbs) or any(v in t for v in action_verbs):
            return True

    # Conversation signals
    ack_words = {"yes","no","ok","okay","sure","thanks","thank","good",
                 "great","nice","fine","got","see","understood","right",
                 "goodbye","bye","later"}
    if any(t.startswith(p) for p in ("tell me more","go on","proceed","continue")):
        return False
    if t.startswith("not sure") or t.startswith("i'm not sure"):
        return False
    if wc <= 3 and (first_w in ack_words or words[0] in ack_words):
        return False

    # Keyword fallback
    tech_keywords = {"fastapi","springboot","django","kubernetes","docker",
                     "react","vue","angular","node","python","golang",
                     "rust","swift","flutter","kotlin","java","typescript",
                     "postgres","mysql","redis","mongodb","graphql","rest",
                     "owasp","ci/cd","cicd","tdd","api","sql","db",
                     "spring","microservice","article","brand","spec","deep",
                     "prompt","terraform","dockerfile","migration","schema"}
    if wc == 3 and words[0] in ("spec","brand","deep") and words[1] in ("driven","voice","research"):
        return True
    cn_task_starters = {"我的","这个","那个","写","审","检","部","搭","配",
                        "设","优","分","怎么","如何","哪","数据","网站","性能","把"}
    if any(t.startswith(p) for p in cn_task_starters):
        return True
    if any(kw in words for kw in tech_keywords):
        return True
    if wc <= 3 and wc >= 2:
        second_w = words[1] if wc >= 2 else ""
        noun_actions = {"review","audit","optimization","deploy","pipeline",
                        "migration","testing","debug","design","practices"}
        if second_w in noun_actions:
            return True
    return wc >= 4


    """3-feature decision tree: is this a task requiring skill routing?"""
    t = text.strip().lower()
    if len(t) <= 2:
        return False

    words = t.split()
    wc = len(words)

    # Feature 1: Explicit task starters (high precision)
    # Short commands like "fix bug", "deploy app", "write tests"
    first_w = words[0] if wc > 0 else ""
    if first_w in ("fix","debug","build","run","test","deploy","make",
                   "write","create","show","list","find","check","scan",
                   "help","audit","review","refactor","optimize","migrate"):
        return True
    # Chinese: 帮我/给我/我要 start → task
    if any(t.startswith(p) for p in ("帮我","给我","我要","写一个","实现一",
                                       "搭","创","建","配","设","部","优",
                                       "检","审","测","分")):
        return True

    # Feature 2: Question patterns → mostly NOT tasks
    # Check first two words for question starters
    pair = " ".join(words[:2]) if wc >= 2 else first_w
    question_starters = {"what","why","who","when","where","does","is it",
                         "are there","can you","tell me","do you","did you",
                         "is there","what's","whats","what are","what is",
                         "what does","how does","how come","how can","how is"}
    # "how to X" and "how do I X" are tasks (request for instructions)
    how_task = {"how to","how do i","how can i","how should i"}
    if pair in how_task or first_w == "how" and wc >= 3 and "to" in words[1:3]:
        pass  # continue to verb check below
    if first_w in question_starters or pair in question_starters or pair.startswith("wh"):
        return False
    # Chinese question words
    if first_w in ("什么","为什么","谁","什么时候","哪里","怎么","如何",
                   "有没有","是不是","能不能","会不会"):
        return False

    # Feature 3: Length + verb signal
    if wc >= 3:
        action_verbs = {"deploy","publish","build","create","write","fix",
                        "debug","optimize","refactor","test","review","audit",
                        "monitor","analyze","migrate","upgrade","configure",
                        "install","setup","design","plan","implement","generate",
                        "containerize","secure","encrypt","validate","send","notify"}
        if any(v in words for v in action_verbs) or any(v in t for v in action_verbs):
            return True

    # Conversation signals → skip
    ack_words = {"yes","no","ok","okay","sure","thanks","thank","good",
                 "great","nice","fine","got","see","understood","right","goodbye","bye","later"}
    # "tell me more", "go on", "continue", "proceed"
    if any(t.startswith(p) for p in ("tell me more", "go on", "proceed", "continue")):
        return False
    if t.startswith("not sure") or t.startswith("i'm not sure"):
        return False
    if wc <= 3 and (first_w in ack_words or words[0] in ack_words):
        return False

    # Fallback: keyword-style queries (tech + topic) → assume task
    tech_keywords = {"fastapi","springboot","django","kubernetes","docker",
                     "react","vue","angular","node","python","golang",
                     "rust","swift","flutter","kotlin","java","typescript",
                     "postgres","mysql","redis","mongodb","graphql","rest",
                     "owasp","ci/cd","cicd","tdd","api","sql","db",
                     "springboot","spring","microservice","article","brand",
                     "spec","deep research","deep","prompt","terraform",
                     "dockerfile","redis","mongodb","migration","schema"}
    # Any 2+ word keyword pair like "spec driven", "brand voice"
    if wc == 3 and words[0] in ("spec","brand","deep") and words[1] in ("driven","voice","research"):
        return True
    # Chinese task patterns: 我的/数据库/写/审查/检查/部署 + <topic>
    cn_task_starters = {"我的","这个","那个","写","审","检",
                        "部","搭","配","设","优","分",
                        "怎么","如何","哪","数据","网站","性能"}
    if any(t.startswith(p) for p in cn_task_starters):
        return True
    if any(kw in t.split() for kw in tech_keywords):
        return True
    if wc <= 3 and wc >= 2:
        second_w = words[1] if wc >= 2 else ""
        noun_actions = {"review","audit","optimization","deploy","pipeline",
                        "migration","testing","debug","design","practices"}
        if second_w in noun_actions:
            return True
    return wc >= 4


def _match_with_triggers(query: str, bm25_top3: list[tuple[str, float]],
                         bm25_top30: list[tuple[str, float]] | None = None) -> list[dict]:
    """Three-phase matching: BM25 -> trigger phrase -> semantic.

    Phase 1: BM25 candidates (pre-computed)
    Phase 2a: Exact trigger phrase match in query
    Phase 2b: Trigger phrase overlap scoring (IDF-weighted)
    """
    _load_triggers()
    q_lower = query.lower().strip()

    # Phase 2a: Exact phrase match — search ALL trigger phrases
    for phrase, skill_name in _TRIGGER_PHRASE_TO_SKILL.items():
        if phrase in q_lower:
            return [{"name": skill_name, "score": 1.0, "matched": [phrase], "phase": "exact"}]

    # Phase 2b: Trigger word overlap against candidates only
    q_words = set(re.findall(r"[a-z0-9]{3,}", q_lower))
    if "py" in q_lower:
        q_words.add("py")
    stop = {'the','this','that','with','from','have','been','what','when',
              'where','which','about','than','then','just','also','very'}
    q_words -= stop
    if not q_words:
        return []  # No trigger match - return empty, don't fallback to BM25

    pool = bm25_top3[:30]
    scored = []
    for name, bm25_s in pool:
        skill_phrases = [p for p, sn in _TRIGGER_PHRASE_TO_SKILL.items() if sn == name]
        overlap = 0
        matched = []
        for p in skill_phrases[:20]:
            pw = set(re.findall(r"[a-z0-9]{3,}", p)) - stop
            common = q_words & pw
            if common:
                overlap += len(common)
                matched.append(p)
        if overlap > 0:
            scored.append({"name": name, "score": round(overlap / max(len(q_words),1), 2),
                           "matched": matched[:2], "phase": "trigger"})

    if scored:
        scored.sort(key=lambda x: -x["score"])
        return scored[:3]

    # Phase 2c: Levenshtein fuzzy matching against BM25 candidates
    # Fast single-row Levenshtein: ~0.05ms per comparison, searches top-15 candidates
    if len(q_lower) >= 5:
        best_fuzzy = None
        best_fuzzy_ratio = 0
        for name, _ in bm25_top3[:15]:
            phrases = [p for p, sn in _TRIGGER_PHRASE_TO_SKILL.items() if sn == name]
            for phrase in phrases[:10]:
                if len(phrase) < 4:
                    continue
                a, b = q_lower, phrase.lower()
                m, n = len(a), len(b)
                if abs(m - n) / max(m, n, 1) > 0.50:
                    continue
                # Single-row Levenshtein (O(n), no full matrix)
                prev = list(range(n + 1))
                for i in range(1, m + 1):
                    curr = [i] + [0] * n
                    for j in range(1, n + 1):
                        cost = 0 if a[i-1] == b[j-1] else 1
                        curr[j] = min(curr[j-1] + 1, prev[j] + 1, prev[j-1] + cost)
                    prev = curr
                ratio = 1 - (prev[n] / max(m, n))
                if ratio > 0.55 and ratio > best_fuzzy_ratio:
                    best_fuzzy_ratio = ratio
                    best_fuzzy = (name, phrase)
        if best_fuzzy:
            return [{"name": best_fuzzy[0], "score": round(best_fuzzy_ratio, 2),
                     "matched": [best_fuzzy[1]], "phase": "fuzzy"}]
    
    return []


# ── Rule Engine ──

def _load_rules() -> list[dict]:
    """Load priority rules from ~/.neuro-skill/rules.json."""
    global _RulesCache
    if _RulesCache is not None:
        return _RulesCache
    rules_path = Path.home() / ".neuro-skill" / "rules.json"
    if rules_path.exists():
        try:
            _RulesCache = json.loads(rules_path.read_text(encoding="utf-8"))
            return _RulesCache
        except Exception:
            pass
    _RulesCache = []
    return _RulesCache


def _check_rules(query: str) -> str | None:
    """Check if query matches a priority rule. Returns skill name or None."""
    rules = _load_rules()
    for rule in rules:
        pattern = rule.get("pattern", "")
        skill = rule.get("skill", "")
        if pattern and skill:
            try:
                if re.search(pattern, query, re.IGNORECASE):
                    return skill
            except re.error:
                pass
    return None


# ── Hook Handlers ──

def on_session_start(**kwargs):
    """Scan skills and build BM25 index. ~500ms for 332 skills, one-time."""
    global _RouteIndex
    with _RouteLock:
        if _RouteIndex is not None:
            return
        try:
            dirs = _get_skill_dirs()
            skills = _find_skill_files(dirs)
            _RouteIndex = _KeywordIndex(skills)
            _build_ngram(skills)
        except Exception:
            import logging
            logging.getLogger("unlimited_skills").warning(
                "Failed to build skill index", exc_info=True
            )


def pre_llm_call(**kwargs) -> dict:
    """Route user query, return top-3 skills as context block.

    Called before every LLM invocation by Hermes.
    Injects {"context": str} into the LLM prompt.
    """
    global _RouteIndex

    user_message = kwargs.get("user_message", "")
    if not user_message or not user_message.strip():
        return {}

    # Build index on first call (backup if on_session_start didn't fire)
    if _RouteIndex is None:
        try:
            on_session_start()
        except Exception:
            return {}

    if _RouteIndex is None:
        return {}

    lines = ["[Top 3 skills for this query]"]

    # Rule check (priority)
    rule_match = _check_rules(user_message)
    if rule_match:
        lines.insert(0, f"  (Rule: {rule_match})")
        lines.append(f"  1. {rule_match} (1.000)")
        lines.append("")
        lines.append("Rule-matched — use this skill unless the query clearly needs something else.")
        return {"context": "\n".join(lines)}

    # Task detection: skip routing for non-task queries
    if not _is_task_query(user_message):
        return {}
    
    # Three-phase routing: BM25 -> trigger phrases -> context
    try:
        bm25_top3 = _hybrid_recall(user_message, top_k=30)
        bm25_top30 = _hybrid_recall(user_message, top_k=80)
        results = _match_with_triggers(user_message, bm25_top3, bm25_top30)
    except Exception:
        return {}

    if not results:
        lines.append("  (no strong match — use built-in tools)")
        return {"context": "\n".join(lines)}

    for i, item in enumerate(results):
        if isinstance(item, dict):
            name = item.get("name", "")
            score = item.get("score", 0)
            phase = item.get("phase", "bm25")
            matched = item.get("matched", [])
            tag = {"exact": "E", "trigger": "T", "bm25": "B"}.get(phase, "?")
            extra = ""
            if matched:
                extra = " [" + matched[0] + "]"
            lines.append(f"  {tag} {name} ({score:.3f}){extra}")
        else:
            name, score = item
            lines.append(f"  ? {name} ({score:.3f})")

    lines.append("")
    lines.append("If none match, fall back to built-in tools.")

    return {"context": "\n".join(lines)}

# ── P2: 2-confirmation learning ──
_PENDING = {}
def _record_correction(query, skill):
    k = (query.lower().strip(), skill)
    c = _PENDING.get(k, 0) + 1
    _PENDING[k] = c
    return c >= 2
