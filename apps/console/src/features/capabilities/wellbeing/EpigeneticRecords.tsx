import { useEffect, useState, type FormEvent } from 'react';
import { requestJson } from '../../../shared/data/gatewayRequest';
import { useUILanguage } from '../../../shared/i18n';

type Reported = { value: number; unit?: string; scale?: string } | null;
type RecordRow = { id: string; revision: number; source_report_id: string; observed_at: string; source: string; notes: string; evidence_basis: 'source_reported'; biological_age?: Reported; chronological_age?: Reported; pace_of_aging?: Reported; organ_scores: Record<string, Reported> };
const base = '/api/capabilities/wellbeing/epigenetic';
const words = {
  en: ['Epigenetic results', 'Source-reported values only. Gideon does not diagnose or derive clinical meaning.', 'New report', 'Source report ID', 'Observed date', 'Source', 'Reported ages', 'Biological age', 'Biological age unit', 'Chronological age', 'Chronological age unit', 'Pace of aging', 'Pace value', 'Pace scale', 'Organ scores JSON', 'Each named score uses a value and exactly one source-authored unit or scale. Use null for a reported missing score.', 'Notes', 'Save correction', 'Record report', 'No epigenetic results', 'Report history', 'Evidence: source reported', 'Loading…', 'missing'],
  es: ['Resultados epigenéticos', 'Solo valores informados por la fuente. Gideon no diagnostica ni deriva significado clínico.', 'Nuevo informe', 'ID del informe fuente', 'Fecha observada', 'Fuente', 'Edades informadas', 'Edad biológica', 'Unidad de edad biológica', 'Edad cronológica', 'Unidad de edad cronológica', 'Ritmo de envejecimiento', 'Valor del ritmo', 'Escala del ritmo', 'JSON de puntuaciones de órganos', 'Cada puntuación usa un valor y exactamente una unidad o escala de la fuente. Use null si falta.', 'Notas', 'Guardar corrección', 'Registrar informe', 'Sin resultados epigenéticos', 'Historial del informe', 'Evidencia: informada por la fuente', 'Cargando…', 'ausente'],
  ar: ['النتائج اللاجينية', 'قيم مبلّغ عنها من المصدر فقط. لا يشخّص Gideon ولا يستنتج معنى سريريًا.', 'تقرير جديد', 'معرّف تقرير المصدر', 'تاريخ الرصد', 'المصدر', 'الأعمار المبلّغ عنها', 'العمر البيولوجي', 'وحدة العمر البيولوجي', 'العمر الزمني', 'وحدة العمر الزمني', 'وتيرة الشيخوخة', 'قيمة الوتيرة', 'مقياس الوتيرة', 'JSON لدرجات الأعضاء', 'تستخدم كل درجة قيمة ووحدة أو مقياسًا واحدًا من المصدر. استخدم null للقيمة المفقودة.', 'ملاحظات', 'حفظ التصحيح', 'تسجيل التقرير', 'لا توجد نتائج لاجينية', 'سجل التقرير', 'الدليل: مبلّغ من المصدر', 'جار التحميل…', 'مفقود'],
  hi: ['एपिजेनेटिक परिणाम', 'केवल स्रोत द्वारा रिपोर्ट किए गए मान। Gideon निदान या नैदानिक अर्थ नहीं निकालता।', 'नई रिपोर्ट', 'स्रोत रिपोर्ट आईडी', 'अवलोकन तिथि', 'स्रोत', 'रिपोर्ट की गई आयु', 'जैविक आयु', 'जैविक आयु इकाई', 'कालानुक्रमिक आयु', 'कालानुक्रमिक आयु इकाई', 'उम्र बढ़ने की गति', 'गति मान', 'गति पैमाना', 'अंग स्कोर JSON', 'हर स्कोर में मान और स्रोत की ठीक एक इकाई या पैमाना हो। गुम मान के लिए null उपयोग करें।', 'टिप्पणियाँ', 'सुधार सहेजें', 'रिपोर्ट दर्ज करें', 'कोई एपिजेनेटिक परिणाम नहीं', 'रिपोर्ट इतिहास', 'साक्ष्य: स्रोत द्वारा रिपोर्ट', 'लोड हो रहा है…', 'गुम'],
  'zh-CN': ['表观遗传结果', '仅记录来源报告值。Gideon 不作诊断或推导临床含义。', '新建报告', '来源报告 ID', '观察日期', '来源', '报告年龄', '生物年龄', '生物年龄单位', '实足年龄', '实足年龄单位', '衰老速度', '速度值', '速度量表', '器官评分 JSON', '每项评分须包含数值以及来源提供的一个单位或量表。缺失值使用 null。', '备注', '保存更正', '记录报告', '暂无表观遗传结果', '报告历史', '证据：来源报告', '加载中…', '缺失'],
};

function measure(value: string, label: string, authored: string) {
  if (!value.trim()) return undefined;
  return { value: Number(value), [label]: authored };
}

export default function EpigeneticRecords() {
  const language = useUILanguage(), w = words[language];
  const [rows, setRows] = useState<RecordRow[]>([]), [selected, setSelected] = useState<RecordRow | null>(null), [history, setHistory] = useState<RecordRow[]>([]);
  const [reportId, setReportId] = useState(''), [observed, setObserved] = useState(''), [source, setSource] = useState(''), [notes, setNotes] = useState('');
  const [bio, setBio] = useState(''), [bioUnit, setBioUnit] = useState('years'), [chronological, setChronological] = useState(''), [chronoUnit, setChronoUnit] = useState('years');
  const [pace, setPace] = useState(''), [paceScale, setPaceScale] = useState('years/year'), [scores, setScores] = useState('{}');
  const [busy, setBusy] = useState(false), [error, setError] = useState('');

  async function run(action: () => Promise<void>) { setBusy(true); setError(''); try { await action(); } catch (reason) { setError(String(reason)); } finally { setBusy(false); } }
  async function load() { const records = (await requestJson<{ records: RecordRow[] }>(base)).records; setRows(records); return records; }
  function populate(row: RecordRow) {
    setSelected(row); setReportId(row.source_report_id); setObserved(row.observed_at); setSource(row.source); setNotes(row.notes);
    setBio(row.biological_age == null ? '' : String(row.biological_age.value)); setBioUnit(row.biological_age?.unit || row.biological_age?.scale || 'years');
    setChronological(row.chronological_age == null ? '' : String(row.chronological_age.value)); setChronoUnit(row.chronological_age?.unit || row.chronological_age?.scale || 'years');
    setPace(row.pace_of_aging == null ? '' : String(row.pace_of_aging.value)); setPaceScale(row.pace_of_aging?.scale || row.pace_of_aging?.unit || 'years/year');
    setScores(JSON.stringify(row.organ_scores, null, 2));
  }
  async function select(row: RecordRow) { populate(row); setHistory((await requestJson<{ history: RecordRow[] }>(`${base}/${row.id}/history`)).history); window.history.replaceState(null, '', `#/capabilities/wellbeing?view=epigenetic&id=${encodeURIComponent(row.id)}`); }
  useEffect(() => { void run(async () => { await load(); const id = new URLSearchParams(location.hash.split('?')[1]).get('id'); if (id) await select(await requestJson<RecordRow>(`${base}/${encodeURIComponent(id)}`)); }); }, []);
  function body() {
    let organScores: Record<string, Reported>;
    try { organScores = JSON.parse(scores) as Record<string, Reported>; } catch { throw new Error('Organ scores must be valid JSON'); }
    return {
      source_report_id: reportId, observed_at: observed, source, notes, organ_scores: organScores,
      biological_age: measure(bio, 'unit', bioUnit), chronological_age: measure(chronological, 'unit', chronoUnit), pace_of_aging: measure(pace, 'scale', paceScale),
    };
  }
  async function save(event: FormEvent) {
    event.preventDefault(); await run(async () => {
      const payload = body();
      const row = selected
        ? await requestJson<RecordRow>(`${base}/${selected.id}`, 'PUT', { ...payload, source: undefined, request_id: crypto.randomUUID(), revision: selected.revision })
        : await requestJson<RecordRow>(base, 'POST', { ...payload, request_id: crypto.randomUUID() });
      await load(); await select(row);
    });
  }
  function clear() { setSelected(null); setHistory([]); setReportId(''); setObserved(''); setSource(''); setNotes(''); setBio(''); setChronological(''); setPace(''); setScores('{}'); window.history.replaceState(null, '', '#/capabilities/wellbeing?view=epigenetic'); }
  const shown = (value?: Reported) => value == null ? w[23] : `${value.value} ${value.unit || value.scale}`;
  return <main dir={language === 'ar' ? 'rtl' : undefined} style={{ maxWidth: 900, marginInline: 'auto', padding: 20 }}>
    <h1>{w[0]}</h1><p>{w[1]}</p>
    {error && <p role="alert">{error}</p>}{busy && <p role="status">{w[22]}</p>}
    <button type="button" onClick={clear}>{w[2]}</button>
    <form onSubmit={save} style={{ display: 'grid', gap: 10 }}>
      <label>{w[3]}<input required value={reportId} onChange={e => setReportId(e.target.value)} /></label>
      <label>{w[4]}<input required type="date" value={observed} onChange={e => setObserved(e.target.value)} /></label>
      <label>{w[5]}<input required disabled={selected !== null} value={source} onChange={e => setSource(e.target.value)} /></label>
      <fieldset><legend>{w[6]}</legend>
        <label>{w[7]}<input type="number" step="any" value={bio} onChange={e => setBio(e.target.value)} /></label><label>{w[8]}<input value={bioUnit} onChange={e => setBioUnit(e.target.value)} /></label>
        <label>{w[9]}<input type="number" step="any" value={chronological} onChange={e => setChronological(e.target.value)} /></label><label>{w[10]}<input value={chronoUnit} onChange={e => setChronoUnit(e.target.value)} /></label>
      </fieldset>
      <fieldset><legend>{w[11]}</legend><label>{w[12]}<input type="number" step="any" value={pace} onChange={e => setPace(e.target.value)} /></label><label>{w[13]}<input value={paceScale} onChange={e => setPaceScale(e.target.value)} /></label></fieldset>
      <label>{w[14]}<textarea rows={5} value={scores} onChange={e => setScores(e.target.value)} aria-describedby="organ-help" /></label><small id="organ-help">{w[15]}</small>
      <label>{w[16]}<textarea value={notes} onChange={e => setNotes(e.target.value)} /></label>
      <button disabled={busy}>{selected ? w[17] : w[18]}</button>
    </form>
    {!busy && rows.length === 0 && <p>{w[19]}</p>}
    <ul>{rows.map(row => <li key={row.id}><button onClick={() => void run(() => select(row))}>{row.observed_at} · {row.source_report_id}</button></li>)}</ul>
    {selected && <section><h2>{w[20]}</h2><p>{w[21]}</p><ol>{history.map(row => <li key={row.revision}>v{row.revision}: biological {shown(row.biological_age)}; chronological {shown(row.chronological_age)}; pace {shown(row.pace_of_aging)}</li>)}</ol></section>}
  </main>;
}
