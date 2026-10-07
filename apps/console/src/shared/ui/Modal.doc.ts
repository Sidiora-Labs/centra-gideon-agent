import type { UiDoc } from './uiDoc'

const doc: UiDoc = {
  name: 'Modal',
  keywords: ['modal', 'dialog', 'sheet', 'overlay', 'scrim', 'popup', 'portal', 'centered'],
  description:
    'The reusable centered modal with a scrim. A pinned header carries the title + a single close (X) button; Escape and a scrim click also dismiss it, focus is trapped inside, and only the body scrolls. Portaled to <body> so position:fixed centers against the VIEWPORT (an animated/transformed ancestor like the composer or glow would otherwise capture the fixed positioning and push it off-center). Width tracks the content column, a touch wider for reading.',
  props: [
    { name: 'children', description: 'The scrolling modal body.' },
    { name: 'chrome', description: 'Standard titled header (default), or media chrome for fullscreen viewers: accessible title remains visually hidden, body fills the viewport with no padding, black scrim, native close toolbar, initial close focus.' },
    { name: 'closeLabel', description: 'Accessible name for the native close control; defaults to Close.' },
    { name: 'headerActions', description: 'Optional real controls placed before Close in the standard header, such as Conversation and History navigation.' },
    { name: 'mediaControls', description: 'Optional real viewer controls placed before the native close action in the fullscreen media toolbar.' },
    { name: 'presentation', description: 'Centered (default), right drawer (18rem, capped at 85vw), bottom-sheet (40rem, capped at 80dvh), or fullscreen. Every presentation retains the titled header and scrolling body.' },
    { name: 'open', description: 'Controlled visibility; defaults to true. Only open modals trap focus, handle Escape, or lock scrolling.' },
    { name: 'keepMounted', description: 'Keep children mounted while closed, hidden and inert, for persistent runtime controllers. Defaults to false.' },
    { name: 'initialFocus', description: 'Optional ref to an enabled element inside the modal that receives focus when it opens. Defaults to the first available control.' },
    { name: 'restoreFocus', description: 'Optional ref to the opener that receives focus when the modal closes. Defaults to the element focused when it opens.' },
    { name: 'dismissOnBackdrop', description: 'Allow scrim clicks to request close; defaults to true. Set false for media viewers or dialogs requiring explicit dismissal.' },
    { name: 'lockBodyScroll', description: 'Prevent page scrolling while open; defaults to false. Nested locks restore the original overflow only after the final locking modal closes.' },
    { name: 'icon', description: 'Optional leading node beside the title in the header.' },
    { name: 'layoutId', description: 'Shared-element id: when the opening trigger renders a motion.* with the same layoutId, the sheet morphs OUT of that element instead of scaling from center.' },
    { name: 'onClose', description: 'Called when the X button, Escape, or an enabled scrim dismissal requests close. Required; update open state or unmount in response.' },
    { name: 'title', description: 'The modal heading, shown in the pinned header (also the aria-label when a string).' },
  ],
  bestPractices: [
    { guidance: true, description: 'Reach for Modal for any centered dialog rather than hand-rolling a fixed overlay — the scrim, Escape/scrim-click dismiss, focus trap, viewport centering, and enter/exit motion come built in.' },
    { guidance: true, description: 'Wire onClose to your open-state so Escape and scrim clicks close the modal (it does not manage its own open state).' },
    { guidance: true, description: 'Pass a matching `layoutId` on both the trigger (a motion.*) and the Modal to get the "grow from the trigger" shared-element morph.' },
    { guidance: false, description: 'Do not wrap Modal in a transformed/animated ancestor expecting it to center there — it portals to <body> on purpose; center offset comes from the viewport.' },
    { guidance: false, description: 'Do not hardcode colors or px in className — everything routes through design tokens (the token-lint ratchet fails the build otherwise).' },
  ],
  anatomy: ['createPortal to <body>', 'fixed full-screen flex-center container', 'blurred scrim (click-to-close)', 'motion.div sheet (squircle, expressiveness-scaled overshoot; layoutId morph)', 'pinned header (icon • title • close X)', 'scrolling body'],
}

export default doc
