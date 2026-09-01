# Contributing

Contributions are welcome, from a typo to a plugin.

## Getting set up

```bash
make up            # app, database and redis
make db-upgrade    # bring the schema up to date
make shell         # a shell in the app container
```

`make down` stops it, `make build` rebuilds the image after a dependency
changes, and `make logs` follows what it is doing.

## What CI checks

Two things, and you can run both before pushing:

```bash
make test          # pytest src/tests/ -v
make lint          # black --check .
```

`make lint-fix` formats in place. CI runs `pytest` and `./sourceant code lint`
against the same tree, so a green run here is a green run there.

## Opening a pull request

Branch off `main`, keep the change to one subject, and say in the description
what the change is and why it exists. If it fixes a bug, a test that fails
without the fix is worth more than a paragraph explaining it.

New plugins live under `src/plugins/`. The built-in ones are the working
examples: each declares what it provides and registers it, and the core reaches
for the interface rather than the class.

## Reporting something

[Open an issue](https://github.com/sourceant/sourceant/issues) with what you
did, what happened, and what you expected instead. A log line beats a summary
of one.
