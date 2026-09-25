import { useEffect, useRef, useState, type FormEvent } from 'react'
import { requestJson } from '../../../shared/data/gatewayRequest'
import { Button } from '../../../shared/ui/Button'
import { Field, Select, TextArea, TextInput } from '../../../shared/ui/forms'
import { Download, Plus, Scale } from 'lucide-react'

type Values = { muscle_percent: number; fat_percent: number; bone_mass: { value: number; unit: string }; temperature: { value: number; unit: string } }
type RecordRow = { id: string; revision: number; observed_at: string; source: string; notes: string; original_values: Values; normalized_values: { muscle_percent: number; fat_percent: number; bone_mass_kg: number; temperature_c: number } }
const words = {
  en: ['Body composition observations', 'Authored measurements only. This record does not provide medical advice or duplicate body weight.', 'Loading…', 'New observation', 'Export records', 'No body composition observations', 'Observed at', 'Source', 'Muscle percent', 'Fat percent', 'Bone mass', 'Bone mass unit', 'Temperature', 'Temperature unit', 'Notes', 'Save correction', 'Save observation', 'Canonical record', 'History', 'Canonical export', 'muscle', 'fat', 'bone'],
  es: ['Observaciones de composición corporal', 'Solo mediciones registradas. Este registro no ofrece consejo médico ni duplica el peso corporal.', 'Cargando…', 'Nueva observación', 'Exportar registros', 'Sin observaciones de composición corporal', 'Observado el', 'Fuente', 'Porcentaje muscular', 'Porcentaje de grasa', 'Masa ósea', 'Unidad de masa ósea', 'Temperatura', 'Unidad de temperatura', 'Notas', 'Guardar corrección', 'Guardar observación', 'Registro canónico', 'Historial', 'Exportación canónica', 'músculo', 'grasa', 'hueso'],
  ar: ['ملاحظات تكوين الجسم', 'قياسات مدخلة فقط. لا يقدم هذا السجل نصيحة طبية ولا يكرر وزن الجسم.', 'جار التحميل…', 'ملاحظة جديدة', 'تصدير السجلات', 'لا توجد ملاحظات لتكوين الجسم', 'وقت الملاحظة', 'المصدر', 'نسبة العضلات', 'نسبة الدهون', 'كتلة العظام', 'وحدة كتلة العظام', 'درجة الحرارة', 'وحدة الحرارة', 'ملاحظات', 'حفظ التصحيح', 'حفظ الملاحظة', 'السجل الأساسي', 'السجل', 'التصدير الأساسي', 'عضلات', 'دهون', 'عظام'],
  hi: ['शारीरिक संरचना अवलोकन', 'केवल दर्ज माप। यह रिकॉर्ड चिकित्सीय सलाह नहीं देता और शरीर के वजन की नकल नहीं करता।', 'लोड हो रहा है…', 'नया अवलोकन', 'रिकॉर्ड निर्यात करें', 'कोई शारीरिक संरचना अवलोकन नहीं', 'अवलोकन समय', 'स्रोत', 'मांसपेशी प्रतिशत', 'वसा प्रतिशत', 'अस्थि द्रव्यमान', 'अस्थि द्रव्यमान इकाई', 'तापमान', 'तापमान इकाई', 'टिप्पणियाँ', 'सुधार सहेजें', 'अवलोकन सहेजें', 'प्रामाणिक रिकॉर्ड', 'इतिहास', 'प्रामाणिक निर्यात', 'मांसपेशी', 'वसा', 'अस्थि'],
  'zh-CN': ['身体成分观察', '仅记录用户输入的测量值。本记录不提供医疗建议，也不重复记录体重。', '加载中…', '新建观察', '导出记录', '暂无身体成分观察', '观察时间', '来源', '肌肉百分比', '脂肪百分比', '骨量', '骨量单位', '温度', '温度单位', '备注', '保存更正', '保存观察', '规范记录', '历史', '规范导出', '肌肉', '脂肪', '骨量'],
}

function uiLanguage(): 'en' | 'es' | 'ar' | 'hi' | 'zh-CN' {
  const value = (typeof document !== 'undefined' && document.documentElement.lang) || (typeof navigator !== 'undefined' && navigator.language) || 'en'
  if (value.toLowerCase().startsWith('es')) return 'es'
  if (value.toLowerCase().startsWith('ar')) return 'ar'
  if (value.toLowerCase().startsWith('hi')) return 'hi'
  if (value.toLowerCase().startsWith('zh')) return 'zh-CN'
  return 'en'
}

export default function BodyComposition({ baseUrl = '' }: { baseUrl?: string }) {
  const w = words[uiLanguage()]
  const base = `${baseUrl}/api/capabilities/wellbeing/body-composition`
  const mounted = useRef(false), generation = useRef(0)
  const [records, setRecords] = useState<RecordRow[]>([]), [selected, setSelected] = useState<RecordRow | null>(null), [history, setHistory] = useState<RecordRow[]>([])
  const [observed, setObserved] = useState(''), [source, setSource] = useState(''), [notes, setNotes] = useState('')
  const [muscle, setMuscle] = useState(''), [fat, setFat] = useState(''), [bone, setBone] = useState(''), [boneUnit, setBoneUnit] = useState('kg'), [temperature, setTemperature] = useState(''), [temperatureUnit, setTemperatureUnit] = useState('C')
  const [requestId, setRequestId] = useState(() => crypto.randomUUID()), [busy, setBusy] = useState(false), [error, setError] = useState(''), [exported, setExported] = useState('')
  const [editing, setEditing] = useState(false)
  const current = (token: number) => mounted.current && generation.current === token
  const run = async (action: (token: number) => Promise<void>) => {
    const token = ++generation.current
    setBusy(true); setError('')
    try { await action(token) } catch (reason) { if (current(token)) setError(String(reason)) } finally { if (current(token)) setBusy(false) }
  }
  const load = async (token: number) => {
    const rows = (await requestJson<{ records: RecordRow[] }>(base)).records
    if (current(token)) setRecords(rows)
  }
  const open = async (id: string, token: number) => {
    const row = await requestJson<RecordRow>(`${base}/${encodeURIComponent(id)}`)
    const versions = await requestJson<{ history: RecordRow[] }>(`${base}/${encodeURIComponent(id)}/history`)
    if (!current(token)) return
    const params = new URLSearchParams(location.hash.split('?')[1] || '')
    if (params.get('view') !== 'body-composition') return
    setSelected(row); setEditing(true); setObserved(row.observed_at); setSource(row.source); setNotes(row.notes)
    setMuscle(String(row.original_values.muscle_percent)); setFat(String(row.original_values.fat_percent))
    setBone(String(row.original_values.bone_mass.value)); setBoneUnit(row.original_values.bone_mass.unit)
    setTemperature(String(row.original_values.temperature.value)); setTemperatureUnit(row.original_values.temperature.unit)
    setHistory(versions.history); setRequestId(crypto.randomUUID())
    params.set('id', row.id); window.history.replaceState(null, '', `#/capabilities/wellbeing?${params}`)
  }
  useEffect(() => {
    mounted.current = true
    const token = ++generation.current
    const id = new URLSearchParams(location.hash.split('?')[1] || '').get('id')
    setBusy(true); setError('')
    void (async () => {
      try { await load(token); if (id && current(token)) await open(id, token) }
      catch (reason) { if (current(token)) setError(String(reason)) }
      finally { if (current(token)) setBusy(false) }
    })()
    return () => { mounted.current = false; generation.current += 1 }
  }, [])
  const reset = () => { generation.current += 1; setBusy(false); setSelected(null); setEditing(true); setHistory([]); setObserved(''); setSource(''); setNotes(''); setMuscle(''); setFat(''); setBone(''); setBoneUnit('kg'); setTemperature(''); setTemperatureUnit('C'); setRequestId(crypto.randomUUID()); window.history.replaceState(null, '', '#/capabilities/wellbeing?view=body-composition') }
  const save = async (event: FormEvent) => {
    event.preventDefault()
    await run(async token => {
      const values: Values = { muscle_percent: Number(muscle), fat_percent: Number(fat), bone_mass: { value: Number(bone), unit: boneUnit }, temperature: { value: Number(temperature), unit: temperatureUnit } }
      const payload = { request_id: requestId, ...(selected ? { revision: selected.revision } : { source }), observed_at: observed, values, notes }
      const row = await requestJson<RecordRow>(selected ? `${base}/${selected.id}` : base, selected ? 'PUT' : 'POST', payload)
      await load(token); if (current(token)) await open(row.id, token)
    })
  }
  const download = async () => { await run(async token => { const value = await requestJson<object>(`${base}/export`); if (current(token)) setExported(JSON.stringify(value, null, 2)) }) }
  return <main style={{ maxWidth: 'var(--content-width)' }} className="mx-auto w-full space-y-2xl px-l py-2xl text-on-surface" aria-busy={busy}>
    <div className="flex flex-wrap items-start justify-between gap-l"><div><h2 data-type="title-m" className="text-on-surface">{w[0]}</h2><p data-type="body-m" className="mt-xs text-on-surface-var">{w[1]}</p></div><div className="flex gap-s"><Button onClick={reset}><Plus size={17} />{w[3]}</Button><Button variant="secondary" onClick={() => void download()}><Download size={17} />{w[4]}</Button></div></div>
    {error && <p role="alert">{error}</p>}{busy && <p role="status">{w[2]}</p>}
    <section className="rounded-lg border border-outline-variant/20 bg-surface-container p-l" aria-label={w[0]}>{!busy && records.length === 0 ? <div className="py-8 text-center"><Scale className="mx-auto mb-3 text-on-surface-low" /><p>{w[5]}</p><Button className="mt-4" onClick={reset}>{w[3]}</Button></div> : <ul className="divide-y divide-outline-variant/30">{records.map(row => <li key={row.id}><button className="flex w-full items-center justify-between py-3 text-left" onClick={() => void run(token => open(row.id, token))}><span>{new Date(row.observed_at).toLocaleDateString()} · {row.source}</span><strong>{row.original_values.muscle_percent}% {w[20]} · {row.original_values.fat_percent}% {w[21]}</strong></button></li>)}</ul>}</section>
    {editing && <form onSubmit={save} className="grid gap-m rounded-lg border border-outline-variant/20 bg-surface-container p-l sm:grid-cols-2">
      <Field label={w[6]}><TextInput required value={observed} onChange={setObserved} placeholder="2026-09-25T08:00:00Z" /></Field>
      <Field label={w[7]}><TextInput required disabled={!!selected} value={source} onChange={setSource} /></Field>
      <Field label={w[8]}><TextInput required type="number" min={0} max={100} value={muscle} onChange={setMuscle} /></Field>
      <Field label={w[9]}><TextInput required type="number" min={0} max={100} value={fat} onChange={setFat} /></Field>
      <Field label={w[10]}><TextInput required type="number" min={0} value={bone} onChange={setBone} /></Field>
      <Field label={w[11]}><Select ariaLabel={w[11]} value={boneUnit} onChange={setBoneUnit} options={['kg', 'g', 'lb'].map(value => ({ value, label: value }))} /></Field>
      <Field label={w[12]}><TextInput required type="number" value={temperature} onChange={setTemperature} /></Field>
      <Field label={w[13]}><Select ariaLabel={w[13]} value={temperatureUnit} onChange={setTemperatureUnit} options={['C', 'F', 'K'].map(value => ({ value, label: value }))} /></Field>
      <Field label={w[14]}><TextArea value={notes} onChange={setNotes} /></Field>
      <Button type="submit" disabled={busy}>{selected ? w[15] : w[16]}</Button>
    </form>}
    {selected && <section><h2>{w[17]}</h2><p>{selected.original_values.muscle_percent}% {w[20]} · {selected.original_values.fat_percent}% {w[21]} · {selected.original_values.bone_mass.value} {selected.original_values.bone_mass.unit} {w[22]} · {selected.original_values.temperature.value} °{selected.original_values.temperature.unit}</p><p>{selected.normalized_values.bone_mass_kg} kg {w[22]} · {selected.normalized_values.temperature_c} °C · {selected.source}</p><h3>{w[18]}</h3><ol>{history.map(row => <li key={row.revision}>v{row.revision}: {row.original_values.temperature.value} °{row.original_values.temperature.unit} · {row.notes}</li>)}</ol></section>}
    {exported && <section><h2>{w[19]}</h2><pre>{exported}</pre></section>}
  </main>
}
