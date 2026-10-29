# Security Policy

## Supported versions

Only the latest release receives fixes.

| Version | Supported |
| ------- | --------- |
| 0.5.x   | yes       |
| < 0.5   | no        |

## Scope

scpi-sim is a test tool. It simulates lab instruments for development and CI
and is not meant to be exposed to untrusted networks: like real instruments, it
accepts SCPI commands from anyone who can reach its ports and has no
authentication. Reports about that design are not vulnerabilities, but we are
interested in issues such as:

- crashes or hangs of the server caused by malformed protocol data
  (raw socket, VXI-11, HiSLIP, port mapper),
- code execution or file access through bench or profile files,
- problems in the Docker image or the browser demo
  (https://scpi-sim.steitec.net).

## Reporting a vulnerability

Please **do not open a public issue**. Report it privately through
[GitHub's private vulnerability reporting](https://github.com/steitec/scpi-sim/security/advisories/new)
or by e-mail to hello@steitec.net.

Include the version, how to reproduce the problem and what impact you expect.
You will get an answer within a week. Once a fix is released, the report is
published as a GitHub security advisory, crediting you if you wish.
