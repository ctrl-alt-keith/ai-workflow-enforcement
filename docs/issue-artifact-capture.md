# Issue-owned Dropbox artifact capture

Use `python3 -m enforcement.issue_artifact_capture --help` only after the
producing controller admits an exact artifact under the
[Playbook's governed-artifact and storage contract](https://github.com/ctrl-alt-keith/ai-workflow-playbook/blob/main/docs/evidence-lifecycle.md#governed-artifact-capture).
Complete Claude/Codex review outputs and decision inputs with an exact
downstream dependency may qualify; canaries, health probes, routine checks,
and scratch remain ephemeral unless separately admitted. A review launcher's
local output file is not durable capture.

Supply the governing `CAK-<id>`, a fresh local source and receipt path, a
unique dated and versioned name, the expected Dropbox account and namespace,
and the storage-admission authority reference. The caller must establish that
namespace from the owning storage contract; a numeric ID alone is not authority.
The command writes only to the
verified provider folder `/issues/CAK-<id>/`; a Dropbox synced source path
never selects the destination. The folder must already exist. Resolve an
absent folder through the authorized provider route before capture; unknown
provider state blocks the write.

Preserve the command's exclusive receipt with the issue's durable evidence.
After a blocked upload or readback, inspect provider state before another
attempt. Do not retry the same target on an uncertain outcome. On success, the
receipt's Dropbox path and ID are the artifact locator; local source paths are
not.

Dropbox provider behavior checked 2026-09-23; official references are in the
reviewed PR evidence.
