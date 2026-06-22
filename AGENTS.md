# Repository Safety Rules

## Human Review And Publication

Human review is required before anything in this repository is treated as ready for public release, debut, launch, announcement, submission, posting, or broad sharing.

Do not publish, release, announce, submit, post, share broadly, or otherwise make work public without explicit human review and explicit user approval in the current conversation.

Do not treat broad phrases such as "publish", "share", "post progress", "put it on GitHub", "make it public", or similar wording as permission to skip human review.

Before any public-facing action, clearly state what would become visible, what review has or has not happened, and wait for a clear yes from the user.

## GitHub Repository Visibility

Repository visibility changes are destructive/publication actions.

Never make this GitHub repository public through Codex, CLI, API, GitHub connector, Chrome extension, browser automation, or recurring automation without explicit, repository-specific user confirmation in the current conversation.

Before changing this repository to public, state all of the following and wait for a clear yes:

- Target repository in `owner/name` form.
- Exact operation, such as `gh repo edit owner/name --visibility public`.
- Confirmation that README, license, SECURITY.md, secret scan, personal path scan, and PUBLIC_READY.md have been checked.
- Reminder that commit history and files become visible on the web.

Never create a public GitHub repository by default. New repositories must be private unless the user explicitly says the exact repository should be public.

Never run automation that changes repository visibility. Automations may report visibility and readiness status only.
