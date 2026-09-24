# Security, Permissions and Audit

## Principles
- jarvisd runs as the **user**, never as root. Root operations go through `pkexec`/`sudo`, and each one
  needs an explicit approval.
- **One choke-point.** Every side effect goes through `ToolExecutor.execute()`, which runs
  assess → policy → approval → execute → verify → audit.
- **Critical operations always ask**, in every mode: mkfs, dd to devices, partitioning tools,
  efibootmgr, grub-install, `rm -rf` on system paths, auth files (shadow/sudoers/pam), disabling
  AppArmor/firewall, and any write under `/boot/efi` or to raw disks.
- Irreversible operations ask in AUTONOMOUS mode (MEDIUM risk and up) and in FULL mode (HIGH risk and up).
- Build scripts contain no disk-writing commands (enforced by a test). VM testing uses disposable qcow2 images.

## Permissions
`root, file_modify, file_delete, external_comm, send_messages, cloud, mcp, browser, package_install,
system_change, camera, microphone, input_control, run_commands`.
You can view or change them with `jarvis permissions` or in Settings → Permissions. A permission that
isn't granted makes JARVIS ask instead of act.

## Approval dialog
It shows the action, reason, risk, operation type, reversibility, backup availability and the exact
command or change. Buttons: APPROVE, APPROVE ALL SIMILAR (low/medium risk only, for the current
session), DENY, EDIT PLAN, SHOW DETAILS, CREATE BACKUP, STOP JARVIS. When no approval UI is attached,
requests time out as *denied*.

## Secrets
API keys go to `secrets.json` (0600) or the system keyring (`JARVIS_USE_KEYRING=1`), or come from
`JARVIS_KEY_<NAME>` env vars. They are never returned by the API. The audit log redacts `sk-…`, `AIza…`,
`hf_…`, `ghp_…`, `xox…`, bearer tokens, and `password=`/`token=`/`api_key=` values.

## Audit
`~/.config/jarvis/audit.jsonl` (0600) records the request, plan, permission decision, approval result,
command, file change, result, error and recovery. Screenshots are not logged by default
(`audit.log_screenshots`). You can disable logging per category.

## Emergency stop
`STOP` is a process-wide event. Waits, typing chunks, subprocesses (whose process group is killed),
approval waits and the agent loop all check it. PyAutoGUI's corner failsafe is enabled too.
