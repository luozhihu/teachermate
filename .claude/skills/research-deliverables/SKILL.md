---
name: research-deliverables
description: "Use when generating medical research deliverables from grounded wiki answers, including research topic proposals, research protocols, and researcher-ready markdown documents."
---

# Research Deliverables

Generate editable research documents from grounded wiki answers. Do not invent hidden sources. Preserve the wiki citations supplied by the caller.

## Outputs

- Research topic proposal in Markdown
- Research protocol in Markdown

## Rules

- Treat the provided wiki-backed answer as the primary source of truth.
- Keep the output editable and practical for a real researcher.
- Include a `Sources` section with the wiki citations exactly as provided.
- Prefer clear headings, structured methodology sections, and copy-paste-ready formatting.