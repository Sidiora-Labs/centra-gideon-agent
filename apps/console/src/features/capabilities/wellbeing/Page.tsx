import PrivacyPage from './Privacy';
import SharedPage from './SharedHealth';
import ExportsPage from './Exports';
import LifePage from './LifeCalendar';
import MemoryPage from './MemoryPractice';
import CognitionPage from './Cognition';
import { useHashRoute } from '../../../app/shell/useHashRoute';
import InterventionPage from './Interventions';
import GenomePage from './Genome';
import ConsumptionPage from './Substances';
import ImportPage from './AppleHealth';
import LabsPage from './Labs';
import EpigeneticRecords from './EpigeneticRecords';
import EyePrescriptions from './EyePrescriptions';
import LifestyleProfile from './LifestyleProfile';
import BodyComposition from './BodyComposition';
import { useEffect, useState, type FormEvent } from 'react';
import { requestJson } from '../../../shared/data/gatewayRequest';
import { Button } from '../../../shared/ui/Button';
import { Field, Select, TextArea, TextInput } from '../../../shared/ui/forms';
import { TopBar } from '../../../shared/ui/TopBar';
import { PageTitle } from '../../../shared/ui/PageTitle';
import { Activity, Archive, Brain, Building2, CalendarDays, Dna, Dumbbell, Eye, FileUp, FlaskConical, HeartPulse, ListChecks, Scale, Share2, ShieldCheck, Sparkles, type LucideIcon } from 'lucide-react';
import { AreaNavigation } from '../AreaNavigation';

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
  const w = words[uiLanguage()];
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
  return <main className="mx-auto w-full space-y-l px-l py-2xl text-on-surface" style={{ maxWidth: 'var(--content-width)' }}>
    <div className="flex flex-wrap items-center justify-between gap-m"><h2 data-type="title-m">{w[0]}</h2><div className="flex flex-wrap gap-s"><Button onClick={() => { setSelected(null); setHistory([]); setFirst(''); setSecond(''); setNotes(''); setSource('manual'); setRequestId(crypto.randomUUID()); window.history.replaceState(null, '', '#/capabilities/wellbeing'); }}>{w[7]}</Button><Button variant="secondary" onClick={() => void exportData()}>{w[9]}</Button></div></div>
    {error && <p role="alert">{error}</p>}{busy && <p role="status">{w[10]}</p>}
    <form onSubmit={save} className="grid gap-m rounded-lg bg-surface-container p-l sm:grid-cols-2">
      <Field label={w[0]}><Select value={kind} disabled={!!selected} onChange={value => { const next = value as Measurement['kind']; setKind(next); setUnit(next === 'body_weight' ? 'kg' : 'mmHg'); }} options={[{ value: 'body_weight', label: w[1] }, { value: 'blood_pressure', label: w[2] }]} /></Field>
      <Field label={w[3]}><TextInput required value={observed} onChange={setObserved} /></Field>
      <Field label={kind === 'body_weight' ? w[1] : w[17]}><input className="h-10 w-full rounded-md border border-outline-variant/30 bg-surface-container px-m text-on-surface outline-none focus:border-primary/40 focus:ring-2 focus:ring-inset focus:ring-primary" required type="number" step="any" min="0.001" value={first} onChange={e => setFirst(e.target.value)} /></Field>
      {kind === 'blood_pressure' && <Field label={w[18]}><input className="h-10 w-full rounded-md border border-outline-variant/30 bg-surface-container px-m text-on-surface outline-none focus:border-primary/40 focus:ring-2 focus:ring-inset focus:ring-primary" required type="number" step="any" min="0.001" value={second} onChange={e => setSecond(e.target.value)} /></Field>}
      <Field label={w[16]}><Select value={unit} onChange={setUnit} options={(kind === 'body_weight' ? ['kg', 'lb'] : ['mmHg']).map(value => ({ value, label: value }))} /></Field>
      <Field label={w[4]}><TextInput required disabled={!!selected} value={source} onChange={setSource} /></Field>
      <Field label={w[5]}><TextArea value={notes} onChange={setNotes} /></Field><Button type="submit" disabled={busy}>{w[6]}</Button>
    </form>
    <form onSubmit={e => { e.preventDefault(); void load(); }} className="flex flex-wrap items-end gap-m"><Field label={w[12]}><TextInput value={from} onChange={setFrom} /></Field><Field label={w[13]}><TextInput value={until} onChange={setUntil} /></Field><Button type="submit" variant="secondary" disabled={busy}>{w[14]}</Button></form>
    {!busy && !rows.length && <p>{w[11]}</p>}
    <ul className="divide-y divide-outline-variant/20 rounded-lg bg-surface-container px-l">{rows.map(row => <li key={row.id}><button className="w-full py-m text-left" onClick={() => void select(row.id)}>{row.observed_at} · {row.kind === 'body_weight' ? w[1] : w[2]} · {Object.values(row.values).join('/')} {row.unit}</button></li>)}</ul>
    {!!history.length && <section><h2>{w[8]}</h2><ol>{history.map(row => <li key={row.revision}>{w[15]} {row.revision} · {row.observed_at} · {Object.values(row.values).join('/')} {row.unit} · {row.notes}</li>)}</ol></section>}
  </main>;
}

function uiLanguage(): 'en' | 'es' | 'ar' | 'hi' | 'zh-CN' {
  const value = (typeof document !== 'undefined' && document.documentElement.lang) || (typeof navigator !== 'undefined' && navigator.language) || 'en'
  if (value.toLowerCase().startsWith('es')) return 'es'
  if (value.toLowerCase().startsWith('ar')) return 'ar'
  if (value.toLowerCase().startsWith('hi')) return 'hi'
  if (value.toLowerCase().startsWith('zh')) return 'zh-CN'
  return 'en'
}

export default function Page() {
  const language = uiLanguage();
  const labels = { en: ['Measurements', 'Laboratory', 'Import', 'Consumption', 'Genome', 'Interventions', 'Exercises', 'Memory', 'Life calendar', 'Exports', 'Shared health', 'Privacy', 'Organizations', 'Epigenetic results', 'Eye prescriptions', 'Lifestyle profile', 'Body composition'], es: ['Mediciones', 'Laboratorio', 'Importar', 'Consumo', 'Genoma', 'Intervenciones', 'Ejercicios', 'Memoria', 'Calendario vital', 'Exportaciones', 'Salud compartida', 'Privacidad', 'Organizaciones', 'Resultados epigenéticos', 'Recetas oculares', 'Perfil de estilo de vida', 'Composición corporal'], ar: ['القياسات', 'المختبر', 'استيراد', 'الاستهلاك', 'الجينوم', 'التدخلات', 'التمارين', 'الذاكرة', 'تقويم الحياة', 'الصادرات', 'صحة مشتركة', 'الخصوصية', 'المؤسسات', 'النتائج اللاجينية', 'وصفات العيون', 'ملف نمط الحياة', 'تكوين الجسم'], hi: ['माप', 'प्रयोगशाला', 'आयात', 'सेवन', 'जीनोम', 'हस्तक्षेप', 'अभ्यास', 'स्मृति', 'जीवन कैलेंडर', 'निर्यात', 'साझा स्वास्थ्य', 'गोपनीयता', 'संगठन', 'एपिजेनेटिक परिणाम', 'नेत्र पर्चे', 'जीवनशैली प्रोफ़ाइल', 'शारीरिक संरचना'], 'zh-CN': ['测量', '化验', '导入', '摄入', '基因组', '干预', '练习', '记忆', '生活日历', '导出', '共享健康', '隐私', '机构', '表观遗传结果', '眼镜处方', '生活方式档案', '身体成分'] }[language];
  const views = ['measurements', 'labs', 'import', 'consumption', 'genome', 'interventions', 'cognition', 'memory', 'life', 'exports', 'shared', 'privacy', 'organizations', 'epigenetic', 'eyes', 'lifestyle', 'body-composition'];
  const icons: LucideIcon[] = [Activity, FlaskConical, FileUp, ListChecks, Dna, HeartPulse, Brain, Brain, CalendarDays, Archive, Share2, ShieldCheck, Building2, Sparkles, Eye, Dumbbell, Scale];
  const navigationLabel = { en: 'Wellbeing sections', es: 'Secciones de bienestar', ar: 'أقسام العافية', hi: 'स्वास्थ्य अनुभाग', 'zh-CN': '健康栏目' }[language];
  const destinations = views.map((id, index) => ({ id, label: labels[index], icon: icons[index] }));
  const { query, setQuery } = useHashRoute('capabilities');
  const view = query.view || 'measurements';
  const detailKey = [view, query.id, query.source, query.plan, query.record, query.card, query.event, query.subject, query.fact].join(':');
  const title = labels[views.indexOf(view)] ?? labels[0];
  const content = view === 'body-composition' ? <BodyComposition /> : view === 'lifestyle' ? <LifestyleProfile /> : view === 'eyes' ? <EyePrescriptions /> : view === 'epigenetic' ? <EpigeneticRecords /> : view === 'organizations' || view === 'privacy' ? <PrivacyPage /> : view === 'shared' ? <SharedPage /> : view === 'exports' ? <ExportsPage /> : view === 'life' ? <LifePage /> : view === 'memory' ? <MemoryPage /> : view === 'cognition' ? <CognitionPage /> : view === 'interventions' ? <InterventionPage /> : view === 'genome' ? <GenomePage /> : view === 'consumption' ? <ConsumptionPage /> : view === 'labs' ? <LabsPage /> : view === 'import' ? <ImportPage /> : <MeasurementsPage />;
  const changeView = (next: string) => setQuery({ view: next === 'measurements' ? null : next, id: null, source: null, plan: null, record: null, card: null, event: null, subject: null, fact: null }, { replace: true });
  return <AreaNavigation label={navigationLabel} items={destinations} active={view} onChange={changeView}>
    <div className="flex h-full min-h-0 flex-col"><TopBar left={<PageTitle>{title}</PageTitle>} /><div className="min-h-0 flex-1 overflow-y-auto"><div key={detailKey}>{content}</div></div></div>
  </AreaNavigation>;
}
