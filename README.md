# unlimited-skills

无上限技能引擎 — Hermes Agent 三阶段技能路由插件。

## 架构

```text
  │
  ▼
[Phase 0] 确认检测
  ├── 用户确认 → 注入执行上下文
  └── 未确认 →
        │
        ▼
[Phase 1] Task Gate (规则+LLM混合)
  ├── 非任务 → 跳过（零开销）
  └── 任务 →
        │
        ▼
[Phase 2] Score-Rank Keyword Match
  ├── 命中 → Top-6 注入（含匹配度评分）
  └── 未命中 → 精简技能列表 Top-25 注入

→ LLM 语义推理最佳匹配 → 用户确认 → 执行
```

## 特性

- **无上限技能数** — O(n) 关键词匹配，技能数不影响延迟
- **评分排序** — 覆盖率(coverage) + 特异性(specificity) 加权排序，最佳匹配优先
- **预设 token 集合** — Catalog 加载时预计算 frozenset，查询时 O(1) 交集
- **中英双语** — 中英文 tokenization 全覆盖，CN bigrams/trigrams + English words
- **零噪声** — 最小匹配阈值 + 中文停用词过滤，避免无关技能混入
- **零外部依赖** — 纯 Python 标准库，无需 pip install
- **100% 隐私** — 所有索引本地，无需外部 API

## 安装

```bash
# 通过 Hermes CLI
hermes skills install https://github.com/wuykjl/unlimited-skills

# 或手动
git clone https://github.com/wuykjl/unlimited-skills
cp -r unlimited-skills ~/AppData/Local/hermes/plugins/
```

## 配置

.hermes/config.yaml:

```yaml
plugins:
  enabled:
    - unlimited-skills
```

## 维护

| 文件 | 说明 |
|------|------|
| `skills-catalog.md` | 技能目录。编辑关键词列提升路由精度 |
| `__init__.py` | 路由引擎。无需修改即可工作 |

编辑 `skills-catalog.md` 的「关键词」列可提升匹配精度：
- 增加常用同义词
- 移除过于泛化的词
- 中英文关键词均支持

## 性能

| 操作 | 复杂度 |
| --- | --- |
| Catalog 加载 | O(n)，仅一次 |
| 关键词匹配 | O(n), frozenset 交集 |
| Fallback 注入 | Top-25 限制 |
| 上下文开销 | 匹配时 ~500 chars, fallback ~1200 chars |

## 版本

v2.0.0 — 替换旧版 BM25/triggers/Levenshtein 五层混合路由为三阶段评分匹配。

## 许可证

MIT
