"""Neuro-skill drift monitor: runs daily, logs accuracy metrics."""
import json, os, sys, importlib.util, time

train_path = os.path.expanduser("~/.hermes/.unlimited-skills-task-train.json")
plugin_path = os.path.expanduser(
    "~/AppData/Local/hermes/hermes-agent/venv/Lib/site-packages/unlimited_skills/plugins/hermes/__init__.py"
)

with open(train_path) as f:
    train = json.load(f)

for key in list(sys.modules.keys()):
    if 'hermes' in key or 'neuro' in key: del sys.modules[key]
spec = importlib.util.spec_from_file_location("hermes_plugin", plugin_path)
mod = importlib.util.module_from_spec(spec)
mod._RouteIndex = None
spec.loader.exec_module(mod)

tp = fp = fn = tn = 0
for q in train["task"]:
    if mod._is_task_query(q): tp += 1
    else: fn += 1
for q in train["non_task"]:
    if not mod._is_task_query(q): tn += 1
    else: fp += 1

total = tp + tn + fp + fn
snapshot = {
    "ts": time.time(),
    "date": time.strftime("%Y-%m-%d"),
    "total": total,
    "task": len(train["task"]),
    "non_task": len(train["non_task"]),
    "accuracy": round((tp+tn)/(total or 1), 3),
    "precision": round(tp/(tp+fp or 1), 3),
    "recall": round(tp/(tp+fn or 1), 3),
    "fp": fp,
    "fn": fn,
}

drift_path = os.path.expanduser("~/.hermes/.unlimited-skills-drift-log.json")
history = json.load(open(drift_path)) if os.path.isfile(drift_path) else []
history.append(snapshot)
json.dump(history[-365:], open(drift_path, "w"), indent=2)

# Alert if accuracy dropped more than 5%
if len(history) >= 2:
    prev = history[-2]["accuracy"]
    curr = snapshot["accuracy"]
    delta = curr - prev
    if delta < -0.05:
        print(f"ALERT: accuracy dropped {abs(delta)*100:.1f}% ({prev*100:.1f}% -> {curr*100:.1f}%)")
    else:
        print(f"Accuracy stable: {curr*100:.1f}% (delta {delta*100:+.1f}%)")
else:
    print(f"Baseline: accuracy={snapshot['accuracy']*100:.1f}%")
