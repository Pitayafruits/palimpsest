# Contributing

Please open an issue describing the behavior, your OS, Confluence deployment type and expected result. Use synthetic documents: do not post private page exports or credentials.

For changes, install `requirements-build.txt`, run the test suite described in README, and include a regression test for a parser, image, authentication or conflict-handling fix. UI copy-only changes do not need new tests. Keep Windows and macOS behavior consistent where practical.

Submit focused pull requests. Explain the user-facing change and relevant validation. Changes that remove conflict protection, send credentials to another origin or enable plaintext credential storage will not be accepted.
