import { useEffect, useState, type FormEvent } from 'react';
import { requestJson } from '../../../shared/data/gatewayRequest';
import { useUILanguage } from '../../../shared/i18n';

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
  return <fieldset disabled={disabled}><legend>{sideLabel}</legend>{fields.map(([label, suffix, unit]) => {
    const key = `${prefix}${suffix}` as keyof Draft;
    return <label key={key}>{sideLabel} {label} ({unit})<input required type="number" step="any" min={suffix === 'Axis' ? 0 : suffix === 'Cylinder' ? -20 : -40} max={suffix === 'Axis' ? 180 : suffix === 'Cylinder' ? 20 : 40} value={draft[key]} onChange={event => setDraft({ ...draft, [key]: event.target.value })} /></label>;
  })}</fieldset>;
}

export default function EyePrescriptions() {
  const language = useUILanguage(), w = words[language];
  const [records, setRecords] = useState<Prescription[]>([]), [selected, setSelected] = useState<Prescription | null>(null);
  const [history, setHistory] = useState<Prescription[]>([]), [draft, setDraft] = useState<Draft>(blank());
  const [busy, setBusy] = useState(false), [error, setError] = useState(''), [exported, setExported] = useState('');
  async function run(action: () => Promise<void>) { setBusy(true); setError(''); try { await action(); } catch (reason) { setError(String(reason)); } finally { setBusy(false); } }
  async function load() { setRecords((await requestJson<{ prescriptions: Prescription[] }>(base)).prescriptions); }
  async function select(id: string) {
    const row = await requestJson<Prescription>(`${base}/${encodeURIComponent(id)}`);
    setSelected(row); setDraft(fromRecord(row));
    setHistory((await requestJson<{ history: Prescription[] }>(`${base}/${encodeURIComponent(id)}/history`)).history);
    window.history.replaceState(null, '', `#/capabilities/wellbeing?view=eyes&id=${encodeURIComponent(id)}`);
  }
  useEffect(() => { void run(async () => { await load(); const id = new URLSearchParams(location.hash.split('?')[1]).get('id'); if (id) await select(id); }); }, []);
  const payload = () => ({ observed_date: draft.observed_date, source: draft.source, notes: draft.notes,
    left: eye(draft.leftSphere, draft.leftCylinder, draft.leftAxis), right: eye(draft.rightSphere, draft.rightCylinder, draft.rightAxis) });
  async function save(event: FormEvent) {
    event.preventDefault();
    await run(async () => {
      if (selected) {
        const { source: _source, ...changes } = payload();
        const row = await requestJson<Prescription>(`${base}/${encodeURIComponent(selected.id)}`, 'PUT', { ...changes, revision: selected.revision, request_id: crypto.randomUUID() });
        await load(); await select(row.id);
      } else {
        const row = await requestJson<Prescription>(base, 'POST', { ...payload(), request_id: crypto.randomUUID() });
        await load(); await select(row.id);
      }
    });
  }
  return <main dir={language === 'ar' ? 'rtl' : 'auto'} style={{ maxWidth: 900, marginInline: 'auto', padding: 20 }}><h1>{w[0]}</h1>
    <p>{w[1]}</p>
    {error && <p role="alert">{error}</p>}{busy && <p role="status">{w[2]}</p>}
    <button disabled={busy} onClick={() => { setSelected(null); setHistory([]); setDraft(blank()); window.history.replaceState(null, '', '#/capabilities/wellbeing?view=eyes'); }}>{w[3]}</button>
    <form onSubmit={save} style={{ display: 'grid', gap: 12 }}>
      <label>{w[4]}<input required type="date" value={draft.observed_date} onChange={event => setDraft({ ...draft, observed_date: event.target.value })} /></label>
      <label>{w[5]}<input required disabled={!!selected} value={draft.source} onChange={event => setDraft({ ...draft, source: event.target.value })} /></label>
      <EyeFields side="left" labels={w} draft={draft} setDraft={setDraft} disabled={busy} /><EyeFields side="right" labels={w} draft={draft} setDraft={setDraft} disabled={busy} />
      <label>{w[11]}<textarea value={draft.notes} onChange={event => setDraft({ ...draft, notes: event.target.value })} /></label>
      <button disabled={busy}>{selected ? w[12] : w[13]}</button>
    </form>
    <section><h2>{w[14]}</h2>{!busy && records.length === 0 && <p>{w[15]}</p>}<ul>{records.map(row => <li key={row.id}><button onClick={() => void run(() => select(row.id))}>{row.observed_date} · {row.source} · v{row.revision}</button></li>)}</ul></section>
    {selected && <section><h2>{w[16]}</h2><ol>{history.map(row => <li key={row.revision}>v{row.revision}: {w[6]} {row.left.sphere} D / {row.left.cylinder} D × {row.left.axis} {w[19]}; {w[7]} {row.right.sphere} D / {row.right.cylinder} D × {row.right.axis} {w[19]} · {row.notes}</li>)}</ol></section>}
    <button disabled={busy} onClick={() => void run(async () => setExported(JSON.stringify(await requestJson(base + '/export'), null, 2)))}>{w[17]}</button>
    {exported && <pre aria-label={w[18]}>{exported}</pre>}
  </main>;
}
