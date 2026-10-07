# Working inside a chat

Chat controls operate on the selected session and its current state. Availability depends
on memory mode, ownership, activity, provider capabilities, and platform support. A
missing or disabled control can carry a specific reason; do not assume every unavailable
feature is simply hidden.

## Rewind and branch

Use the message actions to rewind an earlier user turn or branch from a selected message.
Rewind replaces the active continuation after the selected point; retained rewind variants
can be inspected and restored into a new session. Branch creates a separate conversation
without replacing the original. Stored lineage supplies the link to the source session.

These operations are history edits, not reversals of external effects. A file write,
message delivery, or remote operation performed by a removed turn does not disappear
because its transcript was rewound. Memory/context selection must follow the active
branch and summary coverage, not an old continuation.

Fork admission checks ownership, supported persistent mode, and session capacity. Temporary
or Incognito sessions cannot be copied through the persistent transcript fork path. A
refusal is a result to inspect rather than permission to retry under a broader identity.
See [chat sessions](../architecture/CHAT_SESSIONS.md).

## Plan before execution

The composer offers **Plan this first** once the session supports it. Plan mode uses the
runtime task-mode gate to refuse mutating tools. It can still perform admitted read-only
work, so “plan” does not mean the runtime makes no model or tool calls.

Review the resulting plan, edit it or comment for a redraft, then approve or cancel using
the offered controls. Approval continues work in the same conversation under the actual
prior task mode and current grants. It does not grant standing permission to every action
mentioned in the plan. Re-planning during an active turn cooperatively parks work; it
cannot undo an effect already completed.

## Queued messages and interruption

Sending while a turn is active can queue the next message. Queued controls allow cancelling,
editing, or promoting a message with **Interrupt now**. Interruption requests a cooperative
stop and retains the next queued work. The **Stop** action has its own cancellation and
queue semantics. Neither is proof that an external process stopped instantly; inspect the
reported turn and process state.

A send acknowledgement represents accepted work, not a completed answer. The console
tracks the actual accepted turn and replay cursor so reconnect can recover its events.
Do not manually resubmit merely because a transport response was interrupted.

## Find, quote, and follow-ups

With a chat open, `Cmd+F` or `Ctrl+F` opens the conversation find bar. It searches loaded
rendered message text and tool titles, rather than every collapsed tool payload. Keyboard
controls move among matches and close the bar. Cross-session search is a separate session
list operation and follows current session access rules.

Selecting transcript text exposes quote/copy actions. A quote inserts attributed text into
the composer; it does not send until you submit it.

Follow-up chips can propose next messages after a reply. Selecting one allows editing;
its send action submits it. Generation uses a configured background model and is optional
in chat settings. It is skipped for restricted private sessions and unavailable models;
there is no guaranteed number of suggestions or fixed appearance delay. A suggestion is
not an automatic authorization to execute its task.

## Streaming text and reduced motion

Chat settings can select smooth reveal or immediate text updates. Reduced-motion and
animation preferences constrain reveal behavior. Display pacing does not alter the
recorded answer or imply the model is still generating after it has completed.

## Screen capture and attachments

The composer can offer screen-area capture when an actual capture path is available.
Browser capture selects a surface, takes a frame, stops its tracks, and permits cropping.
A supported host-native capture path can capture the gateway host's screen; it is not the
remote phone's screen merely because the phone opened the page.

Platform support and permission affect whether the control can be used. Do not assume a
browser feature or host OS guarantees successful capture. Inspect the offered reason and
cancel without attaching content when capture fails.

A captured image follows the ordinary upload/attachment path. Attaching it does not send
it until the message is submitted. Uploaded bytes and extraction are separate from the
model's ability to understand the image; the selected model and permissions still matter.

## Records and privacy

Server-side history edits, branches, plan transitions, interruption, uploads, and background
follow-up calls have their respective runtime/audit paths. Browser-local find and quote
operations do not themselves create a gateway tool invocation. Inspect the actual audit
record instead of assuming a fixed one-row count for every click or error path.

Temporary and Incognito have distinct persistence and execution contracts. Removing a
visible message, cancelling a turn, or closing a tab is not a general deletion guarantee
for uploads, external actions, or unrelated persistent memory. Use the applicable lifecycle
and deletion controls; see [memory](../architecture/KNOWLEDGE_MEMORY.md) and
[security](../architecture/SECURITY.md).
