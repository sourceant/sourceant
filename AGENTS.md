# Agent Guidance

## Documentation

Every new feature, and every change to an existing feature, updates the public documentation in the same piece of work. Where the feature has a screen, refresh its screenshots and use them in the documentation.

## Naming: type, never kind

Call the category of a thing `type`. Never introduce `kind` to mean type: not in identifiers, fields, columns, API parameters or copy. When you change code that uses `kind` this way, migrate it, keeping a deprecated alias where clients depend on the old name. `scripts/check-kind.sh` runs in CI and fails when uses of the word grow. When you remove some, lower `.kind-baseline`.
