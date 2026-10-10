"""Bibliographic sources: the agent chooses which to search per question; every source is searched exhaustively.

Each source module exposes count(query, date_from, date_to) -> int and fetch_all(...) -> (records, total).
Records are normalized dicts keyed by a filesystem-safe record id ('pmid' field, kept for compatibility):
the real PMID when one exists, else OA-W..., S2-..., EPMC-..., or an NCT number.
"""
import html
import json
import os
import re
import time
import urllib.parse

from . import PipelineError, net, pubmed

CATALOG = {
    'pubmed': {
        'covers': 'MEDLINE and PubMed (~38M biomedical and life-science records). Every hit of the query is retrieved; '
                  'queries above 9,999 hits are split by publication date so nothing is truncated.',
        'best_for': 'clinical, medical, public-health, nursing, pharmacology and biomedical questions',
        'syntax': 'PubMed Boolean: MeSH via term[MeSH Terms], free text via term[tiab], truncation colchicin*[tiab], '
                  'publication type via randomized controlled trial[pt]; AND/OR/NOT with parentheses.'},
    'europepmc': {
        'covers': 'Europe PMC (~45M): all of PubMed plus PMC full texts, preprints (bioRxiv, medRxiv, Research Square), '
                  'Agricola, Chinese Biological Abstracts, theses and patents.',
        'best_for': 'biomedical and life-science questions (adds preprints and non-MEDLINE journals); a strong second source for health topics',
        'syntax': 'Europe PMC syntax: TITLE_ABS:"wildfire" or TITLE_ABS:(colchicine AND coronary); AND/OR/NOT; quotes for phrases; '
                  'MESH:"Myocardial Infarction" for MeSH; wildcard colchicin*.'},
    'openalex': {
        'covers': 'OpenAlex (~250M works, all disciplines): journals, working papers (NBER, SSRN, RePEc), reports, books, '
                  'dissertations; strong in economics, social science, environment, engineering, education and policy.',
        'best_for': 'economics, labour, income, policy, social-science, environmental, disaster and interdisciplinary questions',
        'syntax': 'Title-and-abstract text search with AND/OR/NOT (upper case), parentheses and "quoted phrases"; '
                  'stemming is automatic (no wildcards). No commas allowed.'},
    'semanticscholar': {
        'covers': 'Semantic Scholar (~220M papers, all disciplines, strong in computer science, economics and social science, '
                  'with arXiv/SSRN preprints).',
        'best_for': 'a second multidisciplinary source alongside OpenAlex for non-biomedical questions',
        'syntax': 'Bulk search syntax: + for AND, | for OR, - for NOT, "quoted phrases", prefix* wildcards, parentheses. '
                  'Example: wildfire + (income | wage* | employment)'},
    'clinicaltrials': {
        'covers': 'ClinicalTrials.gov registry (~550k studies), including unpublished and ongoing trials and posted results.',
        'best_for': 'any question about interventions tested in trials (drugs, devices, vaccines, procedures); finds unpublished trials',
        'syntax': 'Plain terms with AND/OR/NOT and parentheses, e.g. colchicine AND (coronary OR myocardial infarction).'},
}

BIOMED = r'\b(patient|clinical|disease|mortality|death|therap|treat|drug|medic|vaccin|trial|hospital|cancer|tumou?r|cardi|coronary|' \
         r'heart|stroke|diabet|infect|surg|symptom|syndrome|pregnan|birth|child health|nurs|pharma|dose|biomarker|lipoprotein|' \
         r'myocard|arter|blood|renal|kidney|liver|lung|asthma|copd|depress|anxiety|dementia|obes|screening|diagnos)'
SOCIAL = r'\b(income|wage|earning|employment|unemploy|labou?r|economic|econom|gdp|poverty|household|firm|business|market|price|' \
         r'housing|rent|education|school|policy|crime|migration|tourism|agricultur|farm|climate|environment|wildfire|bushfire|' \
         r'flood|hurricane|disaster|energy|emission|land use|urban|rural|recovery|insurance|tax|trade|productivity|welfare)'
INTERVENTION = r'\b(drug|vaccin|therap|treat|trial|intervention|supplement|procedure|surgery|device|dose|randomi[sz]ed|' \
               r'reduce|prevent|efficacy|effective|versus|vs\.?|compared|improve|lower)'


def suggest(question, pico=None):
    """Suggested sources with reasons, from the question's vocabulary. The agent may change them; it must justify."""
    text = (question + ' ' + ' '.join(str(v) for v in (pico or {}).values())).lower()
    bio = bool(re.search(BIOMED, text))
    soc = bool(re.search(SOCIAL, text))
    out = []
    if bio or not soc:
        out.append(('pubmed', 'biomedical question: MEDLINE/PubMed is the core database'))
        out.append(('europepmc', 'adds preprints, PMC-only and non-MEDLINE journals to PubMed'))
        if re.search(INTERVENTION, text):
            out.append(('clinicaltrials', 'intervention question: trial registry finds unpublished and ongoing trials'))
    if soc:
        out.append(('openalex', 'economic/social/environmental vocabulary: multidisciplinary index with working papers and reports'))
        if not bio:
            out.append(('semanticscholar', 'second multidisciplinary index (economics, social science, SSRN/arXiv preprints)'))
    return out


def _years(date_from, date_to):
    lo = int(str(date_from)[:4]) if date_from else None
    hi = int(str(date_to)[:4]) if date_to else None
    return lo, hi


def _clean(text):
    text = html.unescape(re.sub(r'<[^>]+>', ' ', text or ''))
    return re.sub(r'\s+', ' ', text).strip()


def _doi(value):
    if not value:
        return None
    return re.sub(r'^https?://(dx\.)?doi\.org/', '', str(value).strip(), flags=re.I).lower() or None


def _get_json(url, retries=4):
    last = None
    for attempt in range(retries):
        try:
            return json.loads(net.get(url))
        except Exception as exc:  # 429/5xx/network
            last = exc
            time.sleep(2 * (attempt + 1))
    raise PipelineError(f'request failed after {retries} attempts: {url[:120]} ({last})')


def _mailto():
    email = os.environ.get('NCBI_EMAIL')
    return f'&mailto={urllib.parse.quote(email)}' if email else ''


# ----------------------------------------------------------------------------- PubMed

class PubMed:
    name = 'pubmed'
    SLICE = 9999  # ESearch cannot page past 9,999; larger result sets are split by publication date

    def count(self, query, date_from=None, date_to=None):
        n, errs = pubmed.count(query, date_from, date_to)
        if errs:
            raise PipelineError('PubMed reported problems with the query: ' + json.dumps(errs)[:400])
        return n

    def fetch_all(self, query, date_from=None, date_to=None, progress=None):
        ids, total = pubmed.search_ids(query, date_from, date_to, limit=self.SLICE)
        if total > len(ids):
            lo, hi = _years(date_from, date_to)
            ids = sorted(set(self._sliced(query, lo or 1800, hi or time.gmtime().tm_year, progress)))
        if progress:
            progress(f'  pubmed: {len(ids)} of {total} PMIDs collected; fetching records')
        records = pubmed.fetch(ids)
        for r in records:
            r.update(pubmed_id=r['pmid'], sources=['pubmed'])
        return records, total

    def _sliced(self, query, lo, hi, progress):
        n = pubmed.count(query, str(lo), str(hi))[0]
        if n == 0:
            return []
        if n <= self.SLICE:
            return pubmed.search_ids(query, str(lo), str(hi), limit=self.SLICE)[0]
        if lo < hi:
            mid = (lo + hi) // 2
            return self._sliced(query, lo, mid, progress) + self._sliced(query, mid + 1, hi, progress)
        out = []  # one year above 9,999 hits: split by month
        for m in range(1, 13):
            got = pubmed.search_ids(query, f'{lo}/{m:02d}/01', f'{lo}/{m:02d}/31', limit=self.SLICE)[0]
            out += got
        return out


# ----------------------------------------------------------------------------- Europe PMC

class EuropePMC:
    name = 'europepmc'
    BASE = 'https://www.ebi.ac.uk/europepmc/webservices/rest/search'

    def _q(self, query, date_from, date_to):
        lo, hi = _years(date_from, date_to)
        if lo or hi:
            query = f'({query}) AND FIRST_PDATE:[{lo or 1800}-01-01 TO {hi or 3000}-12-31]'
        return urllib.parse.quote(query)

    def count(self, query, date_from=None, date_to=None):
        d = _get_json(f'{self.BASE}?query={self._q(query, date_from, date_to)}&format=json&pageSize=1&resultType=lite')
        return int(d.get('hitCount', 0))

    def fetch_all(self, query, date_from=None, date_to=None, progress=None):
        cursor, out, total = '*', [], None
        q = self._q(query, date_from, date_to)
        while True:
            d = _get_json(f'{self.BASE}?query={q}&format=json&pageSize=1000&resultType=core&cursorMark={urllib.parse.quote(cursor)}')
            total = int(d.get('hitCount', 0)) if total is None else total
            rows = d.get('resultList', {}).get('result', [])
            out += [self.record(r) for r in rows]
            nxt = d.get('nextCursorMark')
            if progress:
                progress(f'  europepmc: {len(out)}/{total}')
            if not rows or not nxt or nxt == cursor:
                return out, total
            cursor = nxt

    @staticmethod
    def record(r):
        pmid = r.get('pmid')
        rid = pmid or f"EPMC-{r.get('source', 'X')}{r.get('id')}"
        authors = [a.strip() for a in (r.get('authorString') or '').rstrip('.').split(',') if a.strip()]
        return {'pmid': re.sub(r'[^A-Za-z0-9_-]', '', rid), 'pubmed_id': pmid, 'title': _clean(r.get('title')),
                'abstract': _clean(r.get('abstractText')), 'year': str(r.get('pubYear') or ''), 'authors': authors,
                'journal': ((r.get('journalInfo') or {}).get('journal') or {}).get('title') or '',
                'doi': _doi(r.get('doi')), 'pmcid': r.get('pmcid'), 'pubtypes': list((r.get('pubTypeList') or {}).get('pubType') or []),
                'mesh': [], 'language': r.get('language') or '', 'sources': ['europepmc'],
                'preprint': r.get('source') == 'PPR'}


# ----------------------------------------------------------------------------- OpenAlex

class OpenAlex:
    name = 'openalex'
    BASE = 'https://api.openalex.org/works'
    TYPES = {'review': 'Review', 'editorial': 'Editorial', 'letter': 'Letter', 'erratum': 'Published Erratum',
             'retraction': 'Retraction of Publication', 'paratext': 'Editorial', 'peer-review': 'Comment'}

    def _filter(self, query, date_from, date_to):
        if ',' in query:
            raise PipelineError('OpenAlex queries cannot contain commas; use AND/OR instead')
        f = f'title_and_abstract.search:{query}'
        lo, hi = _years(date_from, date_to)
        if lo:
            f += f',from_publication_date:{lo}-01-01'
        if hi:
            f += f',to_publication_date:{hi}-12-31'
        return urllib.parse.quote(f, safe=':,')

    def count(self, query, date_from=None, date_to=None):
        d = _get_json(f'{self.BASE}?filter={self._filter(query, date_from, date_to)}&per_page=1{_mailto()}')
        return int(d.get('meta', {}).get('count', 0))

    def fetch_all(self, query, date_from=None, date_to=None, progress=None):
        cursor, out, total = '*', [], None
        sel = 'id,doi,title,publication_year,type,language,ids,open_access,authorships,primary_location,abstract_inverted_index'
        while cursor:
            d = _get_json(f'{self.BASE}?filter={self._filter(query, date_from, date_to)}&per_page=200&cursor={urllib.parse.quote(cursor)}&select={sel}{_mailto()}')
            total = int(d.get('meta', {}).get('count', 0)) if total is None else total
            rows = d.get('results', [])
            out += [self.record(w) for w in rows]
            cursor = d.get('meta', {}).get('next_cursor') if rows else None
            if progress:
                progress(f'  openalex: {len(out)}/{total}')
        return out, total

    @classmethod
    def record(cls, w):
        ids = w.get('ids') or {}
        pmid = re.sub(r'\D', '', str(ids.get('pmid') or '')) or None
        pmcid = re.sub(r'.*/', '', str(ids.get('pmcid') or '')) or None
        wid = str(w.get('id') or '').rsplit('/', 1)[-1]
        inv = w.get('abstract_inverted_index') or {}
        words = sorted(((pos, word) for word, poss in inv.items() for pos in poss))
        abstract = ' '.join(word for _, word in words)
        authors = []
        for a in w.get('authorships') or []:
            name = ((a.get('author') or {}).get('display_name') or '').split()
            if name:
                authors.append(name[-1] + ' ' + ''.join(p[0] for p in name[:-1]))
        loc = w.get('primary_location') or {}
        typ = w.get('type') or ''
        oa = w.get('open_access') or {}
        return {'pmid': pmid or f'OA-{wid}', 'pubmed_id': pmid, 'title': _clean(w.get('title')), 'abstract': abstract,
                'year': str(w.get('publication_year') or ''), 'authors': authors,
                'journal': ((loc.get('source') or {}) or {}).get('display_name', '') if loc.get('source') else '',
                'doi': _doi(w.get('doi')), 'pmcid': pmcid.upper() if pmcid else None,
                'pubtypes': [cls.TYPES.get(typ, typ.replace('-', ' ').title())] if typ else [],
                'mesh': [], 'language': w.get('language') or '', 'sources': ['openalex'],
                'oa_url': oa.get('oa_url'), 'preprint': typ == 'preprint'}


# ----------------------------------------------------------------------------- Semantic Scholar

class SemanticScholar:
    name = 'semanticscholar'
    BASE = 'https://api.semanticscholar.org/graph/v1/paper/search/bulk'
    TYPES = {'Review': 'Review', 'Editorial': 'Editorial', 'LettersAndComments': 'Letter', 'News': 'News'}

    def _params(self, query, date_from, date_to):
        p = f'query={urllib.parse.quote(query)}'
        lo, hi = _years(date_from, date_to)
        if lo or hi:
            p += f"&year={lo or ''}-{hi or ''}"
        return p

    def count(self, query, date_from=None, date_to=None):
        d = _get_json(f'{self.BASE}?{self._params(query, date_from, date_to)}&fields=title')
        return int(d.get('total', 0))

    def fetch_all(self, query, date_from=None, date_to=None, progress=None):
        token, out, total = None, [], None
        fields = 'title,abstract,year,externalIds,publicationTypes,authors,venue,openAccessPdf'
        while True:
            url = f'{self.BASE}?{self._params(query, date_from, date_to)}&fields={fields}' + (f'&token={urllib.parse.quote(token)}' if token else '')
            d = _get_json(url)
            total = int(d.get('total', 0)) if total is None else total
            out += [self.record(p) for p in d.get('data') or []]
            token = d.get('token')
            if progress:
                progress(f'  semanticscholar: {len(out)}/{total}')
            if not token:
                return out, total
            time.sleep(1.1)  # unauthenticated rate limit

    @classmethod
    def record(cls, p):
        ext = p.get('externalIds') or {}
        pmid = str(ext.get('PubMed') or '') or None
        pmcid = ext.get('PubMedCentral')
        types = [cls.TYPES.get(t, t) for t in (p.get('publicationTypes') or [])]
        authors = []
        for a in p.get('authors') or []:
            name = (a.get('name') or '').split()
            if name:
                authors.append(name[-1] + ' ' + ''.join(x[0] for x in name[:-1]))
        pdf = (p.get('openAccessPdf') or {}).get('url') or None
        return {'pmid': pmid or f"S2-{ext.get('CorpusId') or p.get('paperId')}", 'pubmed_id': pmid, 'title': _clean(p.get('title')),
                'abstract': _clean(p.get('abstract')), 'year': str(p.get('year') or ''), 'authors': authors,
                'journal': p.get('venue') or '', 'doi': _doi(ext.get('DOI')),
                'pmcid': ('PMC' + str(pmcid)) if pmcid and not str(pmcid).startswith('PMC') else pmcid,
                'pubtypes': types, 'mesh': [], 'language': '', 'sources': ['semanticscholar'], 'oa_url': pdf or None,
                'preprint': bool(ext.get('ArXiv')) and not ext.get('DOI')}


# ----------------------------------------------------------------------------- ClinicalTrials.gov

class ClinicalTrials:
    name = 'clinicaltrials'
    BASE = 'https://clinicaltrials.gov/api/v2/studies'

    def count(self, query, date_from=None, date_to=None):
        d = _get_json(f'{self.BASE}?query.term={urllib.parse.quote(query)}&pageSize=1&countTotal=true')
        return int(d.get('totalCount', 0))

    def fetch_all(self, query, date_from=None, date_to=None, progress=None):
        token, out, total = None, [], None
        lo, hi = _years(date_from, date_to)
        while True:
            url = f'{self.BASE}?query.term={urllib.parse.quote(query)}&pageSize=1000&countTotal=true' + (f'&pageToken={token}' if token else '')
            d = _get_json(url)
            total = int(d.get('totalCount', 0)) if total is None else total
            for s in d.get('studies') or []:
                r = self.record(s)
                y = int(r['year']) if r['year'].isdigit() else None
                if y and ((lo and y < lo) or (hi and y > hi)):
                    continue
                out.append(r)
            token = d.get('nextPageToken')
            if progress:
                progress(f'  clinicaltrials: {len(out)}/{total}')
            if not token:
                return out, total

    @staticmethod
    def record(s):
        p = s.get('protocolSection') or {}
        ident = p.get('identificationModule') or {}
        status = p.get('statusModule') or {}
        design = p.get('designModule') or {}
        desc = p.get('descriptionModule') or {}
        cond = (p.get('conditionsModule') or {}).get('conditions') or []
        arms = [i.get('name') for i in ((p.get('armsInterventionsModule') or {}).get('interventions') or []) if i.get('name')]
        start = ((status.get('startDateStruct') or {}).get('date') or '')[:4]
        nct = ident.get('nctId')
        summary = f"[Trial registration; status {status.get('overallStatus')}; type {design.get('studyType')}; " \
                  f"results posted: {'yes' if s.get('hasResults') else 'no'}; conditions: {', '.join(cond)}; interventions: {', '.join(arms)}] " \
                  + _clean(desc.get('briefSummary'))
        return {'pmid': nct, 'pubmed_id': None, 'nct_id': nct, 'title': ident.get('officialTitle') or ident.get('briefTitle') or nct,
                'abstract': summary, 'year': start, 'authors': [nct], 'journal': 'ClinicalTrials.gov', 'doi': None, 'pmcid': None,
                'pubtypes': ['Trial Registration', (design.get('studyType') or '').title()], 'mesh': [], 'language': 'eng',
                'sources': ['clinicaltrials'], 'registry': True, 'has_results': bool(s.get('hasResults'))}


SOURCES = {c.name: c() for c in (PubMed, EuropePMC, OpenAlex, SemanticScholar, ClinicalTrials)}
PRIORITY = ['pubmed', 'europepmc', 'openalex', 'semanticscholar', 'clinicaltrials']


def get(name):
    if name not in SOURCES:
        raise PipelineError(f'unknown source {name!r}; available: {", ".join(SOURCES)}')
    return SOURCES[name]


def title_key(title):
    return re.sub(r'[^a-z0-9]', '', (title or '').lower())[:90]


def deduplicate(records):
    """Merge duplicates across and within sources by PMID, DOI, NCT id or normalised title. Earlier sources in PRIORITY win;
    the merged record keeps the union of sources and fills missing fields (abstract, DOI, PMC id, open-access link)."""
    records = sorted(records, key=lambda r: min(PRIORITY.index(s) for s in r.get('sources', ['clinicaltrials'])))
    keep, index = [], {}
    for r in records:
        keys = [k for k in (('pmid', r.get('pubmed_id')), ('doi', r.get('doi')), ('nct', r.get('nct_id')),
                            ('title', title_key(r.get('title')) if len(title_key(r.get('title'))) >= 25 else None)) if k[1]]
        hit = next((index[k] for k in keys if k in index), None)
        if hit is None:
            keep.append(dict(r))
            hit = len(keep) - 1
        else:
            base = keep[hit]
            base['sources'] = sorted(set(base.get('sources', [])) | set(r.get('sources', [])), key=PRIORITY.index)
            for f in ('abstract', 'doi', 'pmcid', 'oa_url', 'pubmed_id', 'journal'):
                if not base.get(f) and r.get(f):
                    base[f] = r[f]
        for k in keys:
            index.setdefault(k, hit)
    return keep, len(records) - len(keep)
