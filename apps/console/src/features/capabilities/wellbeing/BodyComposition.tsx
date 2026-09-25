import { useEffect, useState, type FormEvent } from 'react'
import { requestJson } from '../../../shared/data/gatewayRequest'
import { useUILanguage } from '../../../shared/i18n'

type Values = { muscle_percent: number; fat_percent: number; bone_mass: { value: number; unit: string }; temperature: { value: number; unit: string } }
type RecordRow = { id: string; revision: number; observed_at: string; source: string; notes: string; original_values: Values; normalized_values: { muscle_percent: number; fat_percent: number; bone_mass_kg: number; temperature_c: number } }
const words = {
  en: ['Body composition observations', 'Authored measurements only. This record does not provide medical advice or duplicate body weight.', 'Loading…', 'New observation', 'Export records', 'No body composition observations', 'Observed at', 'Source', 'Muscle percent', 'Fat percent', 'Bone mass', 'Bone mass unit', 'Temperature', 'Temperature unit', 'Notes', 'Save correction', 'Save observation', 'Canonical record', 'History', 'Canonical export', 'muscle', 'fat', 'bone'],
  es: ['Observaciones de composición corporal', 'Solo mediciones registradas. Este registro no ofrece consejo médico ni duplica el peso corporal.', 'Cargando…', 'Nueva observación', 'Exportar registros', 'Sin observaciones de composición corporal', 'Observado el', 'Fuente', 'Porcentaje muscular', 'Porcentaje de grasa', 'Masa ósea', 'Unidad de masa ósea', 'Temperatura', 'Unidad de temperatura', 'Notas', 'Guardar corrección', 'Guardar observación', 'Registro canónico', 'Historial', 'Exportación canónica', 'músculo', 'grasa', 'hueso'],
  ar: ['ملاحظات تكوين الجسم', 'قياسات مدخلة فقط. لا يقدم هذا السجل نصيحة طبية ولا يكرر وزن الجسم.', 'جار التحميل…', 'ملاحظة جديدة', 'تصدير السجلات', 'لا توجد ملاحظات لتكوين الجسم', 'وقت الملاحظة', 'المصدر', 'نسبة العضلات', 'نسبة الدهون', 'كتلة العظام', 'وحدة كتلة العظام', 'درجة الحرارة', 'وحدة الحرارة', 'ملاحظات', 'حفظ التصحيح', 'حفظ الملاحظة', 'السجل الأساسي', 'السجل', 'التصدير الأساسي', 'عضلات', 'دهون', 'عظام'],
  hi: ['शारीरिक संरचना अवलोकन', 'केवल दर्ज माप। यह रिकॉर्ड चिकित्सीय सलाह नहीं देता और शरीर के वजन की नकल नहीं करता।', 'लोड हो रहा है…', 'नया अवलोकन', 'रिकॉर्ड निर्यात करें', 'कोई शारीरिक संरचना अवलोकन नहीं', 'अवलोकन समय', 'स्रोत', 'मांसपेशी प्रतिशत', 'वसा प्रतिशत', 'अस्थि द्रव्यमान', 'अस्थि द्रव्यमान इकाई', 'तापमान', 'तापमान इकाई', 'टिप्पणियाँ', 'सुधार सहेजें', 'अवलोकन सहेजें', 'प्रामाणिक रिकॉर्ड', 'इतिहास', 'प्रामाणिक निर्यात', 'मांसपेशी', 'वसा', 'अस्थि'],
  'zh-CN': ['身体成分观察', '仅记录用户输入的测量值。本记录不提供医疗建议，也不重复记录体重。', '加载中…', '新建观察', '导出记录', '暂无身体成分观察', '观察时间', '来源', '肌肉百分比', '脂肪百分比', '骨量', '骨量单位', '温度', '温度单位', '备注', '保存更正', '保存观察', '规范记录', '历史', '规范导出', '肌肉', '脂肪', '骨量'],
}

export default function BodyComposition({ baseUrl = '' }: { baseUrl?: string }) {
  const w = words[useUILanguage()]
  const base = `${baseUrl}/api/capabilities/wellbeing/body-composition`
  const [records, setRecords] = useState<RecordRow[]>([]), [selected, setSelected] = useState<RecordRow | null>(null), [history, setHistory] = useState<RecordRow[]>([])
  const [observed, setObserved] = useState(''), [source, setSource] = useState(''), [notes, setNotes] = useState('')
  const [muscle, setMuscle] = useState(''), [fat, setFat] = useState(''), [bone, setBone] = useState(''), [boneUnit, setBoneUnit] = useState('kg'), [temperature, setTemperature] = useState(''), [temperatureUnit, setTemperatureUnit] = useState('C')
  const [requestId, setRequestId] = useState(() => crypto.randomUUID()), [busy, setBusy] = useState(false), [error, setError] = useState(''), [exported, setExported] = useState('')
  const run = async (action: () => Promise<void>) => { setBusy(true); setError(''); try { await action() } catch (reason) { setError(String(reason)) } finally { setBusy(false) } }
  const load = async () => setRecords((await requestJson<{ records: RecordRow[] }>(base)).records)
  const open = async (id: string) => {
    const row = await requestJson<RecordRow>(`${base}/${encodeURIComponent(id)}`)
    const versions = await requestJson<{ history: RecordRow[] }>(`${base}/${encodeURIComponent(id)}/history`)
    setSelected(row); setObserved(row.observed_at); setSource(row.source); setNotes(row.notes)
    setMuscle(String(row.original_values.muscle_percent)); setFat(String(row.original_values.fat_percent))
    setBone(String(row.original_values.bone_mass.value)); setBoneUnit(row.original_values.bone_mass.unit)
    setTemperature(String(row.original_values.temperature.value)); setTemperatureUnit(row.original_values.temperature.unit)
    setHistory(versions.history); setRequestId(crypto.randomUUID())
    const params = new URLSearchParams(location.hash.split('?')[1] || ''); params.set('view', 'body-composition'); params.set('id', row.id); window.history.replaceState(null, '', `#/capabilities/wellbeing?${params}`)
  }
  useEffect(() => { void run(async () => { await load(); const id = new URLSearchParams(location.hash.split('?')[1] || '').get('id'); if (id) await open(id) }) }, [])
  const reset = () => { setSelected(null); setHistory([]); setObserved(''); setSource(''); setNotes(''); setMuscle(''); setFat(''); setBone(''); setBoneUnit('kg'); setTemperature(''); setTemperatureUnit('C'); setRequestId(crypto.randomUUID()); window.history.replaceState(null, '', '#/capabilities/wellbeing?view=body-composition') }
  const save = async (event: FormEvent) => {
    event.preventDefault()
    await run(async () => {
      const values: Values = { muscle_percent: Number(muscle), fat_percent: Number(fat), bone_mass: { value: Number(bone), unit: boneUnit }, temperature: { value: Number(temperature), unit: temperatureUnit } }
      const payload = { request_id: requestId, ...(selected ? { revision: selected.revision } : { source }), observed_at: observed, values, notes }
      const row = await requestJson<RecordRow>(selected ? `${base}/${selected.id}` : base, selected ? 'PUT' : 'POST', payload)
      await load(); await open(row.id)
    })
  }
  const download = async () => { await run(async () => { const value = await requestJson<object>(`${base}/export`); setExported(JSON.stringify(value, null, 2)) }) }
  return <main style={{ maxWidth: 900, marginInline: 'auto', padding: 20 }} aria-busy={busy}>
    <h1>{w[0]}</h1>
    <p>{w[1]}</p>
    {error && <p role="alert">{error}</p>}{busy && <p role="status">{w[2]}</p>}
    <button onClick={reset}>{w[3]}</button> <button onClick={() => void download()}>{w[4]}</button>
    <ul>{records.map(row => <li key={row.id}><button onClick={() => void run(() => open(row.id))}>{row.observed_at} · {row.source}</button></li>)}</ul>
    {!busy && records.length === 0 && <p>{w[5]}</p>}
    <form onSubmit={save} style={{ display: 'grid', gap: 10 }}>
      <label>{w[6]}<input required value={observed} onChange={event => setObserved(event.target.value)} placeholder="2026-09-25T08:00:00Z" /></label>
      <label>{w[7]}<input required disabled={!!selected} value={source} onChange={event => setSource(event.target.value)} /></label>
      <label>{w[8]}<input required type="number" min="0" max="100" step="any" value={muscle} onChange={event => setMuscle(event.target.value)} /></label>
      <label>{w[9]}<input required type="number" min="0" max="100" step="any" value={fat} onChange={event => setFat(event.target.value)} /></label>
      <label>{w[10]}<input required type="number" min="0" step="any" value={bone} onChange={event => setBone(event.target.value)} /></label>
      <label>{w[11]}<select aria-label={w[11]} value={boneUnit} onChange={event => setBoneUnit(event.target.value)}>{['kg', 'g', 'lb'].map(value => <option key={value}>{value}</option>)}</select></label>
      <label>{w[12]}<input required type="number" step="any" value={temperature} onChange={event => setTemperature(event.target.value)} /></label>
      <label>{w[13]}<select aria-label={w[13]} value={temperatureUnit} onChange={event => setTemperatureUnit(event.target.value)}>{['C', 'F', 'K'].map(value => <option key={value}>{value}</option>)}</select></label>
      <label>{w[14]}<textarea value={notes} onChange={event => setNotes(event.target.value)} /></label>
      <button disabled={busy}>{selected ? w[15] : w[16]}</button>
    </form>
    {selected && <section><h2>{w[17]}</h2><p>{selected.original_values.muscle_percent}% {w[20]} · {selected.original_values.fat_percent}% {w[21]} · {selected.original_values.bone_mass.value} {selected.original_values.bone_mass.unit} {w[22]} · {selected.original_values.temperature.value} °{selected.original_values.temperature.unit}</p><p>{selected.normalized_values.bone_mass_kg} kg {w[22]} · {selected.normalized_values.temperature_c} °C · {selected.source}</p><h3>{w[18]}</h3><ol>{history.map(row => <li key={row.revision}>v{row.revision}: {row.original_values.temperature.value} °{row.original_values.temperature.unit} · {row.notes}</li>)}</ol></section>}
    {exported && <section><h2>{w[19]}</h2><pre>{exported}</pre></section>}
  </main>
}
