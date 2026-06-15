"""Auto-train: read feedback log, generate trigger phrases, rebuild index."""
import json, os, sys

# Paths
hermes_home = os.path.expanduser("~/.hermes")
idx_path = os.path.join(hermes_home, ".unlimited-skills-trigger-index.json")
fb_path = os.path.join(hermes_home, ".unlimited-skills-feedback.json")
cn_path = os.path.join(hermes_home, ".unlimited-skills-cn-augment.json")

# Load index
if os.path.isfile(idx_path):
    with open(idx_path) as f:
        idx = json.load(f)
else:
    idx = {}

# Load feedback
if not os.path.isfile(fb_path):
    print("No feedback file found")
    sys.exit(0)

with open(fb_path) as f:
    fb = json.load(f)

feedback = fb.get("feedback", [])
if not feedback:
    print("No feedback entries")
    sys.exit(0)

# Process: deduplicate, keep latest (query → correct_skill)
latest = {}
for entry in feedback:
    q = entry.get("query", "").strip().lower()
    s = entry.get("correct", "")
    if q and s:
        latest[q] = s

# Apply to trigger index
added = 0
for query, skill in latest.items():
    for k in idx:
        if skill in k or skill.replace(" ", "-") in k:
            if query not in idx[k]:
                idx[k].append(query)
                added += 1
            break
    else:
        # Skill not in index — add placeholder
        idx[skill] = [query]
        added += 1

# Also update CN augment
cn = {}
if os.path.isfile(cn_path):
    with open(cn_path) as f:
        cn = json.load(f)

for query, skill in latest.items():
    is_zh = any(ord(c) > 0x4e00 for c in query)
    if is_zh:
        for k in cn:
            if skill in k:
                if query not in cn[k]:
                    cn[k].append(query)
                break

# Save
with open(idx_path, "w") as f:
    json.dump(idx, f, ensure_ascii=False, separators=(",", ":"))

tp = sum(len(v) for v in idx.values())
print(f"Trained {added} new trigger phrases from {len(latest)} feedback entries")
print(f"Total: {len(idx)} skills, {tp} phrases")

# Also write a status file
status = {"trained": added, "total_feedback": len(feedback), "total_skills": len(idx), "total_phrases": tp}
with open(os.path.join(hermes_home, ".unlimited-skills-train-status.json"), "w") as f:
    json.dump(status, f)
