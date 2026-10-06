
export type ArtifactApprovalView = {
  artifactId: string;
  version: string;
  publisherId: string;
  archiveSha256: string;
  sourceUri: string;
  installedVersion?: string;
  requestedCapabilities: string[];
  addedCapabilities: string[];
  rollbackVersion?: string;
};

type Props = {
  artifact: ArtifactApprovalView;
  busy?: boolean;
  onApprove: () => void;
  onCancel: () => void;
};

export function ArtifactApproval({ artifact, busy = false, onApprove, onCancel }: Props) {
  return (
    <section aria-labelledby="artifact-approval-title" className="space-y-4">
      <header>
        <h2 id="artifact-approval-title" className="text-lg font-semibold">
          Review artifact update
        </h2>
        <p className="text-sm text-muted-foreground">
          {artifact.artifactId} {artifact.installedVersion ?? "not installed"} → {artifact.version}
        </p>
      </header>
      <dl className="grid gap-2 rounded-lg border p-4 text-sm sm:grid-cols-2">
        <dt className="text-muted-foreground">Publisher</dt>
        <dd>{artifact.publisherId}</dd>
        <dt className="text-muted-foreground">SHA-256</dt>
        <dd className="break-all font-mono text-xs">{artifact.archiveSha256}</dd>
        <dt className="text-muted-foreground">Source</dt>
        <dd className="break-all">{artifact.sourceUri}</dd>
        <dt className="text-muted-foreground">Rollback target</dt>
        <dd>{artifact.rollbackVersion ?? "None"}</dd>
      </dl>
      <div className="rounded-lg border p-4">
        <h3 className="text-sm font-medium">Requested capabilities</h3>
        <ul className="mt-2 space-y-1 text-sm">
          {artifact.requestedCapabilities.map((capability) => (
            <li key={capability}>
              {capability}
              {artifact.addedCapabilities.includes(capability) ? " (new)" : ""}
            </li>
          ))}
        </ul>
      </div>
      <div className="flex justify-end gap-2">
        <button type="button" className="rounded-md border px-3.5 py-1.5 text-xs" onClick={onCancel} disabled={busy}>
          Cancel
        </button>
        <button type="button" className="rounded-md bg-primary px-3.5 py-1.5 text-xs text-primary-foreground" onClick={onApprove} disabled={busy}>
          Approve exact update
        </button>
      </div>
    </section>
  );
}

