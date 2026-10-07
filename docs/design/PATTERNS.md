# Console interaction patterns

The current [design](../../apps/console/DESIGN.md) and [product](../../apps/console/PRODUCT.md) contracts guide layout and behavior. Native reusable controls live under `apps/console/src/shared/ui/`. Their TypeScript interfaces are the authority for supported properties.

## Forms

[forms.tsx](../../apps/console/src/shared/ui/forms.tsx) exports `Field`, `FieldError`, `TextInput`, `TextArea`, `NumberField`, `DateInput`, `Select`, `Checkbox` and other form helpers. Use `Field` to associate labels and hints with compatible child controls.

Text and select callbacks receive values, rather than raw change events; checkbox callbacks receive a boolean. Preserve existing validation, required flags, read-only constraints, immediate updates and keyboard behavior when adopting them. Numeric fields have their own edit and commit behavior; they are not interchangeable with every raw number input. Specialized file, range or editor controls may require a different interface.

```tsx
// From a feature component; adjust the relative path to the actual file.
import { Field, TextInput } from '../../shared/ui/forms'

<Field label="Name" hint="Shown in the list">
  <TextInput value={name} onChange={setName} required />
</Field>
```

## Actions

[Button](../../apps/console/src/shared/ui/Button.tsx) and [IconButton](../../apps/console/src/shared/ui/IconButton.tsx) provide native action styling and shared activation guards. Preserve action authority in the owning handler: a visual disabled state is not server authorization. Give icon-only actions a meaningful accessible label.

Use actual loading state for an asynchronous operation. A disabled action with a prerequisite reason is a distinct state. Supply an accurate `disabledReason` and, where supported, a loading label rather than inventing busy status for selection or unavailable permissions.

## Dialogs

The [dialog helpers](../../apps/console/src/shared/ui/dialog/index.ts) expose `confirm`, `confirmDelete`, `promptInput`, `promptForm` and `alertDialog`. Their host renders the shared modal. Await a destructive confirmation before dispatch, and preserve cancellation without acknowledging an operation that has not occurred.

## Collections and recovery

[ListScaffold](../../apps/console/src/shared/ui/ListScaffold.tsx) supplies `EmptyState`, `LoadError`, `ListRow`, loading status and skeleton families. Empty data and failed loading are different states. Use a real retry callback for recoverable failures, retain the underlying error information, and do not turn an unavailable server into an empty collection.

A clickable row must retain its accessible action target and avoid stealing nested control events. Use current component signatures rather than copying historical `src/ui` examples.

## Motion and typography

Use [the motion vocabulary](MOTION.md) and current theme helpers. Reduced motion must preserve the action and state information. Typography and spacing tokens describe presentation; they do not replace meaningful labels or constraints.
