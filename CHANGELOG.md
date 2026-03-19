# CHANGELOG

<!-- version list -->

## v0.2.1 (2026-03-19)

### Bug Fixes

- **models**: Use ActionableError for PostingResult and ContentResult failures
  ([`7702ead`](https://github.com/grimlor/ado-workflows/commit/7702eadaee02bdfb061075a226643008fa8f6226))


## v0.2.0 (2026-03-19)

### Bug Fixes

- Enable pyright strict mode with Azure SDK type stubs
  ([`71db909`](https://github.com/grimlor/ado-workflows/commit/71db909925c0f0375db553c708f2a969c542146e))

- **ci**: Push release commit to main alongside tag
  ([`a1361b6`](https://github.com/grimlor/ado-workflows/commit/a1361b69d87664d605cd9b4e89a2c334678a2a36))

- **ci**: Use PAT for release push to bypass branch ruleset
  ([`3f6eb86`](https://github.com/grimlor/ado-workflows/commit/3f6eb86d96ea93c11f0fca0bb43f90a344ab0129))

### Build System

- Add semantic-release config and align pytest settings
  ([`7bebc0f`](https://github.com/grimlor/ado-workflows/commit/7bebc0fd86e7368cf1c8d239b2d6504d0d928ad0))

- Remove CI skill sync in favor of universal-dev-skills clone
  ([`c61da72`](https://github.com/grimlor/ado-workflows/commit/c61da7204ec71cb59cf46926276ae223055641c8))

- Set major_on_zero = false to prevent premature 1.0.0 bump
  ([`bb22178`](https://github.com/grimlor/ado-workflows/commit/bb22178f299db3e91653ee57628ce5fde6299ecc))

### Chores

- Add combine-as-imports, cap requires-python, unify PR template
  ([`008c67e`](https://github.com/grimlor/ado-workflows/commit/008c67efc9dd02ce5bc67fc33c21863dfb902e50))

- Add format step to check task
  ([`b773658`](https://github.com/grimlor/ado-workflows/commit/b7736587301597daaaa2415e7e0ec39cb2aef16b))

- Add license badge, normalize badge format
  ([`ac07259`](https://github.com/grimlor/ado-workflows/commit/ac0725995798f1e7b32d03ecbcb05f1ebe32346b))

- Add ruff per-file-ignores for test conventions
  ([`b2e7ce3`](https://github.com/grimlor/ado-workflows/commit/b2e7ce34db566d6ad68a292e8eb07a250ea2ba8c))

- Standardize author identity and add skills sync workflow
  ([`ffbdbc4`](https://github.com/grimlor/ado-workflows/commit/ffbdbc49f34eace46f4c4a0f15c1fcaf68c24760))

### Continuous Integration

- Merge publish workflow into release pipeline
  ([`321b8e6`](https://github.com/grimlor/ado-workflows/commit/321b8e612afcc2507584b5c81130d21f1295ecf3))

### Documentation

- Add README for .copilot directory to clarify purpose and contents
  ([`a371d3d`](https://github.com/grimlor/ado-workflows/commit/a371d3d2c805a9857357f11dd9684ab67fb889ed))

- Fix coverage badge gist ID
  ([`1048df4`](https://github.com/grimlor/ado-workflows/commit/1048df4e896e5d35f8ae85f9e0ff62ef02448e9a))

### Features

- Add code review operations (iterations, positioning, content, identity)
  ([`2c1631f`](https://github.com/grimlor/ado-workflows/commit/2c1631fe5c3709a89080b0652e2937a91e046019))

### Testing

- Convert WHAT fields to numbered enumeration
  ([`02b6886`](https://github.com/grimlor/ado-workflows/commit/02b6886c3b1d3a7b10b03c4c44c81caaa3516b25))


## v0.1.0 (2026-03-05)

- Initial Release
