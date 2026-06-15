"""unlimited-skills test suite — run: python test_router.py"""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import __init__ as plugin
_tokenize_set = plugin._tokenize_set
_parse_frontmatter = plugin._parse_frontmatter
_KeywordIndex = plugin._KeywordIndex
_is_task_query = plugin._is_task_query
_rules_task = plugin._rules_task
_is_ambiguous = plugin._is_ambiguous
_ngram_search = plugin._ngram_search
_build_ngram = plugin._build_ngram
_match_with_triggers = plugin._match_with_triggers
_record_correction = plugin._record_correction
_load_triggers = plugin._load_triggers
pre_llm_call = plugin.pre_llm_call

def test_tokenize():
    ts = _tokenize_set("python code review")
    assert "python" in ts and "code" in ts and "review" in ts

def test_bm25_routing():
    skills = [
        ("python-reviewer", "Python review", "python pep8 security code review"),
        ("go-builder", "Go fix", "go golang build fix error"),
    ]
    idx = _KeywordIndex(skills)
    r = idx.query("python code review", top_k=3)
    assert r[0][0] == "python-reviewer"

def test_edge_cases():
    idx = _KeywordIndex([])
    assert idx.query("anything", top_k=3) == []

def test_frontmatter():
    text = """---
name: test
description: desc
---
body"""
    meta, body = _parse_frontmatter(text)
    assert meta["name"] == "test"
    assert meta["description"] == "desc"

def test_task_detection():
    assert _is_task_query("deploy my app")
    assert _is_task_query("write unit tests")
    assert not _is_task_query("hi")
    assert not _is_task_query("thanks")

def test_ambiguous():
    assert _is_ambiguous("Anyone else broken?")
    assert not _is_ambiguous("deploy my app")

def test_ngram():
    skills = [("perf", "Perf", "make your app faster optimize lag slow")]
    _build_ngram(skills)
    r = _ngram_search("my app is lagging", 3)
    assert len(r) > 0 and r[0][0] == "perf"

def test_pending():
    c1 = _record_correction("test query", "test-skill")
    assert not c1
    c2 = _record_correction("test query", "test-skill")
    assert c2

def test_pre_llm_empty():
    assert pre_llm_call(user_message="") == {}
    assert pre_llm_call(user_message="hi") == {}

def test_zero_deps():
    fp = os.path.join(os.path.dirname(os.path.abspath(__file__)), "__init__.py")
    src = open(fp, encoding="utf-8").read()
    assert "from neuro_skill" not in src
    assert "import yaml" not in src

def test_triggers_load():
    _load_triggers()
    assert plugin._TRIGGER_PHRASE_TO_SKILL is not None
    assert len(plugin._TRIGGER_PHRASE_TO_SKILL) > 1000

if __name__ == "__main__":
    tests = [
        test_tokenize, test_bm25_routing, test_edge_cases,
        test_frontmatter, test_task_detection, test_ambiguous,
        test_ngram, test_pending,
        test_pre_llm_empty, test_zero_deps, test_triggers_load,
    ]
    passed = 0
    for t in tests:
        try:
            t()
            passed += 1
            print(f"  PASS {t.__name__}")
        except Exception as e:
            print(f"  FAIL {t.__name__}: {e}")
    print(f"\n  {passed}/{len(tests)} passed")
    sys.exit(0 if passed == len(tests) else 1)
