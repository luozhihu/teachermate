---
name: teacher-deliverables
description: "Use when generating teacher-facing deliverables from grounded wiki answers, including exam drafts, lesson plans, teaching outlines, and classroom-ready markdown documents."
---

# Teacher Deliverables

Generate editable teacher documents from grounded wiki answers. Do not invent hidden sources. Preserve the wiki citations supplied by the caller.

## Outputs

- Exam draft in Markdown
- Lesson plan in Markdown

## Rules

- Treat the provided wiki-backed answer as the primary source of truth.
- Keep the output editable and practical for a real teacher.
- Include a `Sources` section with the wiki citations exactly as provided.
- Prefer clear headings, short teacher notes, and copy-paste-ready formatting.
