export type LoraItem = { id: string; sha256: string; bytes: number; base_model: string; compatibility: string; trigger_words: string; effect_verified: boolean }
export type LoraInventory = { items: LoraItem[]; invalid: { id: string; error: string }[]; truncated: boolean; supports_lora: boolean; selected_base_model: string }
export default function LoraPicker({ inventory, selected, change }: { inventory: LoraInventory; selected: Record<string, string>; change: (id: string, value: string | null) => void }) {
  return <fieldset className="space-y-2"><legend>Installed LoRA adapters</legend><p>Compatibility compares declared base-model metadata only. Tensor loading and image effect have not been verified.</p>
    {!inventory.supports_lora && <p role="status">The selected model does not advertise LoRA support.</p>}
    {inventory.items.length === 0 && <p>No installed adapters discovered.</p>}
    {inventory.items.map(item => <article key={item.id} className="rounded-lg bg-surface-high p-m"><label><input type="checkbox" disabled={!inventory.supports_lora || item.compatibility !== 'metadata_match'} checked={item.id in selected} onChange={event => change(item.id, event.target.checked ? '1' : null)} />{item.id}</label>
      <p>{item.compatibility} · base model: {item.base_model || 'Unknown'} · {item.bytes} bytes</p><p>SHA-256: {item.sha256}</p>{item.trigger_words && <p>Trigger words: {item.trigger_words}</p>}
      {item.id in selected && <label>Adapter scale<input type="number" min="-2" max="2" step="0.1" value={selected[item.id]} onChange={event => change(item.id, event.target.value)} /></label>}
    </article>)}
    {inventory.invalid.map(item => <p role="alert" key={item.id}>{item.id}: {item.error}</p>)}{inventory.truncated && <p>Discovery limit reached; some files were not inspected.</p>}
  </fieldset>
}
