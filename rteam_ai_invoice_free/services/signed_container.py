# Part of Auto Extract any Bill PDF/JPEG. See LICENSE file for full copyright and licensing details.
"""Unwrap documents carried inside a PKCS#7 / CMS signature container.

Banks (notably Ukrainian ones: monobank / Universal Bank, PrivatBank, etc.)
deliver payment receipts and invoices as a qualified electronic signature
envelope (КЕП / CAdES) that often keeps a ``.pdf`` file name. The bytes are an
ASN.1 DER CMS ``SignedData`` structure, not a PDF, so the AI gateway - and any
ordinary PDF viewer - cannot read them. The real document sits in the
encapsulated content (``pkcs7-data`` OCTET STRING).

We only *unwrap*, never *verify*: UA signatures use the DSTU GOST 34311-95 hash,
which the standard crypto stack does not implement, so verification is
impossible here anyway and is not our job. Extraction is dependency-free - a
small DER reader plus a magic-bytes fallback.
"""
import logging

_logger = logging.getLogger(__name__)

# OBJECT IDENTIFIER 1.2.840.113549.1.7.2 - CMS/PKCS#7 signedData
_OID_SIGNED_DATA = b"\x06\x09\x2a\x86\x48\x86\xf7\x0d\x01\x07\x02"
# OBJECT IDENTIFIER 1.2.840.113549.1.7.1 - pkcs7-data (the encapsulated content)
_OID_PKCS7_DATA = b"\x06\x09\x2a\x86\x48\x86\xf7\x0d\x01\x07\x01"

# Magic prefixes of the document types the gateway can extract. Used to validate
# whatever we pull out of the envelope before trusting it.
_SUPPORTED_MAGIC = (
    b"%PDF-",            # PDF
    b"\xff\xd8\xff",     # JPEG
    b"\x89PNG\r\n\x1a\n",  # PNG
    b"PK\x03\x04",       # ZIP container (XLSX / docx / Google Sheets export)
)


def looks_like_signed_container(file_bytes: bytes) -> bool:
    """True when the bytes are a CMS/PKCS#7 signedData envelope (any file name).

    A DER structure starts with SEQUENCE (0x30); the signedData OID appears in
    the header region. We scan a small prefix so a stray match deep in a real
    PDF/image never trips the detector.
    """
    if not file_bytes or file_bytes[0] != 0x30:
        return False
    return _OID_SIGNED_DATA in file_bytes[:64]


def _read_der_length(buf: bytes, pos: int):
    """Return (length, new_pos). length is None for the indefinite form (0x80)."""
    if pos >= len(buf):
        return None, pos
    first = buf[pos]
    pos += 1
    if first < 0x80:
        return first, pos
    if first == 0x80:
        return None, pos
    num_bytes = first & 0x7F
    length = int.from_bytes(buf[pos:pos + num_bytes], "big")
    return length, pos + num_bytes


def _extract_via_asn1(file_bytes: bytes):
    """Extract the encapsulated content by walking the DER structure.

    encapContentInfo ::= SEQUENCE { eContentType OID pkcs7-data,
                                    eContent [0] EXPLICIT OCTET STRING }
    We locate the first pkcs7-data OID (the encapContentInfo one precedes the
    signed-attribute copies), then read the [0] wrapper and the OCTET STRING.
    """
    idx = file_bytes.find(_OID_PKCS7_DATA)
    if idx == -1:
        return None
    pos = idx + len(_OID_PKCS7_DATA)
    # [0] EXPLICIT context tag wrapping the eContent.
    if pos < len(file_bytes) and file_bytes[pos] == 0xA0:
        pos += 1
        _outer_len, pos = _read_der_length(file_bytes, pos)
    if pos >= len(file_bytes):
        return None
    tag = file_bytes[pos]
    if tag not in (0x04, 0x24):  # OCTET STRING (primitive) or constructed
        return None
    pos += 1
    length, pos = _read_der_length(file_bytes, pos)
    if tag == 0x04 and length is not None:
        return file_bytes[pos:pos + length] or None
    # Constructed / indefinite OCTET STRING: concatenate the inner primitive
    # chunks until we run out or hit an end-of-contents marker.
    chunks = []
    end = len(file_bytes) if length is None else pos + length
    while pos < end:
        if file_bytes[pos] != 0x04:
            break
        pos += 1
        chunk_len, pos = _read_der_length(file_bytes, pos)
        if chunk_len is None:
            break
        chunks.append(file_bytes[pos:pos + chunk_len])
        pos += chunk_len
    data = b"".join(chunks)
    return data or None


def _carve_pdf(file_bytes: bytes):
    """Fallback: carve a PDF out by its %PDF .. %%EOF boundaries.

    Used only when the DER walk fails. PDF readers parse from the trailer, so we
    must cut at the document's own final %%EOF rather than leave the trailing
    signature bytes attached.
    """
    start = file_bytes.find(b"%PDF-")
    if start == -1:
        return None
    end = file_bytes.rfind(b"%%EOF")
    if end == -1:
        return None
    end += len(b"%%EOF")
    # Keep a single trailing newline if present (some writers require it).
    if file_bytes[end:end + 1] in (b"\r", b"\n"):
        end += 1
    return file_bytes[start:end] or None


def _is_supported_document(data: bytes) -> bool:
    return bool(data) and any(data.startswith(m) for m in _SUPPORTED_MAGIC)


def unwrap_signed_document(file_bytes: bytes):
    """Return the original document inside a signature envelope, or None.

    None means "not a signed container we can unwrap" - the caller should send
    the bytes through unchanged. A returned value is always a recognised
    document type (PDF / JPEG / PNG / ZIP-based).
    """
    if not looks_like_signed_container(file_bytes):
        return None
    extracted = _extract_via_asn1(file_bytes)
    if _is_supported_document(extracted):
        return extracted
    carved = _carve_pdf(file_bytes)
    if _is_supported_document(carved):
        return carved
    return None
