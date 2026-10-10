"""Full text: open-access XML from Europe PMC, or files the researcher drops into fulltext/uploads/."""
import re
import xml.etree.ElementTree as ET

from . import net

EPMC = 'https://www.ebi.ac.uk/europepmc/webservices/rest/'
SECTION_TAGS = {'title', 'label'}


def _fetch(url):
    try:
        return net.get(url)
    except Exception:
        return None


def xml_to_text(xml_bytes):
    """Flatten JATS XML to readable text with section headings; tables become tab-separated rows."""
    root = ET.fromstring(xml_bytes)
    body = root.find('.//body')
    parts = []
    front = root.find('.//front')
    if front is not None:
        abstract = front.find('.//abstract')
        if abstract is not None:
            parts.append('ABSTRACT\n' + ' '.join(abstract.itertext()))
    def walk(node, depth=0):
        tag = node.tag.split('}')[-1]
        if tag == 'sec':
            title = node.find('title')
            if title is not None:
                parts.append('\n' + '#' * min(depth + 1, 4) + ' ' + ''.join(title.itertext()).strip())
            for child in node:
                if child is not title:
                    walk(child, depth + 1)
        elif tag == 'table-wrap':
            label = node.find('label'); cap = node.find('caption')
            head = ' '.join(x for x in [''.join(label.itertext()) if label is not None else '',
                                        ''.join(cap.itertext()) if cap is not None else ''] if x)
            parts.append('\nTABLE ' + head.strip())
            for row in node.iter('tr'):
                cells = [' '.join(''.join(c.itertext()).split()) for c in row if c.tag in ('td', 'th')]
                parts.append('\t'.join(cells))
        elif tag in ('p', 'fig', 'list', 'disp-quote', 'boxed-text'):
            text = ' '.join(''.join(node.itertext()).split())
            if text:
                parts.append(text)
        else:
            for child in node:
                walk(child, depth)
    if body is not None:
        for child in body:
            walk(child)
    return '\n'.join(parts).strip()


def lookup_pmcid(pmid):
    """Europe PMC often knows a PMC id (or an open-access full text) that the PubMed record does not list."""
    import json
    data = _fetch(f'{EPMC}search?query=ext_id:{pmid}%20AND%20src:med&format=json&resultType=lite')
    if not data:
        return None
    try:
        hits = json.loads(data).get('resultList', {}).get('result', [])
    except ValueError:
        return None
    for h in hits:
        if h.get('pmcid'):
            return h['pmcid']
    return None


def europepmc(pmcid, pmid=None):
    if not pmcid and pmid:
        pmcid = lookup_pmcid(pmid)
    if not pmcid:
        return None
    pmcid = pmcid if str(pmcid).upper().startswith('PMC') else 'PMC' + str(pmcid)
    data = _fetch(f'{EPMC}{pmcid}/fullTextXML')
    if not data or b'<body' not in data:
        return None
    try:
        text = xml_to_text(data)
    except ET.ParseError:
        return None
    return text if len(text) > 1500 else None


def pdf_text(path):
    try:
        from pypdf import PdfReader
    except ImportError:
        return None, 'pypdf not installed: run  pip install pypdf  or save the text as <pmid>.txt instead'
    try:
        reader = PdfReader(str(path))
        text = '\n'.join((page.extract_text() or '') for page in reader.pages)
    except Exception as exc:
        return None, f'could not read PDF: {exc}'
    if len(re.sub(r'\s+', '', text)) < 1500:
        return None, 'PDF has no extractable text (scanned?); save a .txt version instead'
    return text, None


def uploaded(review, pmid):
    """Text for a file the researcher placed in fulltext/uploads/ as <pmid>.txt|.md|.pdf."""
    for ext in ('.txt', '.md'):
        p = review.path(f'fulltext/uploads/{pmid}{ext}')
        if p.is_file():
            return p.read_text(encoding='utf-8', errors='replace'), None
    p = review.path(f'fulltext/uploads/{pmid}.pdf')
    if p.is_file():
        return pdf_text(p)
    return None, None


def doi_to_pmcid(doi):
    import json
    if not doi:
        return None
    data = _fetch(f'{EPMC}search?query=DOI:%22{doi}%22&format=json&resultType=lite')
    try:
        hits = json.loads(data).get('resultList', {}).get('result', []) if data else []
    except ValueError:
        return None
    return next((h['pmcid'] for h in hits if h.get('pmcid')), None)


def registry_text(nct):
    """ClinicalTrials.gov record (protocol and any posted results) flattened to readable 'path: value' lines."""
    import json
    data = _fetch(f'https://clinicaltrials.gov/api/v2/studies/{nct}')
    if not data:
        return None
    try:
        study = json.loads(data)
    except ValueError:
        return None
    lines = []

    def walk(node, path):
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, f'{path}.{k}' if path else k)
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f'{path}[{i}]')
        elif node not in (None, ''):
            lines.append(f'{path}: {node}')
    walk({k: v for k, v in study.items() if k in ('protocolSection', 'resultsSection')}, '')
    text = '\n'.join(lines)
    return text if len(text) > 500 else None


def oa_pdf(url):
    if not url:
        return None
    data = _fetch(url)
    if not data or not data[:5].startswith(b'%PDF'):
        return None
    import io
    try:
        from pypdf import PdfReader
    except ImportError:
        return None
    try:
        reader = PdfReader(io.BytesIO(data))
        text = '\n'.join((page.extract_text() or '') for page in reader.pages)
    except Exception:
        return None
    return text if len(re.sub(r'\s+', '', text)) > 1500 else None


def retrieve(rec):
    """Try every open route for a record's full text. Returns (text, source)."""
    if rec.get('nct_id'):
        return registry_text(rec['nct_id']), 'clinicaltrials.gov'
    pmcid = rec.get('pmcid') or (lookup_pmcid(rec['pubmed_id']) if rec.get('pubmed_id') else None) or doi_to_pmcid(rec.get('doi'))
    text = europepmc(pmcid) if pmcid else None
    if text:
        return text, 'europepmc'
    text = oa_pdf(rec.get('oa_url'))
    if text:
        return text, 'open-access pdf'
    return None, None
