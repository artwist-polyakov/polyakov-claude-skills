# E2E Test — task description read from a file

This task text is deliberately longer than one line, and it carries a "quoted"
phrase, a Windows-style path `C:\tmp\out` and identifiers in backticks such as
`beforeSend`. None of those can live inside a state.json value, which is why the
caller has to name the task itself.

Nothing in the repository needs changing: this run only exercises the wiring of
`--description-file` and `--task-label`.
