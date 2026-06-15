# unlimited-skills

无上限技能引擎 — Hermes Agent 五层混合路由插件。

## 架构

```
User Query
  │
  ▼
[1] Task Gate (规则+LLM混合)
  ├── 非任务 → 跳过（零开销）
  └── 任务 → 
        │
        ▼
[2] Exact Trigger Match (精确命中)
  ├── 命中 → Top-3 注入
  └── 未命中 →
        │
        ▼
[3] Trigger Word Overlap (重叠评分)
  ├── 命中 → Top-3 注入
  └── 未命中 →
        │
        ▼
[4] Levenshtein Fuzzy (拼写容差)
  ├── 命中 → Top-3 注入
  └── 未命中 →
        │
        ▼
[5] LLM Fallback (边界兜底)
       → Top-3 注入
```

## 特性

- **无上限技能数** — BM25 + 触发词匹配，O(1) 查询，技能数不影响延迟
- **中英双语** — 2918 条触发词，覆盖中英文真实用户 query
- **自训练闭环** — 用户修正 → 反馈日志 → cron 自动训练
- **漂移自监控** — 每天 04:00 运行 366 条测试集，accuracy 下降 >5% 自动告警
- **零噪声** — 无 BM25 兜底，分数 0.5-1.0 有意义
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

## Cron 任务

安装后自动注册：

| Cron | 时间 | 功能 |
|------|------|------|
| unlimited-skills-autotrain | 03:00 daily | 反馈 → 训练 |
| unlimited-skills-drift | 04:00 daily | 漂移监控 → 告警 |

## 许可证

MIT
