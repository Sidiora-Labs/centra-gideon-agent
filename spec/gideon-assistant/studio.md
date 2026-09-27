# Gideon Studio implementation list

Studio gives creative work a durable home: a request can start in conversation, continue in an appropriate editor or canvas, and return a versioned result to the same conversation. Writing, slides, images, video, music, MIDI, worlds, 3D, and live media need distinct controls rather than a common form. This list describes implementation work; all tasks remain unchecked.

## 1. Open a real Studio workspace

- [ ] Build the Studio destination and responsive workspace frame.
  - Give Writer, Slides, Image, Video, Music, MIDI, Worlds, 3D, Assets, Jobs, and Live Media labelled entry points under Apps and relevant conversation results.
  - Keep the assistant header, theme, navigation, and return context while giving timelines, editors, and canvases their full usable width and height.
  - Make recent projects and in-progress jobs open the exact saved record rather than an overview.
  - Preserve a draft, selected asset, viewport, and scroll position when users move between a workspace and conversation.
  - Show a useful first action when a collection is empty, and explain missing access without rendering sample data.
  - Provide keyboard reachability, named controls, visible focus, and mobile routes for the actions each modality can support.
  - Implement in proposed `apps/assistant/src/features/studio/StudioWorkspace.web.tsx` and `apps/assistant/src/features/studio/studioRoutes.ts`.
  - Reuse existing `apps/console/src/features/capabilities/creative/Page.tsx`, `media/Page.tsx`, `music/Page.tsx`, and `experience/Page.tsx` through the shared shell module boundary.
  - Depends on shell and delivery foundations; later record links depend on conversation and discovery.

## 2. Write and revise manuscripts

- [ ] Connect Writer to saved works, stories, series, and manuscripts.
  - Start from a blank work, an existing work, or a conversation request; load the same record and current revision in each case.
  - Provide a text editor with outline/context, save state, stage, version history, and explicit restore or fork actions.
  - Keep editorial suggestions distinct from accepted text; adopting a suggestion creates a visible revision.
  - Warn on unsaved changes before navigating away and offer recovery after a failed save or reload.
  - Preview the result as a document and export a selected revision to the available document formats.
  - Report a missing format writer or render failure in the export flow with a retry path and no false download.
  - Return a manuscript card to conversation with project, revision, and export links.
  - Extend `apps/console/src/features/capabilities/creative/Works.tsx` and `ManuscriptExports.tsx` through proposed `apps/assistant/src/features/studio/WriterWorkspace.web.tsx`.
  - Depends on the Studio route and conversation result contract.

## 3. Compose and deliver slides

- [ ] Give Slides a slide-aware editing and preview journey.
  - Create a deck from an outline or open a saved deck; show slide order, title, bullets, notes, and template or image choices.
  - Support insert, reorder, edit, duplicate, and remove with an undoable local draft before save.
  - Preview each slide at presentation proportions and expose missing images or unsupported layout content where it occurs.
  - Save a named version before rendering; reopening and restoring retains slide IDs and notes.
  - Export a selected version to a supported presentation format and link the resulting artifact back to the originating conversation.
  - Show a specific render error and preserve the editable deck if a writer is unavailable or rendering fails.
  - Use existing `apps/console/src/shared/ui/content/SlideDeck.tsx` and document artifact contracts in proposed `apps/assistant/src/features/studio/SlidesWorkspace.web.tsx`.
  - Depends on the Studio route and shared asset/version behavior.

## 4. Generate and edit images and video

- [ ] Connect Image and Video to actual provider readiness and media jobs.
  - Show available models, sizes, durations, aspect ratios, source assets, masks, and controls only when the selected provider supports them.
  - Let an image request use an existing asset/version as source and preserve the exact inputs for a reproducible variant.
  - Let a video request choose references and use a timeline or shot view for ordered clips and revisions.
  - Submit a job once, show queued/running/failed/complete states, and reconnect to the same job after navigation or reload.
  - Preview complete media with accessible playback controls and open its asset history and download action.
  - Distinguish no provider, provider needs setup, unsupported option, failed job, and expired source with a relevant recovery action.
  - Keep incomplete jobs in Jobs and never present a request receipt as a finished image or video.
  - Extend `apps/console/src/features/capabilities/media/ImagePage.tsx`, `VideoPage.tsx`, `TimelinePage.tsx`, `Readiness.tsx`, and `JobsPage.tsx` through proposed `apps/assistant/src/features/studio/MediaWorkspace.web.tsx`.
  - Depends on the Studio route, conversation result contract, and asset/version behavior.

## 5. Make music and MIDI usable as compositions

- [ ] Build Music and MIDI workspaces around listening, editing, and delivery.
  - Open a composition, catalog track, or score by stable ID with title, source, revision, and linked assets.
  - Show music generation provider readiness and job progress before a playable result appears.
  - Offer a focused playback view with transport, duration, track details, and an accessible non-audio description where available.
  - Edit MIDI notes with pitch, timing, velocity, and instrument controls; preview the changed score before committing a version.
  - Keep unsaved MIDI edits when a provider or preview fails, and show what can still be saved or exported locally.
  - Export an explicitly chosen score or audio version with format and provenance visible.
  - Extend `apps/console/src/features/capabilities/music/GenerationPage.tsx`, `CatalogPage.tsx`, `ListeningPage.tsx`, and `MidiPage.tsx` through proposed `apps/assistant/src/features/studio/MusicWorkspace.web.tsx`.
  - Depends on the Studio route, Jobs, and asset/version behavior.

## 6. Build worlds and inspect 3D assets

- [ ] Give Worlds and 3D separate canvases with common project continuity.
  - Open a world, story, scene, model, or assembly from a project or result card at the saved selection.
  - For worlds, show map or scene state, project assets, preview status, and explicit compile/publish steps with their own outcomes.
  - For 3D, show orbit/pan/zoom, model parts, animation clips, assembly editing, and a revision history before export.
  - Keep browser canvas controls usable with keyboard and provide a labelled structure list when pointer controls are unavailable.
  - Distinguish a missing model, unsupported asset, failed compile, and provider unavailable state; preserve the editable project and retry target.
  - Make published output and downloaded files link to the exact source version and project.
  - Extend `apps/console/src/features/capabilities/experience/Worlds.tsx`, `WorldEngine.tsx`, `GameAssets.tsx`, and `apps/console/src/features/capabilities/music/AssemblyPage.tsx`, `Models3DPage.tsx` through proposed `apps/assistant/src/features/studio/WorldWorkspace.web.tsx`.
  - Depends on the Studio route, Jobs, and asset/version behavior.

## 7. Connect assets, versions, jobs, and exports

- [ ] Give every Studio modality a consistent result lifecycle.
  - Show an asset library with type, owner, origin project, source conversation/run, revision, preview, and usable actions.
  - Show version lineage and compare or restore only where the record supports it; keep prior versions addressable.
  - Aggregate generation/render/export jobs by native ID and status without inventing a second job store.
  - Make retry, cancel, download, and reopen actions conditional on the real job and artifact state.
  - Validate format, permission, and source version before an export; show a usable error if an artifact has gone missing.
  - Let conversation and Activity result cards open the exact asset, version, job, or editor and return without losing place.
  - Add proposed `apps/assistant/src/features/studio/StudioAssets.web.tsx` and `studioRecords.ts`; reuse existing `apps/console/src/features/capabilities/media/LibraryPage.tsx`, `JobsPage.tsx`, and `apps/console/src/shared/ui/content/exporters.ts`.
  - Depends on shell, conversation, discovery, and the modality workspaces.

## 8. Complete live media experiences

- [ ] Integrate voice, avatar, and calling destinations with clear availability and control.
  - Keep core conversation voice controls in Conversation; open dedicated narration, avatar, and call experiences from Studio when their providers are ready.
  - Show input/output device and provider status before starting a live session; require explicit user action for microphone or camera access.
  - Expose start, mute, stop, and reconnect states, with a clear owner for any audible output.
  - Keep session state and saved outputs linked to the source project or conversation when supported.
  - Recover visibly from denied permission, disconnect, unavailable provider, or unsupported browser; leave existing creative drafts intact.
  - Provide labelled controls, caption/transcript access where available, and a non-live alternative when an experience cannot run.
  - Reuse `apps/console/src/features/capabilities/experience/AvatarPanel.tsx`, `NativeCalls.tsx`, and `Narration.tsx` via proposed `apps/assistant/src/features/studio/LiveMediaWorkspace.web.tsx`.
  - Depends on the Studio route and conversation session contract.
