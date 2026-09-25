"""Advisory deterministic prose checks with exact Unicode source anchors."""
import json
import re
from pathlib import Path
from .voice import WORD, fingerprint

CATALOG = json.loads(Path(__file__).with_name('editorial_catalog.json').read_text())
LEX = CATALOG['lexicons']
PHRASES = {'prose.cliches': 'CLICHE_PHRASES', 'prose.filter-words': 'FILTER_WORDS', 'prose.hedge-words': 'HEDGE_WORDS',
           'prose.crutch-words': 'CRUTCH_WORDS', 'prose.slop-banned-words': 'TIER1_BANNED_WORDS'}
SUPPORTED = set(PHRASES) | {'prose.modifier-stacking', 'prose.adverbs', 'prose.passive-voice', 'prose.repeated-gestures',
    'prose.word-echoes', 'prose.sentence-rhythm', 'prose.ai-tells', 'prose.structural-tics', 'prose.burstiness',
    'prose.italic-thoughts', 'dialogue.said-bookisms', 'dialogue.attribution-clarity', 'dialogue.tag-variety'}


def forms(word):
    return {word, word + 's', word + 'ed', word + 'ing', word[:-1] + 'ed' if word.endswith('e') else word + word[-1] + 'ed',
            word[:-1] + 'ing' if word.endswith('e') else word + word[-1] + 'ing'}


def scan(check_id, source):
    findings = []
    seen = set()
    def add(start, end, problem, suggestion='Review this passage in context before changing it.', **details):
        if start < 0 or end <= start or end > len(source) or (start, end) in seen:
            return
        seen.add((start, end))
        findings.append({'start': start, 'end': end, 'quote': source[start:end], 'problem': problem, 'suggestion': suggestion, 'severity': 'low', **details})
    tokens = list(WORD.finditer(source))
    sentences = [m for m in re.finditer(r'[^.!?。！？]+[.!?。！？]*', source) if WORD.search(m[0])]
    if check_id in PHRASES:
        occupied = []
        for phrase in sorted(LEX[PHRASES[check_id]], key=len, reverse=True):
            pattern = r'(?<!\w)' + r'\s+'.join(re.escape(w) for w in phrase.split()) + r'(?!\w)'
            for match in re.finditer(pattern, source, re.I):
                if not any(a < match.end() and match.start() < b for a, b in occupied):
                    add(match.start(), match.end(), 'Review phrase: ' + phrase)
                    occupied.append(match.span())
                    if check_id == 'prose.cliches':
                        break
    elif check_id == 'prose.adverbs':
        for index, token in enumerate(tokens):
            word = token[0].casefold()
            if word.endswith('ly') and word not in LEX['NON_ADVERB_LY']:
                tag = index > 0 and tokens[index - 1][0].casefold() in LEX['DIALOGUE_TAGS']
                kind = ('reporting' if word in LEX['REPORTING_TAG_ADVERBS'] else 'emotion') if tag else None
                add(*token.span(), 'Review adverb' + (' after dialogue tag' if tag else ''), dialogue_tag=tag, tag_kind=kind)
    elif check_id == 'prose.modifier-stacking':
        run = []
        for token in tokens + [None]:
            adjective = token is not None and (token[0].casefold() in LEX['COMMON_ADJECTIVES'] or re.search(r'(ous|ful|ive|ent|ant|ical|ic|less|able|ible|ish|ese|like|some|ward|most|ed|ing|ly)$', token[0], re.I))
            if run and (not adjective or source[run[-1].end():token.start()].strip() if token is not None else True):
                if len(run) >= 3:
                    add(run[0].start(), run[-1].end(), 'Stacked modifiers', modifier_count=len(run))
                run = []
            if adjective:
                run.append(token)
    elif check_id == 'prose.passive-voice':
        for i, token in enumerate(tokens[:-1]):
            if token[0].casefold() not in ('is', 'are', 'was', 'were', 'be', 'been', 'being', 'am'):
                continue
            for following in tokens[i + 1:i + 5]:
                word = following[0].casefold()
                if re.search(r'[.!?;\n]', source[token.end():following.start()]):
                    break
                if word.endswith('ly') or word in ('not', 'never', 'being', 'been'):
                    continue
                if word.endswith('ed') or word in LEX['IRREGULAR_PARTICIPLES']:
                    agent = bool(re.match(r'\s+by\s+(?!(?:the\s+)?(?:window|door|river|sea)\b)', source[following.end():], re.I))
                    setting = i > 0 and tokens[i - 1][0].casefold() in LEX['SETTING_SUBJECTS']
                    if agent or (not setting and word not in LEX['STATIVE_PARTICIPLES']):
                        add(token.start(), following.end(), 'Possible agentive passive voice')
                break
    elif check_id == 'prose.word-echoes':
        previous, emitted = {}, set()
        for i, token in enumerate(tokens):
            word = token[0].casefold()
            if len(word) >= 4 and word not in LEX['STOPWORDS']:
                if word in previous and i - previous[word] <= 50 and word not in emitted:
                    add(*tokens[previous[word]].span(), 'Nearby repeated word: ' + word)
                    emitted.add(word)
                previous[word] = i
    elif check_id == 'prose.repeated-gestures':
        for base in LEX['GESTURE_WORDS']:
            hits = [t for t in tokens if t[0].casefold() in forms(base)]
            if len(hits) >= 3:
                add(*hits[0].span(), 'Repeated gesture: ' + base, occurrences=len(hits))
    elif check_id == 'prose.italic-thoughts':
        emitted = set()
        for match in re.finditer(r'(?<!\*)\*(?!\*)([^*\n]+?)\*(?!\*)|(?<![\w_])_(?!_)([^_\n]+?)_(?![\w_])', source):
            inner = match[1] or match[2]
            if len(WORD.findall(inner)) >= 4 and inner.casefold() not in emitted:
                add(*match.span(), 'Extended italicized thought')
                emitted.add(inner.casefold())
    elif check_id in ('prose.sentence-rhythm', 'prose.burstiness', 'style.reading-level'):
        fp = fingerprint(source)
        if check_id == 'prose.sentence-rhythm' and fp['sentences'] >= 8 and fp['metrics']['sentence_cv'] <= 0.35:
            add(0, min(len(source), 4000), 'Low sentence-length variation', coefficient=fp['metrics']['sentence_cv'])
        if check_id == 'prose.burstiness':
            if source.count('—') >= 2 and fp['metrics']['emdash_rate'] >= 15:
                first = source.index('—'); add(first, first + 1, 'High em-dash density', rate=fp['metrics']['emdash_rate'])
            if fp['paragraphs'] >= 4 and fp['metrics']['paragraph_mean'] and fp['metrics']['paragraph_std'] / fp['metrics']['paragraph_mean'] < 0.2:
                add(0, min(len(source), 4000), 'Uniform paragraph lengths')
        if check_id == 'style.reading-level' and fp['words'] >= 100 and fp['metrics']['sentence_mean'] > 30:
            add(0, min(len(source), 4000), 'Sustained long sentences increase reading burden', sentence_mean=fp['metrics']['sentence_mean'])
    elif check_id in ('prose.ai-tells', 'prose.structural-tics'):
        patterns = [r'\bcould(?:n[\'’]?t| not) help but\b', r'\beyes?\s+widened\b', r'\blet out (?:a|the) breath\b[^.!?\n]{0,60}\b(?:holding|held)\b', r'\ba wave of\s+[a-z]+\s+(?:washed|crashed|swept) over\b', r'\bheart\s+(?:pounded|hammered|thudded|slammed|raced)\s+(?:in|against)\s+(?:his|her|their|my|its)\s+chest\b', r'\ba sense of\s+(?:dread|unease|foreboding|wonder|awe|relief|peace|urgency)\b'] if check_id == 'prose.ai-tells' else [r'\bnot\s+(?:just|only)\b[^.!?\n]{1,100}?\bbut\b(?:\s+also)?[^.!?\n]{1,100}[.!?]', r'\bI[\'’]?m\s+not\s+saying\b[^.!?\n]{1,100}?\bI[\'’]?m\s+saying\b[^.!?\n]{1,100}[.!?]']
        for pattern in patterns:
            match = re.search(pattern, source, re.I)
            if match:
                add(*match.span(), 'Stock prose pattern; this is not evidence of AI authorship')
        if check_id == 'prose.structural-tics':
            run = []
            for sentence in sentences:
                words = WORD.findall(sentence[0])
                run = run + [sentence] if len(words) <= 5 else []
                if len(run) == 3:
                    add(run[0].start(), run[-1].end(), 'Three consecutive short sentences')
                    run = []
    elif check_id == 'dialogue.said-bookisms':
        for base in LEX['SAID_BOOKISMS'] + LEX['NON_SPEECH_TAGS']:
            for token in tokens:
                if token[0] != token[0].lower() or token[0] not in forms(base):
                    continue
                before = source[max(0, token.start() - 50):token.start()]
                after = source[token.end():token.end() + 50]
                if re.search(r'[,?!][”"]\s+(?:[\w’\'-]+\s+)?$', before) or re.match(r'\s*,\s*[“"]', after):
                    add(*token.span(), 'Ornate or non-speech dialogue tag: ' + base)
    elif check_id == 'dialogue.attribution-clarity':
        run = []
        for line in re.finditer(r'[^\n]+', source):
            if re.fullmatch(r'\s*[“"][^“”"]+[”"]\s*', line[0]):
                run.append(line)
                if len(run) == 6:
                    add(*run[0].span(), 'Six unattributed dialogue lines')
            else:
                run = []
    elif check_id == 'dialogue.tag-variety':
        tags = list(re.finditer(r'[,?!][”"]\s+(?:[\w’\'-]+\s+)?(said|asked|replied|whispered|shouted|muttered|answered)\b', source, re.I))
        if len(tags) >= 5:
            counts = {word: [m for m in tags if m[1].casefold() == word] for word in {m[1].casefold() for m in tags}}
            repeated = max(counts.values(), key=len)
            if len(repeated) / len(tags) >= 0.8:
                add(*repeated[0].span(1), 'Dominant dialogue tag', occurrences=len(repeated), total_tags=len(tags))
    return sorted(findings, key=lambda finding: finding['start'])
