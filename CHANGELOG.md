# Changelog

This file records what shipped, newest first. Releases before 0.1.3 are listed from the version history, and the repository does not record their dates anywhere, so those headings say `date not recorded` rather than guess one.

## [Unreleased]

- MCP elicitation now expires within the tool-call approval window, cancels unanswered requests, and withdraws stale approval cards.

## [0.1.3] - 2026-09-16

- A self-hosted personal agent gateway that serves the web console from your own machine.
- Chat sessions, autonomous loops, memory, and a knowledge base.
- Tasks, triggers, and workflows for work that runs on a clock or on an event.
- An inbox with channel transports, so messages arrive in one place.
- A permission-gated app platform, with its Python SDK imported as `gideon.sdk`.
- An Electron desktop shell and a Capacitor mobile shell over the same gateway.
- The `gideon` CLI (setup, gateway, chat, run, doctor, snapshot, service install) and a separate Python client package for the gateway API.

Gideon is pre-1.0. Breaking changes can land in any release, so run `gideon snapshot` before you upgrade.

## [0.1.2] - date not recorded

An earlier pre-1.0 release. The repository does not record what changed in it.

## [0.1.1] - date not recorded

An earlier pre-1.0 release. The repository does not record what changed in it.

## [0.1.0] - date not recorded

The earliest release in the version history. The repository does not record what changed in it.

_This file follows the [Keep a Changelog](https://keepachangelog.com/) format._
