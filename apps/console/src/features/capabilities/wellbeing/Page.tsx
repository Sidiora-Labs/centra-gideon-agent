import PrivacyPage from './PrivacyPage';
import HoldingsPage from './HoldingsPage';
import SharedPage from './SharedPage';
import ExportsPage from './ExportsPage';
import LifePage from './LifePage';
import MemoryPage from './MemoryPage';
import CognitionPage from './CognitionPage';
import { useHashRoute } from '../../../app/shell/useHashRoute';
import InterventionPage from './InterventionPage';
import GenomePage from './GenomePage';
import ConsumptionPage from './ConsumptionPage';
import ImportPage from './ImportPage';
import LabsPage from './LabsPage';
import EpigeneticRecords from './EpigeneticRecords';
import EyePrescriptions from './EyePrescriptions';
import LifestyleProfile from './LifestyleProfile';
import BodyComposition from './BodyComposition';
import { useEffect, useState, type FormEvent } from 'react';
import { requestJson } from '../../../shared/data/gatewayRequest';
import { useUILanguage } from '../../../shared/i18n';

type Measurement = { id: string; kind: 'body_weight' | 'blood_pressure'; observed_at: string; unit: string; values: Record<string, number>; source: string; notes: string; revision: number };
const base = '/api/capabilities/wellbeing';
const words = {
  en: ['Measurements', 'Body weight', 'Blood pressure', 'Observed at (with timezone)', 'Source', 'Notes', 'Save', 'New measurement', 'History', 'Export JSON', 'Loading…', 'No measurements', 'From', 'Until', 'Filter', 'Revision', 'Unit', 'Systolic', 'Diastolic'],
  es: ['Mediciones', 'Peso corporal', 'Presión arterial', 'Fecha (con zona horaria)', 'Fuente', 'Notas', 'Guardar', 'Nueva medición', 'Historial', 'Exportar JSON', 'Cargando…', 'Sin mediciones', 'Desde', 'Hasta', 'Filtrar', 'Revisión', 'Unidad', 'Sistólica', 'Diastólica'],
  ar: ['القياسات', 'وزن الجسم', 'ضغط الدم', 'التاريخ (مع المنطقة الزمنية)', 'المصدر', 'ملاحظات', 'حفظ', 'قياس جديد', 'السجل', 'تصدير JSON', 'جار التحميل…', 'لا توجد قياسات', 'من', 'حتى', 'تصفية', 'المراجعة', 'الوحدة', 'الانقباضي', 'الانبساطي'],
  hi: ['माप', 'शरीर का वजन', 'रक्तचाप', 'समय (समय क्षेत्र सहित)', 'स्रोत', 'टिप्पणियाँ', 'सहेजें', 'नया माप', 'इतिहास', 'JSON निर्यात', 'लोड हो रहा है…', 'कोई माप नहीं', 'से', 'तक', 'फ़िल्टर', 'संशोधन', 'इकाई', 'सिस्टोलिक', 'डायस्टोलिक'],
  'zh-CN': ['测量', '体重', '血压', '观测时间（含时区）', '来源', '备注', '保存', '新测量', '历史', '导出 JSON', '加载中…', '暂无测量', '从', '至', '筛选', '修订', '单位', '收缩压', '舒张压'],
};
function MeasurementsPage() {
  const w = words[useUILanguage()];
  const [rows, setRows] = useState<Measurement[]>([]), [history, setHistory] = useState<Measurement[]>([]);
  const [selected, setSelected] = useState<Measurement | null>(null), [kind, setKind] = useState<Measurement['kind']>('body_weight');
  const [observed, setObserved] = useState(new Date().toISOString()), [source, setSource] = useState('manual'), [notes, setNotes] = useState('');
  const [first, setFirst] = useState(''), [second, setSecond] = useState(''), [unit, setUnit] = useState('kg');
  const [from, setFrom] = useState(''), [until, setUntil] = useState(''), [error, setError] = useState(''), [busy, setBusy] = useState(false);
  const [requestId, setRequestId] = useState(() => crypto.randomUUID());
  async function select(id: string) {
    try {
      const row = await requestJson<Measurement>(`${base}/measurements/${encodeURIComponent(id)}`);
      const versions = await requestJson<{ history: Measurement[] }>(`${base}/measurements/${encodeURIComponent(id)}/history`);
      setSelected(row); setKind(row.kind); setObserved(row.observed_at); setUnit(row.unit); setSource(row.source); setNotes(row.notes);
      setFirst(String(row.values.weight ?? row.values.systolic)); setSecond(String(row.values.diastolic ?? '')); setHistory(versions.history);
      window.history.replaceState(null, '', `#/capabilities/wellbeing?id=${encodeURIComponent(id)}`);
    } catch (e) { setError(String(e)); }
  }
  async function load() {
    setBusy(true); setError('');
    try { const query = new URLSearchParams(); if (from) query.set('from', from); if (until) query.set('to', until);
      setRows((await requestJson<{ measurements: Measurement[] }>(`${base}/measurements?${query}`)).measurements);
    } catch (e) { setError(String(e)); } finally { setBusy(false); }
  }
  useEffect(() => { void load(); const id = new URLSearchParams(location.hash.split('?')[1]).get('id'); if (id) void select(id); }, []);
  async function save(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError('');
    try {
      const fields = { observed_at: observed, values: kind === 'body_weight' ? { weight: Number(first) } : { systolic: Number(first), diastolic: Number(second) }, unit, notes };
      const row = await requestJson<Measurement>(`${base}/measurements${selected ? '/' + encodeURIComponent(selected.id) : ''}`, selected ? 'PUT' : 'POST', selected ? { ...fields, revision: selected.revision, request_id: requestId } : { ...fields, kind, source, request_id: requestId });
      setRequestId(crypto.randomUUID()); await load(); await select(row.id);
    } catch (e) { setError(String(e)); } finally { setBusy(false); }
  }
  async function exportData() {
    try { const data = await requestJson<unknown>(`${base}/export`); const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' })); const link = document.createElement('a'); link.href = url; link.download = 'measurements.json'; link.click(); URL.revokeObjectURL(url); }
    catch (e) { setError(String(e)); }
  }
  return <main style={{ padding: 20, maxWidth: 900, marginInline: 'auto' }}>
    <h1>{w[0]}</h1><button onClick={() => { setSelected(null); setHistory([]); setFirst(''); setSecond(''); setNotes(''); setSource('manual'); setRequestId(crypto.randomUUID()); window.history.replaceState(null, '', '#/capabilities/wellbeing'); }}>{w[7]}</button>{' '}<button onClick={() => void exportData()}>{w[9]}</button>
    {error && <p role="alert">{error}</p>}{busy && <p role="status">{w[10]}</p>}
    <form onSubmit={save} style={{ display: 'grid', gap: 12, marginBlock: 16 }}>
      <label>{w[0]}<select value={kind} disabled={!!selected} onChange={e => { const next = e.target.value as Measurement['kind']; setKind(next); setUnit(next === 'body_weight' ? 'kg' : 'mmHg'); }}><option value="body_weight">{w[1]}</option><option value="blood_pressure">{w[2]}</option></select></label>
      <label>{w[3]}<input required value={observed} onChange={e => setObserved(e.target.value)} /></label>
      <label>{kind === 'body_weight' ? w[1] : w[17]}<input required type="number" step="any" min="0.001" value={first} onChange={e => setFirst(e.target.value)} /></label>
      {kind === 'blood_pressure' && <label>{w[18]}<input required type="number" step="any" min="0.001" value={second} onChange={e => setSecond(e.target.value)} /></label>}
      <label>{w[16]}<select value={unit} onChange={e => setUnit(e.target.value)}>{(kind === 'body_weight' ? ['kg', 'lb'] : ['mmHg']).map(v => <option key={v}>{v}</option>)}</select></label>
      <label>{w[4]}<input required disabled={!!selected} value={source} onChange={e => setSource(e.target.value)} /></label>
      <label>{w[5]}<textarea maxLength={4000} value={notes} onChange={e => setNotes(e.target.value)} /></label><button disabled={busy}>{w[6]}</button>
    </form>
    <form onSubmit={e => { e.preventDefault(); void load(); }}><label>{w[12]}<input value={from} onChange={e => setFrom(e.target.value)} /></label><label>{w[13]}<input value={until} onChange={e => setUntil(e.target.value)} /></label><button disabled={busy}>{w[14]}</button></form>
    {!busy && !rows.length && <p>{w[11]}</p>}
    <ul>{rows.map(row => <li key={row.id}><button onClick={() => void select(row.id)}>{row.observed_at} · {row.kind === 'body_weight' ? w[1] : w[2]} · {Object.values(row.values).join('/')} {row.unit}</button></li>)}</ul>
    {!!history.length && <section><h2>{w[8]}</h2><ol>{history.map(row => <li key={row.revision}>{w[15]} {row.revision} · {row.observed_at} · {Object.values(row.values).join('/')} {row.unit} · {row.notes}</li>)}</ol></section>}
  </main>;
}

export default function Page() {
  const language = useUILanguage();
  const labels = { en: ['Measurements', 'Laboratory', 'Import', 'Consumption', 'Genome', 'Interventions', 'Exercises', 'Memory', 'Life calendar', 'Exports', 'Shared health', 'Privacy', 'Organizations', 'Epigenetic results', 'Eye prescriptions', 'Lifestyle profile', 'Body composition'], es: ['Mediciones', 'Laboratorio', 'Importar', 'Consumo', 'Genoma', 'Intervenciones', 'Ejercicios', 'Memoria', 'Calendario vital', 'Exportaciones', 'Salud compartida', 'Privacidad', 'Organizaciones', 'Resultados epigenéticos', 'Recetas oculares', 'Perfil de estilo de vida', 'Composición corporal'], ar: ['القياسات', 'المختبر', 'استيراد', 'الاستهلاك', 'الجينوم', 'التدخلات', 'التمارين', 'الذاكرة', 'تقويم الحياة', 'الصادرات', 'صحة مشتركة', 'الخصوصية', 'المؤسسات', 'النتائج اللاجينية', 'وصفات العيون', 'ملف نمط الحياة', 'تكوين الجسم'], hi: ['माप', 'प्रयोगशाला', 'आयात', 'सेवन', 'जीनोम', 'हस्तक्षेप', 'अभ्यास', 'स्मृति', 'जीवन कैलेंडर', 'निर्यात', 'साझा स्वास्थ्य', 'गोपनीयता', 'संगठन', 'एपिजेनेटिक परिणाम', 'नेत्र पर्चे', 'जीवनशैली प्रोफ़ाइल', 'शारीरिक संरचना'], 'zh-CN': ['测量', '化验', '导入', '摄入', '基因组', '干预', '练习', '记忆', '生活日历', '导出', '共享健康', '隐私', '机构', '表观遗传结果', '眼镜处方', '生活方式档案', '身体成分'] }[language];
  const navigationLabel = { en: 'Wellbeing sections', es: 'Secciones de bienestar', ar: 'أقسام العافية', hi: 'स्वास्थ्य अनुभाग', 'zh-CN': '健康栏目' }[language];
  const views = ['measurements', 'labs', 'import', 'consumption', 'genome', 'interventions', 'cognition', 'memory', 'life', 'exports', 'shared', 'privacy', 'organizations', 'epigenetic', 'eyes', 'lifestyle', 'body-composition'];
  const { query, setQuery } = useHashRoute('capabilities');
  const view = query.view || 'measurements';
  function change(next: string) { setQuery({ view: next === 'measurements' ? null : next, id: null, source: null, plan: null, record: null, card: null, event: null, subject: null, fact: null }, { replace: true }); }
  const detailKey = [view, query.id, query.source, query.plan, query.record, query.card, query.event, query.subject, query.fact].join(':');
  return <><nav aria-label={navigationLabel} style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>{views.map((next, index) => <button key={next} aria-pressed={view === next} onClick={() => change(next)}>{labels[index]}</button>)}</nav><div key={detailKey}>{view === 'body-composition' ? <BodyComposition /> : view === 'lifestyle' ? <LifestyleProfile /> : view === 'eyes' ? <EyePrescriptions /> : view === 'epigenetic' ? <EpigeneticRecords /> : view === 'organizations' ? <HoldingsPage /> : view === 'privacy' ? <PrivacyPage /> : view === 'shared' ? <SharedPage /> : view === 'exports' ? <ExportsPage /> : view === 'life' ? <LifePage /> : view === 'memory' ? <MemoryPage /> : view === 'cognition' ? <CognitionPage /> : view === 'interventions' ? <InterventionPage /> : view === 'genome' ? <GenomePage /> : view === 'consumption' ? <ConsumptionPage /> : view === 'labs' ? <LabsPage /> : view === 'import' ? <ImportPage /> : <MeasurementsPage />}</div></>;
}
