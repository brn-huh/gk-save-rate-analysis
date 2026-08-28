# gk-save-rate-analysis

## Working agreement

- Inspect the relevant code, callers, tests, and `git status` before editing.
- Make the smallest complete change and preserve unrelated user work.
- Fix shared root causes rather than adding the same defensive workaround at each caller.
- Treat `.env.local`, API keys, `data/`, and OUIDs as sensitive. Keep them out of source,
  logs, test fixtures, and generated public output.
- Run the narrowest relevant tests first, then `pytest -q` when the change can affect the
  wider pipeline. Report skipped or failing checks.
- Do not commit, push, deploy, publish, or modify remote systems unless explicitly asked.

## Skill routing

Use a skill only when its specialized workflow materially helps. For ordinary implementation
plans and small code changes, work directly without forcing a skill.

- Hard bugs or performance regressions: `diagnosing-bugs`.
- Test-first feature or bug work: `tdd`.
- Module boundaries, interfaces, or architecture: `codebase-design`.
- Domain terminology, `CONTEXT.md`, or ADRs: `domain-modeling`.
- UI design or implementation: `ui-ux-pro-max`; add `ui-styling` when building the UI.
- UI/UX or accessibility audit: `web-design-guidelines`.
- Review changes against a branch, commit, issue, or spec: `code-review`.
- Primary-source investigation that should be saved in the repository: `research`.
- Throwaway implementation used to answer a design question: `prototype`.

If a named skill is unavailable, continue with the closest direct workflow instead of
inventing a skill name. Do not use a planning skill merely because the user asks for a plan.
