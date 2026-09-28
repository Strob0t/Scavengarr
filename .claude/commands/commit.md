Safe commit following Scavengarr workflow. Message: $ARGUMENTS

Steps:
1. Run `poetry run pre-commit run --all-files` — fix ALL errors before proceeding
2. Run `poetry run pytest` — ALL tests must pass
3. If both pass: `git add .` then `git commit -m "<message>"` then `git push origin staging`
4. If anything fails: fix the issue and restart from step 1

Rules:
- NEVER commit to main — only staging
- Small, atomic commits only
- If no commit message was provided, derive one from the changes (conventional commits style)
