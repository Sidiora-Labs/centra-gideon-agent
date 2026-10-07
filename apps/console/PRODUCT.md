# Product

## Register

product

## Platform

web

## Users

This repository serves people who self-host Gideon and developers extending its open-source runtime. They come to the console to delegate: chatting with agents, launching goal loops, reviewing completed work, shaping memory and knowledge, and connecting services and automations. Gideon also offers a hosted service; self-hosting is not a requirement for using the product.

## Product Purpose

The open-source Gideon console brings together chat, goal loops, memory, knowledge, skills, scheduled automation, an inbox and apps around a personal agent runtime. This document covers that console in the self-hosted edition. Success means users can delegate real work, understand what their agent is doing and control its access.

## Positioning

The one AI agent platform you fully own — provider integrations are modular while the gateway retains core authority, and the whole system answers to one person: you.

## Brand Personality

Friendly, playful, approachable. Gideon should feel like a capable companion, not an enterprise console: soft physical controls, warm coral energy, springy motion that makes delegation feel light. The visual voice blends two influences: a **neural-expressive** signature (fractional variable-font weights, ambient glow, gradient energy that reads as live intelligence) and **Google's playful expressive element design** (pill shapes, tonal surfaces, springy overshoot on interaction) — interpreted through Gideon's own coral identity, not copied literally. Playfulness is earned and tunable (the app literally has a bounciness slider) — it never gets in the way of the task, and it never undercuts the sense that serious agentic machinery is running underneath.

## Anti-references

- **Hacker terminal cosplay.** No green-on-black, scanlines, fake-CRT chrome, or aesthetics that sacrifice readability to look "technical." The optional CLI density mode is a utilitarian layout choice, not a costume.

## Design Principles

1. **Companion, not console.** Every surface should feel personal and warm — one user, their workspace, their agent — never like a fleet-management tool.
2. **Show the machinery, softly.** Autonomy needs legibility: loops, approvals, and agent activity should be legible and steerable where supported, presented calmly rather than as alarm walls.
3. **Playful within discipline.** Motion and delight are real but budgeted (expressiveness/bounciness scale them, reduced-motion zeroes them); the task always wins.
4. **Everything is a token.** Colors, spacing, radius, motion all ride the customizable token system — no hardcoded values; the user can retint and reshape the whole app.
5. **Earned familiarity.** Standard product affordances (nav rail, composer, lists, settings) done exceptionally well beat invented ones.

## Accessibility & Inclusion

The design target is WCAG AA, including ≥4.5:1 normal-text contrast, visible keyboard focus and meaningful reduced-motion behavior. Shared controls and theme rules support that target; their presence is not app-wide certification. Verify actual states, themes, viewports and custom animations. See [current interaction patterns](../../docs/design/PATTERNS.md) and [browser-check limits](e2e/README.md).
