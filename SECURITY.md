# Security Policy

## Supported Versions

Security fixes are applied to the latest released version of `agent-blueprint`.

## Reporting a Vulnerability

Do not open a public issue for suspected security vulnerabilities.

Instead, report the issue privately to the project maintainer with:

- a clear description of the vulnerability
- affected versions or commit range
- reproduction steps or a proof of concept
- impact assessment
- any suggested mitigation

If a dedicated security contact address is added later, this file should be updated to point to it explicitly.

## Scope Notes

Because this project deals with model providers, environment variables, deployment configuration, and generated code, the following areas are especially sensitive:

- credential handling and secret exposure
- unsafe command execution or shell injection
- insecure template rendering or code generation paths
- unsafe YAML loading or interpolation behavior
- deployment packaging and cloud configuration issues

## Disclosure Policy

- Please allow maintainers reasonable time to investigate and prepare a fix before public disclosure.
- When a fix is released, security-relevant user actions should be documented in release notes or `CHANGELOG.md`.
