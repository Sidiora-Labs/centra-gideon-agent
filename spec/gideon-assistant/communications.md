# Communications implementation specification

Give Gideon one understandable place for messages, meetings, people, channels, and items needing attention.
Preserve each connected account's identity, the source record behind every item, and the user's control over
external actions.

Communications opens from the labelled Apps destination and from relevant Chat and Activity cards. Mail and
Calendar also retain direct utility shortcuts. The shared shell owns navigation, identity, focus restoration,
and responsive workspace framing; Communications owns the records and actions below.

The implementation uses Gideon's existing communication, inbox, notification, and approval services. The new
assistant interface presents those native records through typed operations. A message, event, contact, review,
or notification keeps its original source kind and identifier. The visible interface never treats an imported
archive as a live send-enabled account.

## Sequential implementation tasks

- [ ] **communications-01 — Establish typed communication and account contracts.**
  - Add `apps/assistant/src/features/communications/communicationClient.ts` and
    `apps/assistant/src/features/communications/types.ts` with named reads and writes for accounts, mail,
    calendars, people, channels, inbox items, notifications, and reviews.
  - Represent a provider item with account ID, provider kind, source kind, provider-native ID, readiness,
    freshness, and allowed actions. Never use a provider-native ID alone as a global key or route target.
  - Map configured, connected, read-only, expired, unavailable, importing, and failed states to distinct display
    states. Preserve the last known item with a stale indicator when a refresh fails.
  - The module uses the shell identity/session and delivery bootstrap contracts; provider or account changes
    clear account-specific cached records and drafts from the previous selection.
  - Acceptance: switching two accounts that contain the same provider message ID cannot cross-open, merge, send,
    or mark the wrong item; unavailable providers display no sample content as live data.

- [ ] **communications-02 — Build mail and draft journeys.**
  - Add `apps/assistant/src/features/communications/MailWorkspace.web.tsx` and
    `apps/assistant/src/features/communications/MailDetail.web.tsx`. Adapt the established mail list, search,
    unread, draft, compose, and detail patterns to Gideon's mailbox and outbound-email records.
  - Show account, sender, recipients, subject, timestamps, attachment names, thread history, and read state. A
    draft stores its source account and reopens with its edit history; a send request cannot silently switch
    accounts.
  - Compose and reply move through a review state that names the exact account, To/Cc/Bcc, body, attachments,
    and resulting action. Edits invalidate the earlier approval and create a newly reviewable action.
  - Empty mail, empty filtered results, disconnected account, loading, and failed retrieval have separate
    messages and recovery controls. Keyboard users can search, open a message, return, and restore focus and
    list position.
  - Acceptance: a reviewed outbound action sends once through Gideon's governed path, reports its durable
    result, and a reload shows the same draft or sent outcome without duplicate sends.

- [ ] **communications-03 — Build calendar views and event review.**
  - Add `apps/assistant/src/features/communications/CalendarWorkspace.web.tsx` and
    `apps/assistant/src/features/communications/EventDetail.web.tsx` with agenda, week, and month navigation and
    clear event detail.
  - Keep account and calendar choice, access role, source event ID, recurrence identity, time zone, attendees,
    location, and sync freshness visible. Read-only calendars disable edit controls with a reason.
  - Create, edit, and delete show the exact calendar, date and time zone, attendee effect, conflict information,
    and approval result before any provider write. Changed event details require a fresh review.
  - Handle daylight-saving boundaries, all-day end dates, recurring occurrences, no events, stale feeds, and
    failed synchronization without losing the selected date or draft.
  - Acceptance: an event opened from a person or notification resolves to the correct account and occurrence;
    approval applies only to the reviewed target and revision.

- [ ] **communications-04 — Own People and contact actions.**
  - Add `apps/assistant/src/features/communications/PeopleWorkspace.web.tsx` and
    `apps/assistant/src/features/communications/PersonDetail.web.tsx`. Adapt native People records, identity
    aliases, care cadence, imported contacts, touchpoints, and timeline into a searchable list and person
    detail.
  - Show where each identity came from and require explicit review before combining records that may represent
    different people. Keep revision checks on edits and preserve distinct provider identities after a merge.
  - People owns contact records, provider identities, and contact actions. The personal feature may link to a
    person and display a summary or reflection, but does not create a second contact store.
  - A contact with no channels, no history, or disconnected provider remains useful for notes and manual
    touchpoints; the interface labels which actions are unavailable.
  - Acceptance: person links from mail, calendar, and personal context resolve to the same native person record;
    an ambiguous address never silently joins two people.

- [ ] **communications-05 — Surface channels and imported archives honestly.**
  - Add `apps/assistant/src/features/communications/ChannelsWorkspace.web.tsx` for channel status, thread lists,
    source filters, and direct account setup or reconnection.
  - Retain dedicated paths for supported live channels and imported desktop, people, and message archives; show
    the source, last successful sync, read versus send capability, and connection state for each.
  - Channel threads open with provider and account context. Sending or replying uses the channel's native
    permission and review path; archived data has no send affordance.
  - Preserve useful existing channel-specific controls in focused detail routes instead of reducing them to a
    generic message list. Missing permissions, expired credentials, partial imports, and sync errors have
    explicit next actions.
  - Acceptance: a user can tell whether a thread is current and actionable before opening it, and an archive
    item cannot be submitted as a live reply.

- [ ] **communications-06 — Join inbox and notifications without losing source actions.**
  - Add `apps/assistant/src/features/communications/InboxWorkspace.web.tsx` and
    `apps/assistant/src/features/communications/NotificationCenter.web.tsx`. Preserve native inbox open, seen,
    dismiss, draft, apply, reply, and restore actions, with each item linked to its source and review record.
  - The header count and Activity entry use the same native item IDs and current statuses. Opening an item marks
    only that item seen; dismiss, apply, and send remain separate decisions with visible outcomes.
  - Keep proactive digest content read-only until the user explicitly chooses a reply. Device push availability
    is shown separately from in-app notification state.
  - Filters explain zero matches versus an empty inbox. Failed actions retain the item and report how to retry;
    stale counts reconcile from a fresh snapshot after reconnect.
  - Acceptance: a review reached from Chat, Activity, inbox, or a notification opens one exact native action and
    all entry points reflect its final status.

- [ ] **communications-07 — Make review and reconnection safe across accounts.**
  - Add `apps/assistant/src/features/communications/ActionReview.web.tsx` and
    `apps/assistant/src/features/communications/ConnectionRecovery.web.tsx` as shared communication details.
  - Review displays exact target, account, recipients or attendees, content, attachments, expected revision or
    decision hash, expiration, and the effect of approval or denial. Approve, deny, edit, and retry are explicit
    and mutually coherent states.
  - If a connection expires during review, preserve the draft and source reference, offer reconnection, then
    reload permissions and target revision before a fresh confirmation. Never reuse approval from the former
    account or an older draft.
  - Report pending, applied, denied, expired, superseded, and failed outcomes distinctly. A retry uses an
    idempotent request identity and never assumes that a timeout means no provider action occurred.
  - Acceptance: account switch, edited content, stale revision, expired review, and network recovery cannot
    approve a different external action than the one displayed.

- [ ] **communications-08 — Complete the integrated responsive journey.**
  - Wire labelled Communications, Mail, Calendar, People, Channels, Inbox, and Notifications routes through
    `apps/assistant/src/features/communications/routes.web.tsx`; preserve deep links to original native records
    and a clear return to Chat or Activity.
  - Use the shared workspace frame for desktop detail panels and full-screen mobile routes or focused sheets.
    Keep labels, status, and the primary action visible at tablet and phone widths.
  - Implement keyboard order, focus return, screen-reader names and live status announcements for changing
    counts, review results, failures, and reconnect progress. Preserve drafts and selected filters across route
    changes and reloads.
  - Acceptance: a user can start in Chat, open a message or event, find its person and account, review an
    outbound action, return to the same conversation, and see one durable outcome in inbox and Activity.
  - Complete the native-record, account-isolation, exact-review, empty/error, keyboard, and responsive behavior
    checks declared for this feature before calling the journey done.

Communications depends on the shell and delivery foundations. It consumes navigation and typed Activity entry
contracts from shell, discovery, activity, and conversation as those features arrive, without making their later
task completion a prerequisite for the first communication contract. The personal feature consumes
Communications person links and summaries; it does not own People writes.

Hosted adaptation keeps account scope, connection readiness, approval authorization, and managed policy visible
while using the same interface and native records. Connections that an administrator manages remain labelled as
managed. Distribution-specific authentication and route policy belong to delivery.
