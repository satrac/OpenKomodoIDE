# Contributing

This is an experimental, self-supported fork. Pull requests are welcome, but
there is no guarantee of review or support.

## Before opening a pull request

- Keep changes focused and explain the problem they solve.
- Include the commands you ran to build and test the change.
- Do not include generated build output, `codeintel.db`, or local configuration.
- Mention the Python, compiler, and distribution versions used.
- If a change affects packaging or icons, include the relevant `make build`,
  `make image`, or `make check` result.

## Useful commands

```bash
make doctor
make build
make check
make image
```

The original upstream source is at
<https://github.com/ActiveState/OpenKomodoIDE>, but this fork is not supported
by ActiveState.
