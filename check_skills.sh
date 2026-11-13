#!/bin/bash
for f in .agents/skills/*/SKILL.md; do
  d=$(basename "$(dirname "$f")")
  n=$(grep -m1 '^name:' "$f" | sed 's/^name: *//')
  s=$(grep -cE '^## [1-6]\. ' "$f")
  echo "$d name=$n sections=$s"
done
