---
name: fast-delegation
description: "Delegate easy, low-risk requests to a lighter model without switching the foreground model. Use this skill whenever the user invokes /fast, asks to use fast mode, wants a quick lightweight answer, asks to delegate simple work to GPT 5.4 mini, or says they do not want to switch models back and forth."
---

# Fast Delegation

Use this skill when the user wants an easy activity handled by GPT 5.4 mini while keeping the current foreground model unchanged.

## Core behavior

Prefer the `/fast <request>` slash command for explicit fast-mode requests. The command is implemented by the `fast-command` extension and temporarily switches the current session to `gpt-5.4-mini`, sends one prompt, waits for the answer, then restores the previous model. This avoids starting a second Copilot process while keeping the model switch scoped to a single request.

If the user asks for fast mode in normal prose instead of using `/fast`, explain that `/fast <request>` is the low-overhead path for a one-off lighter-model answer. If you handle the request yourself or delegate to a subagent from the foreground conversation, the foreground model is still involved.

Avoid solving the whole task yourself before using fast mode, because the point is to save heavier-model work.

## Good fit for `/fast`

Use GPT 5.4 mini for tasks that are simple, bounded, and low risk:

- Summarizing text, diffs, logs, issues, or command output
- Explaining a small snippet or error message
- Drafting short prose, commit messages, PR descriptions, or shell commands
- Searching or inspecting a small part of the repo
- Running a quick command, test, or small code edit
- Answering a straightforward technical question

## Do not delegate blindly

Keep the foreground model in control, or ask for clarification, when the request involves:

- Security-sensitive data, credentials, access control, or compliance decisions
- Destructive operations, production deployments, or irreversible changes
- Large refactors, multi-file code edits, architecture decisions, or subtle debugging
- Tasks that require careful permissions, user confirmation, or broad repository context
- Anything where an incorrect answer would be costly

If the user used `/fast` for a task that is not a good fit, briefly explain that it needs the main model or a more careful workflow, then proceed safely.

## Direct prompt template

The `/fast` extension temporarily switches to `gpt-5.4-mini` and sends a prompt like this:

```text
Handle this as a fast, lightweight, single-message request.

Work directly and concisely. You may use tools and edit code when the request calls for it. Do not perform destructive operations, handle secrets, or attempt production changes unless the user explicitly asked for that and the normal permission flow allows it. If the request is unsafe, ambiguous, or too complex for fast mode, say so plainly and explain what workflow is needed instead.

Request:
<user request>
```

## Response style

For direct `/fast` invocations, the extension logs the answer itself. If the fast model reports that the task is not suitable for fast mode, do not pretend it completed the work.
