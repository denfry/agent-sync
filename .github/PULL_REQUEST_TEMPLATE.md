<!--
The PR title MUST be a valid Conventional Commit (e.g. "feat: add --json to status",
"fix(locks): handle expired lease"). It becomes the squash-merge commit and drives
the next automated version bump. A CI check enforces this.
-->

## Summary

<!-- What does this PR change, and why? -->

## Related issues

<!-- e.g. Closes #123 -->

## Release impact

- [ ] `fix:` — bug fix (patch)
- [ ] `feat:` — new feature (minor)
- [ ] Breaking change (note `BREAKING CHANGE:` in the commit body)
- [ ] No release (`docs:` / `chore:` / `refactor:` / `test:` / `ci:` / `build:` / `style:`)

## Checklist

- [ ] Tests pass locally (`pytest`)
- [ ] Lint passes (`ruff check .`)
- [ ] Runtime code uses the standard library only
- [ ] Updated `README.md` / docs if behaviour changed
