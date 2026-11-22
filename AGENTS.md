# Repository instructions

## Mission

Build and verify 客厅遥控板 (linux-tv-remote), the phone and tablet LAN web remote described in `SPEC.md`. Treat `SPEC.md` as the product source of truth. The goal is a working installation on this Linux TV computer, not a code sample.

## Required workflow

1. Read all of `SPEC.md` before changing files.
2. Inspect the current repository and working tree; preserve unrelated user changes.
3. Perform the read-only host discovery listed in `SPEC.md`.
4. Briefly report the observed distro, display protocol, desktop, audio stack, available tools, and chosen implementation path.
5. Implement the project end to end. Make reasonable in-scope decisions without stopping for routine questions.
6. Before installing packages, changing permissions, changing firewall rules, enabling services, or performing other system-wide mutations, explain the exact change and use the normal approval flow.
7. Run proportionate automated checks and real local tests. Exercise the actual input, clipboard, media, browser-open, authentication, and user-service paths when safe.
8. Fix failures and rerun relevant tests. Do not report completion while required tests remain failing.
9. Keep `README.md` synchronized with the behavior actually verified on this host.
10. At handoff, distinguish verified behavior, simulated behavior, and anything that still needs a real iPad test.

## Safety boundaries

- Never run the main web service as root.
- Never expose the service to the public internet or add router port forwarding.
- Never make `/dev/uinput` world-writable.
- Never accept or execute arbitrary shell commands, arbitrary URLs, arbitrary key names, or arbitrary key combinations from the client.
- Use argument arrays without a shell for subprocesses.
- Use strict server-side allowlists and request validation.
- Do not print or log passwords, session tokens, or user-entered text.
- Do not weaken the host firewall or authentication merely to make a test pass.
- Preserve user configuration during reinstall or uninstall unless the user explicitly requests deletion.
- Avoid destructive Git or filesystem commands.

## Compatibility priorities

- Old iPad Safari compatibility comes before modern frontend convenience.
- Prefer small, understandable code and minimal dependencies.
- X11 must work when the host uses X11.
- For Wayland, use a security-conscious ydotool setup or clearly document why the host cannot support it and how to use an X11 session instead.
- Unicode and Chinese text input must use a clipboard-based path with an explicit paste action.

## Quality bar

- Handle touch cancellation and stuck mouse-button recovery.
- Validate all request sizes, numeric ranges, IDs, and enums.
- Use short subprocess timeouts and useful error handling.
- Keep secrets out of source control and restrict secret-file permissions.
- Add focused tests for authentication, origin/network restrictions, input validation, URL allowlisting, search encoding, and command mapping.
- Installation and uninstallation must be understandable and reversible.
- If a check cannot be performed, state exactly why and provide a concrete manual verification step.

