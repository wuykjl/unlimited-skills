# unlimited-skills

无上限技能引擎 — Hermes Agent 五层混合路由插件。
Unlimited skill engine — Hermes Agent 5-layer hybrid routing plugin.

> 就像一个高级餐厅的领班：你说"我饿了"，他直接带你到最合适的桌子，不需要你把 400 道菜翻一遍。
> Like a maître d' at a fine restaurant: you say "I'm hungry", they seat you at the best table — no need to flip through a 400-dish menu.

---

## 先看效果 · See the Difference

**我问 AI："帮我优化一下网站" · Me: "Help me optimize my website"**

❌ 没装之前：AI 一脸懵，输出一堆不相关的废话
❌ Before: AI confused, spits out irrelevant text

✅ 装完之后：AI 秒懂，直接调用「性能优化」技能开工
✅ After: AI understands instantly, calls the "Performance Optimization" skill

**我问 AI："谢谢，讲得很好" · Me: "Thanks, well said"**

❌ 没装之前：还在弹一堆技能选项，像推销员一样烦
❌ Before: Still suggesting skills like a pushy salesperson

✅ 装完之后：安静回一句"不客气"，该聊天聊天
✅ After: A quiet "you're welcome" — just chatting normally

---

## 架构 · Architecture

```
User Query
  │
  ▼
[1] Task Gate (规则+LLM混合 / rules + LLM hybrid)
  ├── 非任务 / Not a task → 跳过 / skip（零开销 / zero overhead）
  └── 任务 / Task →
        │
        ▼
[2] Exact Trigger Match (精确命中)
  ├── 命中 / Hit → Top-3 注入 / injected
  └── 未命中 / Miss →
        │
        ▼
[3] Trigger Word Overlap (重叠评分 / overlap scoring)
  ├── 命中 / Hit → Top-3 注入 / injected
  └── 未命中 / Miss →
        │
        ▼
[4] Levenshtein Fuzzy (拼写容差 / typo tolerance)
  ├── 命中 / Hit → Top-3 注入 / injected
  └── 未命中 / Miss →
        │
        ▼
[5] LLM Fallback (边界兜底 / edge case catch-all)
       → Top-3 注入 / injected
```

---

## 特性 · Features

- **无上限技能数 / Unlimited skills** — BM25 + 触发词匹配，O(1) 查询，技能数不影响延迟 / O(1) lookup, skill count doesn't affect latency
- **中英双语 / Bilingual CN/EN** — 2918 条触发词，覆盖中英文真实用户 query / 2,918 trigger phrases covering real Chinese and English user queries
- **自训练闭环 / Self-training loop** — 用户修正 → 反馈日志 → cron 自动训练 / user corrections → feedback log → cron auto-training
- **漂移自监控 / Drift monitor** — 每天 04:00 运行 366 条测试集，accuracy 下降 >5% 自动告警 / runs 366 test cases daily at 4AM, alerts if accuracy drops >5%
- **零噪声 / Zero noise** — 分数 0.5-1.0 有意义；无 BM25 随机猜测 / scores 0.5-1.0 have meaning; no BM25 random guesses
- **100% 隐私 / 100% local** — 所有索引本地，无需外部 API / all indexes on-device, no external APIs

---

## 安装 · Install

```bash
# 就一行命令 / One command
hermes skills install https://github.com/wuykjl/unlimited-skills
```

装完直接用。中文英文都行，它会自己学习，还会自己监控有没有变笨。
Install and go. Works in Chinese and English. Learns on its own. Monitors itself for drift.

---

## 适合谁 · Who's It For?

- 每天和 AI 说"帮我写个脚本""部署一下""检查安全漏洞"的人 / You say "write me a script", "deploy this", "check for security holes" — daily
- 装了 300+ 技能但不知道哪个在用、哪个没用的人 / 300+ skills installed and you don't know which ones are actually being used
- 不想浪费时间在"选技能"这件事上的人 / You don't want to waste time thinking about "which skill to pick"

---

## 致谢 · Acknowledgments

本项目的技能路由能力建立在以下开源技能生态之上：
This project's skill routing is built on the open skill ecosystems of:

- [ECC (Enterprise Coding Conventions)](https://github.com/wuykjl/ecc) — 117 规则编码标准框架 / 117-rule coding standard framework
- [Anthropic Skills](https://github.com/anthropics/skills) — 官方 Agent Skills 仓库 / Official Agent Skills repository
- [Claude Code Plugins](https://github.com/anthropics/claude-code) — Claude Code 官方插件生态 / Claude Code official plugin ecosystem
- 以及所有向 Hermes 生态贡献技能的个人开发者 / And all individual developers who contributed skills to the Hermes ecosystem

---

📍 0 依赖 | 0 配置 | 装完即用 · 0 dependencies | 0 config | install & forget
🔗 [github.com/wuykjl/unlimited-skills](https://github.com/wuykjl/unlimited-skills)
