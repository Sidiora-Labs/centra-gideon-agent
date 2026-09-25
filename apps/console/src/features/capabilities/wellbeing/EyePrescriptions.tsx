import { useEffect, useRef, useState, type FormEvent } from 'react';
import { requestJson } from '../../../shared/data/gatewayRequest';
import { Button } from '../../../shared/ui/Button';
import { Field, TextArea, TextInput } from '../../../shared/ui/forms';
import { Download, Eye, Plus } from 'lucide-react';

type Eye = { sphere: number; sphere_unit: 'D'; cylinder: number; cylinder_unit: 'D'; axis: number; axis_unit: 'degrees' };
type Prescription = { id: string; observed_date: string; source: string; notes: string; left: Eye; right: Eye; revision: number; created_at: string; updated_at: string };
type Draft = { observed_date: string; source: string; notes: string; leftSphere: string; leftCylinder: string; leftAxis: string; rightSphere: string; rightCylinder: string; rightAxis: string };
const base = '/api/capabilities/wellbeing/eyes';
const blank = (): Draft => ({ observed_date: '', source: '', notes: '', leftSphere: '', leftCylinder: '', leftAxis: '', rightSphere: '', rightCylinder: '', rightAxis: '' });
const eye = (sphere: string, cylinder: string, axis: string): Eye => ({ sphere: Number(sphere), sphere_unit: 'D', cylinder: Number(cylinder), cylinder_unit: 'D', axis: Number(axis), axis_unit: 'degrees' });
const fromRecord = (row: Prescription): Draft => ({ observed_date: row.observed_date, source: row.source, notes: row.notes,
  leftSphere: String(row.left.sphere), leftCylinder: String(row.left.cylinder), leftAxis: String(row.left.axis),
  rightSphere: String(row.right.sphere), rightCylinder: String(row.right.cylinder), rightAxis: String(row.right.axis) });
const words = {
  en: ['Eye prescriptions', 'Record authored prescription values and their units. This record does not provide medical interpretation.', 'Loading…', 'New prescription', 'Observation date', 'Source', 'Left eye', 'Right eye', 'Sphere', 'Cylinder', 'Axis', 'Notes', 'Save correction', 'Save prescription', 'Recorded prescriptions', 'No eye prescriptions', 'Correction history', 'Export canonical JSON', 'Canonical eye prescription export', 'degrees'],
  es: ['Recetas oculares', 'Registra valores recetados y sus unidades. Este registro no ofrece interpretación médica.', 'Cargando…', 'Nueva receta', 'Fecha de observación', 'Fuente', 'Ojo izquierdo', 'Ojo derecho', 'Esfera', 'Cilindro', 'Eje', 'Notas', 'Guardar corrección', 'Guardar receta', 'Recetas registradas', 'Sin recetas oculares', 'Historial de correcciones', 'Exportar JSON canónico', 'Exportación canónica de recetas oculares', 'grados'],
  ar: ['وصفات العيون', 'سجّل قيم الوصفة ووحداتها. لا يقدم هذا السجل تفسيرًا طبيًا.', 'جارٍ التحميل…', 'وصفة جديدة', 'تاريخ الملاحظة', 'المصدر', 'العين اليسرى', 'العين اليمنى', 'القوة الكروية', 'الأسطوانة', 'المحور', 'ملاحظات', 'حفظ التصحيح', 'حفظ الوصفة', 'الوصفات المسجلة', 'لا توجد وصفات عيون', 'سجل التصحيحات', 'تصدير JSON القياسي', 'تصدير وصفة العين القياسي', 'درجة'],
  hi: ['नेत्र पर्चे', 'लिखे गए पर्चे के मान और उनकी इकाइयाँ दर्ज करें। यह रिकॉर्ड चिकित्सीय व्याख्या नहीं देता।', 'लोड हो रहा है…', 'नया पर्चा', 'अवलोकन तिथि', 'स्रोत', 'बाईं आँख', 'दाईं आँख', 'स्फीयर', 'सिलिंडर', 'अक्ष', 'टिप्पणियाँ', 'सुधार सहेजें', 'पर्चा सहेजें', 'दर्ज पर्चे', 'कोई नेत्र पर्चा नहीं', 'सुधार इतिहास', 'मानक JSON निर्यात करें', 'मानक नेत्र पर्चा निर्यात', 'डिग्री'],
  'zh-CN': ['眼镜处方', '记录处方数值及其单位。本记录不提供医学解读。', '加载中…', '新处方', '观察日期', '来源', '左眼', '右眼', '球镜', '柱镜', '轴位', '备注', '保存更正', '保存处方', '已记录处方', '暂无眼镜处方', '更正历史', '导出规范 JSON', '规范眼镜处方导出', '度'],
};

function EyeFields({ side, labels, draft, setDraft, disabled }: { side: 'left' | 'right'; labels: string[]; draft: Draft; setDraft: (next: Draft) => void; disabled: boolean }) {
  const prefix = side;
  const sideLabel = side === 'left' ? labels[6] : labels[7];
  const fields = [[labels[8], 'Sphere', 'D'], [labels[9], 'Cylinder', 'D'], [labels[10], 'Axis', labels[19]]] as const;
  return <fieldset disabled={disabled} className="grid gap-m"><legend data-type="label-l" className="mb-s">{sideLabel}</legend>{fields.map(([label, suffix, unit]) => {
    const key = `${prefix}${suffix}` as keyof Draft;
    return <Field key={key} label={`${sideLabel} ${label} (${unit})`}><TextInput required disabled={disabled} type="number" min={suffix === 'Axis' ? 0 : suffix === 'Cylinder' ? -20 : -40} max={suffix === 'Axis' ? 180 : suffix === 'Cylinder' ? 20 : 40} value={draft[key]} onChange={value => setDraft({ ...draft, [key]: value })} /></Field>;
  })}</fieldset>;
}

function uiLanguage(): 'en' | 'es' | 'ar' | 'hi' | 'zh-CN' {
  const value = (typeof document !== 'undefined' && document.documentElement.lang) || (typeof navigator !== 'undefined' && navigator.language) || 'en'
  if (value.toLowerCase().startsWith('es')) return 'es'
  if (value.toLowerCase().startsWith('ar')) return 'ar'
  if (value.toLowerCase().startsWith('hi')) return 'hi'
  if (value.toLowerCase().startsWith('zh')) return 'zh-CN'
  return 'en'
}

export default function EyePrescriptions() {
  const language = uiLanguage(), w = words[language];
  const mounted = useRef(false), generation = useRef(0);
  const [records, setRecords] = useState<Prescription[]>([]), [selected, setSelected] = useState<Prescription | null>(null);
  const [history, setHistory] = useState<Prescription[]>([]), [draft, setDraft] = useState<Draft>(blank());
  const [busy, setBusy] = useState(false), [error, setError] = useState(''), [exported, setExported] = useState('');
  const [editing, setEditing] = useState(false);
  const current = (token: number) => mounted.current && generation.current === token;
  async function run(action: (token: number) => Promise<void>) {
    const token = ++generation.current;
    setBusy(true); setError('');
    try { await action(token); } catch (reason) { if (current(token)) setError(String(reason)); } finally { if (current(token)) setBusy(false); }
  }
  async function load(token: number) {
    const rows = (await requestJson<{ prescriptions: Prescription[] }>(base)).prescriptions;
    if (current(token)) setRecords(rows);
  }
  async function select(id: string, token: number) {
    const row = await requestJson<Prescription>(`${base}/${encodeURIComponent(id)}`);
    const versions = (await requestJson<{ history: Prescription[] }>(`${base}/${encodeURIComponent(id)}/history`)).history;
    if (!current(token)) return;
    const params = new URLSearchParams(location.hash.split('?')[1] || '');
    if (params.get('view') !== 'eyes') return;
    setSelected(row); setEditing(true); setDraft(fromRecord(row)); setHistory(versions);
    window.history.replaceState(null, '', `#/capabilities/wellbeing?view=eyes&id=${encodeURIComponent(id)}`);
  }
  useEffect(() => {
    mounted.current = true;
    const token = ++generation.current;
    const id = new URLSearchParams(location.hash.split('?')[1] || '').get('id');
    setBusy(true); setError('');
    void (async () => {
      try { await load(token); if (id && current(token)) await select(id, token); }
      catch (reason) { if (current(token)) setError(String(reason)); }
      finally { if (current(token)) setBusy(false); }
    })();
    return () => { mounted.current = false; generation.current += 1; };
  }, []);
  const payload = () => ({ observed_date: draft.observed_date, source: draft.source, notes: draft.notes,
    left: eye(draft.leftSphere, draft.leftCylinder, draft.leftAxis), right: eye(draft.rightSphere, draft.rightCylinder, draft.rightAxis) });
  async function save(event: FormEvent) {
    event.preventDefault();
    await run(async token => {
      if (selected) {
        const { source: _source, ...changes } = payload();
        const row = await requestJson<Prescription>(`${base}/${encodeURIComponent(selected.id)}`, 'PUT', { ...changes, revision: selected.revision, request_id: crypto.randomUUID() });
        await load(token); if (current(token)) await select(row.id, token);
      } else {
        const row = await requestJson<Prescription>(base, 'POST', { ...payload(), request_id: crypto.randomUUID() });
        await load(token); if (current(token)) await select(row.id, token);
      }
    });
  }
  const create = () => { generation.current += 1; setBusy(false); setSelected(null); setEditing(true); setHistory([]); setDraft(blank()); window.history.replaceState(null, '', '#/capabilities/wellbeing?view=eyes'); };
  return <main dir={language === 'ar' ? 'rtl' : 'auto'} style={{ maxWidth: 'var(--content-width)' }} className="mx-auto w-full space-y-2xl px-l py-2xl text-on-surface"><div className="flex flex-wrap items-start justify-between gap-l"><div className="min-w-0"><h2 data-type="title-m" className="text-on-surface">{w[0]}</h2><p data-type="body-m" className="mt-xs text-on-surface-var">{w[1]}</p></div><div className="flex w-full flex-wrap gap-s sm:w-auto"><Button disabled={busy} onClick={create}><Plus size={17} />{w[3]}</Button><Button variant="secondary" disabled={busy} onClick={() => void run(async token => { const value = await requestJson(base + '/export'); if (current(token)) setExported(JSON.stringify(value, null, 2)); })}><Download size={17} />{w[17]}</Button></div></div>
    {error && <p role="alert">{error}</p>}{busy && <p role="status">{w[2]}</p>}
    <section className="rounded-lg border border-outline-variant/20 bg-surface-container p-l"><h2 data-type="title-m" className="text-on-surface">{w[14]}</h2>{!busy && records.length === 0 ? <div className="py-8 text-center"><Eye className="mx-auto mb-3 text-on-surface-low" /><p>{w[15]}</p><Button className="mt-4" onClick={create}>{w[3]}</Button></div> : <ul className="mt-3 divide-y divide-outline-variant/30">{records.map(row => <li key={row.id}><button className="flex w-full items-center justify-between py-3 text-left" onClick={() => void run(token => select(row.id, token))}><span>{row.observed_date} · {row.source}</span><strong>{row.left.sphere} / {row.right.sphere} D</strong></button></li>)}</ul>}</section>
    {editing && <form onSubmit={save} className="grid gap-m rounded-lg border border-outline-variant/20 bg-surface-container p-l sm:grid-cols-2">
      <Field label={w[4]}><TextInput required value={draft.observed_date} onChange={value => setDraft({ ...draft, observed_date: value })} /></Field>
      <Field label={w[5]}><TextInput required disabled={!!selected} value={draft.source} onChange={value => setDraft({ ...draft, source: value })} /></Field>
      <EyeFields side="left" labels={w} draft={draft} setDraft={setDraft} disabled={busy} /><EyeFields side="right" labels={w} draft={draft} setDraft={setDraft} disabled={busy} />
      <Field label={w[11]}><TextArea value={draft.notes} onChange={value => setDraft({ ...draft, notes: value })} /></Field>
      <Button type="submit" disabled={busy}>{selected ? w[12] : w[13]}</Button>
    </form>}
    {selected && <section><h2>{w[16]}</h2><ol>{history.map(row => <li key={row.revision}>v{row.revision}: {w[6]} {row.left.sphere} D / {row.left.cylinder} D × {row.left.axis} {w[19]}; {w[7]} {row.right.sphere} D / {row.right.cylinder} D × {row.right.axis} {w[19]} · {row.notes}</li>)}</ol></section>}
    {exported && <pre aria-label={w[18]}>{exported}</pre>}
  </main>;
}
