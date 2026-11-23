---
name: chiron
description: Chiron controls (on, off, status, reset, level). The Chiron hook answers these itself, before any model call.
argument-hint: "[on|off|status|reset|level <topic> <level>]"
disable-model-invocation: true
---

The Chiron plugin's UserPromptSubmit hook handles `/chiron` commands itself and blocks the prompt, so this text should never reach the model.

If it did, the hook did not run (hooks may be disabled, `python3` may be missing from the PATH, or the state folder may not be writable) and nothing was changed. Tell the user that in one sentence. Do not pretend the command worked, and do not change any level yourself.

Commands the hook understands: `/chiron on`, `/chiron off`, `/chiron status`, `/chiron reset`, `/chiron level <setup|ai|code|business|general> <beginner|medium|pro|maximum>`.
