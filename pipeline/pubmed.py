"""PubMed E-utilities: search, fetch records with abstracts, and citation links. Standard library only."""
import json
import os
import time
import urllib.parse
import xml.etree.ElementTree as ET

from . import PipelineError, net

BASE = 'https://eutils.ncbi.nlm.nih.gov/entrez/eutils/'
_last = [0.0]


def _params(extra):
    p = {'tool': 'metareview', **extra}
    if os.environ.get('NCBI_EMAIL'):
        p['email'] = os.environ['NCBI_EMAIL']
    if os.environ.get('NCBI_API_KEY'):
        p['api_key'] = os.environ['NCBI_API_KEY']
    return p


def _get(endpoint, params, retries=3):
    wait = 0.11 if os.environ.get('NCBI_API_KEY') else 0.35
    for attempt in range(retries):
        gap = time.monotonic() - _last[0]
        if gap < wait:
            time.sleep(wait - gap)
        url = BASE + endpoint + '?' + urllib.parse.urlencode(_params(params))
        _last[0] = time.monotonic()
        try:
            return net.get(url)
        except Exception as exc:  # network, HTTP 429/5xx
            if attempt == retries - 1:
                raise PipelineError(f'NCBI request failed ({endpoint}): {exc}')
            time.sleep(2 * (attempt + 1))


def count(query, date_from=None, date_to=None):
    data = json.loads(_get('esearch.fcgi', {'db': 'pubmed', 'term': query, 'retmax': 0, 'retmode': 'json',
                                             **_dates(date_from, date_to)}))
    res = data.get('esearchresult', {})
    if 'ERROR' in res or res.get('errorlist', {}).get('phrasesnotfound'):
        return int(res.get('count', 0)), res.get('errorlist', {})
    return int(res.get('count', 0)), {}


def _dates(date_from, date_to):
    if not (date_from or date_to):
        return {}
    return {'datetype': 'pdat', 'mindate': date_from or '1800', 'maxdate': date_to or '3000'}


def search_ids(query, date_from=None, date_to=None, limit=5000):
    ids, start = [], 0
    while True:
        data = json.loads(_get('esearch.fcgi', {'db': 'pubmed', 'term': query, 'retstart': start, 'retmax': min(1000, limit - start),
                                                 'retmode': 'json', **_dates(date_from, date_to)}))
        res = data.get('esearchresult', {})
        batch = res.get('idlist', [])
        ids += batch
        total = int(res.get('count', 0))
        start += len(batch)
        if not batch or start >= min(total, limit):
            return ids, total


def fetch(pmids):
    """Return a list of record dicts for the given PMIDs (batches of 200)."""
    out = []
    pmids = [str(p) for p in pmids]
    for i in range(0, len(pmids), 200):
        xml = _get('efetch.fcgi', {'db': 'pubmed', 'id': ','.join(pmids[i:i + 200]), 'retmode': 'xml'})
        out += parse(xml)
    return out


def parse(xml_bytes):
    root = ET.fromstring(xml_bytes)
    records = []
    for art in root.iter('PubmedArticle'):
        cit = art.find('MedlineCitation')
        article = cit.find('Article')
        pmid = cit.findtext('PMID')
        title = ''.join(article.find('ArticleTitle').itertext()) if article.find('ArticleTitle') is not None else ''
        abstract = ' '.join(
            (('' if not a.get('Label') else a.get('Label') + ': ') + ''.join(a.itertext())).strip()
            for a in article.findall('Abstract/AbstractText'))
        year = (article.findtext('Journal/JournalIssue/PubDate/Year') or article.findtext('Journal/JournalIssue/PubDate/MedlineDate') or '')[:4]
        authors = []
        for a in article.findall('AuthorList/Author'):
            last = a.findtext('LastName') or a.findtext('CollectiveName')
            if last:
                authors.append((last + ' ' + (a.findtext('Initials') or '')).strip())
        ids = {i.get('IdType'): i.text for i in art.findall('PubmedData/ArticleIdList/ArticleId')}
        records.append({
            'pmid': pmid, 'title': title.strip(), 'abstract': abstract.strip(), 'year': year,
            'authors': authors, 'journal': article.findtext('Journal/Title') or '',
            'doi': ids.get('doi'), 'pmcid': ids.get('pmc'),
            'pubtypes': [t.text for t in article.findall('PublicationTypeList/PublicationType')],
            'mesh': [m.findtext('DescriptorName') for m in cit.findall('MeshHeadingList/MeshHeading')],
            'language': cit.findtext('Article/Language') or article.findtext('Language') or '',
        })
    return records


def linked(pmids, linkname):
    """PMIDs linked to the given ones: 'pubmed_pubmed_citedin' (forward) or 'pubmed_pubmed_refs' (backward)."""
    found = set()
    for i in range(0, len(pmids), 100):
        data = json.loads(_get('elink.fcgi', {'dbfrom': 'pubmed', 'db': 'pubmed', 'id': ','.join(map(str, pmids[i:i + 100])),
                                               'linkname': linkname, 'retmode': 'json'}))
        for ls in data.get('linksets', []):
            for db in ls.get('linksetdbs', []):
                found.update(db.get('links', []))
    return sorted(found - set(map(str, pmids)))


def mesh_exists(term):
    data = json.loads(_get('esearch.fcgi', {'db': 'mesh', 'term': term + '[MeSH Terms]', 'retmode': 'json', 'retmax': 1}))
    return int(data.get('esearchresult', {}).get('count', 0)) > 0
