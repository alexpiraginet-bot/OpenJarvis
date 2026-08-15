"""Safe financial document extraction for receipts and bank statements.

Raw files are processed in memory and never persisted here.  The caller stores
only a digest, metadata and bounded candidate transactions; every candidate
still becomes a pending Jarvis proposal before it can touch the ledger.
"""

from __future__ import annotations

import base64
import csv
import io
import json
import math
import re
import unicodedata
import uuid
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Dict, List, Protocol

from openjarvis.life import LifeContext


class FinancialDocumentError(ValueError):
    """Raised when a document is unsupported or cannot be parsed safely."""


class FinancialDocumentAnalyzer(Protocol):
    """Extract bounded candidates from a receipt, invoice or statement."""

    def analyze(
        self,
        *,
        user_id: str,
        filename: str,
        content_type: str,
        payload: bytes,
    ) -> Dict[str, Any]: ...


class FinancialDocumentStore:
    """Tenant-bound metadata store; raw attachment bytes are never persisted."""

    _RESERVATION_TTL = timedelta(minutes=5)

    def __init__(self, life: LifeContext) -> None:
        self._life = life

    def find_by_sha256(self, user_id: str, sha256: str) -> Dict[str, Any] | None:
        row = self._life.connection.execute(
            "SELECT * FROM financial_documents WHERE user_id = ? AND sha256 = ?",
            (user_id, sha256),
        ).fetchone()
        return self._hydrate(row) if row is not None else None

    def reserve(
        self,
        user_id: str,
        *,
        filename: str,
        content_type: str,
        sha256: str,
    ) -> tuple[Dict[str, Any], bool]:
        """Claim one analysis per tenant and content digest.

        The durable ``analyzing`` row is inserted before any paid provider call.
        A concurrent request sees the same row and cannot buy a second analysis.
        A crashed owner becomes recoverable after the bounded lease.
        """
        now = datetime.now(timezone.utc)
        document_id = uuid.uuid4().hex
        with self._life.store.transaction():
            inserted = self._life.connection.execute(
                "INSERT INTO financial_documents"
                " (id, user_id, filename, content_type, sha256, document_kind,"
                " status, extracted_json, proposals_json, created_at)"
                " VALUES (?, ?, ?, ?, ?, 'unknown', 'analyzing', '{}', '[]', ?)"
                " ON CONFLICT (user_id, sha256) DO NOTHING",
                (
                    document_id,
                    user_id,
                    filename,
                    content_type,
                    sha256,
                    now.isoformat(),
                ),
            )
            if inserted.rowcount == 1:
                row = self._life.connection.execute(
                    "SELECT * FROM financial_documents WHERE id = ? AND user_id = ?",
                    (document_id, user_id),
                ).fetchone()
                if row is None:  # pragma: no cover - insert invariant
                    raise FinancialDocumentError("analysis reservation was not saved")
                return self._hydrate(row), True

            row = self._life.connection.execute(
                "SELECT * FROM financial_documents WHERE user_id = ? AND sha256 = ?",
                (user_id, sha256),
            ).fetchone()
            if row is None:  # pragma: no cover - unique-conflict invariant
                raise FinancialDocumentError("analysis reservation was not found")
            existing = self._hydrate(row)
            stale_before = (now - self._RESERVATION_TTL).isoformat()
            if (
                existing["status"] == "analyzing"
                and str(existing["created_at"]) <= stale_before
            ):
                recovered_id = uuid.uuid4().hex
                recovered = self._life.connection.execute(
                    "UPDATE financial_documents"
                    " SET id = ?, filename = ?, content_type = ?, created_at = ?"
                    " WHERE id = ? AND user_id = ? AND status = 'analyzing'"
                    " AND created_at = ?",
                    (
                        recovered_id,
                        filename,
                        content_type,
                        now.isoformat(),
                        existing["id"],
                        user_id,
                        existing["created_at"],
                    ),
                )
                if recovered.rowcount == 1:
                    recovered_row = self._life.connection.execute(
                        "SELECT * FROM financial_documents"
                        " WHERE id = ? AND user_id = ?",
                        (recovered_id, user_id),
                    ).fetchone()
                    if recovered_row is None:  # pragma: no cover - CAS invariant
                        raise FinancialDocumentError(
                            "recovered analysis reservation was not found"
                        )
                    return self._hydrate(recovered_row), True
            return existing, False

    def release_reservation(self, user_id: str, document_id: str) -> None:
        """Release only the caller's unfinished reservation after a failure."""
        with self._life.store.transaction():
            self._life.connection.execute(
                "DELETE FROM financial_documents"
                " WHERE id = ? AND user_id = ? AND status = 'analyzing'",
                (document_id, user_id),
            )

    def complete_reservation(
        self,
        user_id: str,
        document_id: str,
        *,
        analysis: Dict[str, Any],
        proposal_ids: List[str],
    ) -> Dict[str, Any]:
        """Publish reviewed metadata only if this reservation still owns the row."""
        completed = self._life.connection.execute(
            "UPDATE financial_documents"
            " SET document_kind = ?, status = 'review_required',"
            " extracted_json = ?, proposals_json = ?"
            " WHERE id = ? AND user_id = ? AND status = 'analyzing'",
            (
                analysis["document_kind"],
                json.dumps(analysis, ensure_ascii=False, separators=(",", ":")),
                json.dumps(proposal_ids, separators=(",", ":")),
                document_id,
                user_id,
            ),
        )
        if completed.rowcount != 1:
            raise FinancialDocumentError("analysis reservation is no longer active")
        row = self._life.connection.execute(
            "SELECT * FROM financial_documents WHERE id = ? AND user_id = ?",
            (document_id, user_id),
        ).fetchone()
        if row is None:  # pragma: no cover - update invariant
            raise FinancialDocumentError("completed financial document was not found")
        return self._hydrate(row)

    def save(
        self,
        user_id: str,
        *,
        filename: str,
        content_type: str,
        sha256: str,
        analysis: Dict[str, Any],
        proposal_ids: List[str],
    ) -> Dict[str, Any]:
        document_id = uuid.uuid4().hex
        self._life.connection.execute(
            "INSERT INTO financial_documents"
            " (id, user_id, filename, content_type, sha256, document_kind,"
            " status, extracted_json, proposals_json, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, 'review_required', ?, ?, ?)",
            (
                document_id,
                user_id,
                filename,
                content_type,
                sha256,
                analysis["document_kind"],
                json.dumps(analysis, ensure_ascii=False, separators=(",", ":")),
                json.dumps(proposal_ids, separators=(",", ":")),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        row = self._life.connection.execute(
            "SELECT * FROM financial_documents WHERE id = ? AND user_id = ?",
            (document_id, user_id),
        ).fetchone()
        if row is None:  # pragma: no cover - same-transaction insert invariant
            raise FinancialDocumentError("financial document could not be saved")
        return self._hydrate(row)

    def list(self, user_id: str, *, limit: int = 20) -> List[Dict[str, Any]]:
        rows = self._life.connection.execute(
            "SELECT * FROM financial_documents"
            " WHERE user_id = ? AND status = 'review_required'"
            " ORDER BY created_at DESC LIMIT ?",
            (user_id, max(1, min(limit, 100))),
        ).fetchall()
        return [self._hydrate(row) for row in rows]

    @staticmethod
    def _hydrate(row: Any) -> Dict[str, Any]:
        document = dict(row)
        try:
            document["analysis"] = json.loads(document.pop("extracted_json"))
            document["proposal_ids"] = json.loads(document.pop("proposals_json"))
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise FinancialDocumentError(
                "stored document analysis is malformed"
            ) from exc
        return document


class OpenAIFinancialDocumentAnalyzer:
    """Analyze images/PDFs with structured output behind the shared AI cap."""

    _RESERVATION_MICRO_USD = 500_000

    def __init__(self, budget: Any, *, client: Any = None, model: str = "gpt-5-mini"):
        if client is None:
            from openai import OpenAI

            client = OpenAI()
        self._budget = budget
        self._client = client
        self._model = model

    def analyze(
        self,
        *,
        user_id: str,
        filename: str,
        content_type: str,
        payload: bytes,
    ) -> Dict[str, Any]:
        if content_type.startswith("image/"):
            document_input = {
                "type": "input_image",
                "image_url": (
                    f"data:{content_type};base64,{base64.b64encode(payload).decode()}"
                ),
                "detail": "high",
            }
        elif content_type == "application/pdf" or filename.lower().endswith(".pdf"):
            document_input = {
                "type": "input_file",
                "filename": filename,
                "file_data": base64.b64encode(payload).decode(),
            }
        else:
            raise FinancialDocumentError("receipt must be an image or PDF")

        reservation = self._budget.reserve(
            user_id, self._model, self._RESERVATION_MICRO_USD
        )
        try:
            response = self._client.responses.create(
                model=self._model,
                instructions=(
                    "Extraia somente fatos visíveis do documento financeiro. "
                    "Não invente valores, datas, favorecidos ou categorias. "
                    "Valores monetários devem ser inteiros em centavos."
                ),
                input=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "input_text",
                                "text": (
                                    "Classifique como comprovante ou extrato e extraia "
                                    "os lançamentos visíveis para revisão humana."
                                ),
                            },
                            document_input,
                        ],
                    }
                ],
                text={"format": self._response_format()},
                max_output_tokens=4000,
                timeout=40,
            )
            analysis = normalize_financial_analysis(
                json.loads(str(getattr(response, "output_text", "") or "{}"))
            )
            usage = getattr(response, "usage", None)
            input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
            output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
            from openjarvis.engine.cloud import estimate_cost

            cost_usd = estimate_cost(self._model, input_tokens, output_tokens)
            self._budget.finalize(
                reservation,
                max(1, math.ceil(cost_usd * 1_000_000)),
            )
            return analysis
        except Exception:
            self._budget.release(reservation)
            raise

    @staticmethod
    def _response_format() -> Dict[str, Any]:
        candidate = {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": ["expense", "income"]},
                "amount_cents": {"type": "integer", "minimum": 1},
                "description": {"type": "string"},
                "category": {"type": "string"},
                "occurred_on": {"type": "string"},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            },
            "required": [
                "kind",
                "amount_cents",
                "description",
                "category",
                "occurred_on",
                "confidence",
            ],
            "additionalProperties": False,
        }
        return {
            "type": "json_schema",
            "name": "financial_document_analysis",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {
                    "document_kind": {
                        "type": "string",
                        "enum": ["receipt", "statement", "unknown"],
                    },
                    "candidates": {
                        "type": "array",
                        "items": candidate,
                        "maxItems": 100,
                    },
                },
                "required": ["document_kind", "candidates"],
                "additionalProperties": False,
            },
        }


def _plain(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    return "".join(char for char in normalized if not unicodedata.combining(char))


def _money_cents(value: str) -> int:
    candidate = value.strip().replace("R$", "").replace(" ", "")
    if not candidate:
        raise FinancialDocumentError("statement amount is empty")
    if "," in candidate:
        candidate = candidate.replace(".", "").replace(",", ".")
    try:
        amount = Decimal(candidate)
    except InvalidOperation as exc:
        raise FinancialDocumentError("statement amount is invalid") from exc
    return int((amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _iso_date(value: str) -> str:
    candidate = value.strip()[:10]
    for pattern in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y%m%d"):
        try:
            return datetime.strptime(candidate, pattern).date().isoformat()
        except ValueError:
            continue
    raise FinancialDocumentError("statement date is invalid")


def _category(description: str, kind: str) -> str:
    if kind == "income":
        return "receita"
    text = _plain(description).lower()
    categories = (
        ("mercado", ("mercado", "supermercado", "hortifruti", "padaria")),
        ("transporte", ("posto", "uber", "99app", "combustivel", "estacionamento")),
        ("alimentacao", ("restaurante", "lanchonete", "ifood", "cafe")),
        ("saude", ("farmacia", "clinica", "hospital", "laboratorio")),
        ("moradia", ("condominio", "energia", "luz", "agua", "aluguel")),
        ("assinaturas", ("netflix", "spotify", "apple.com/bill", "google")),
    )
    for category, terms in categories:
        if any(term in text for term in terms):
            return category
    return "outros"


def _decode(payload: bytes) -> str:
    for encoding in ("utf-8-sig", "latin-1"):
        try:
            return payload.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise FinancialDocumentError("statement text encoding is unsupported")


def normalize_financial_analysis(value: Any) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise FinancialDocumentError("document analysis must be an object")
    document_kind = str(value.get("document_kind", "unknown"))
    if document_kind not in {"receipt", "statement", "unknown"}:
        raise FinancialDocumentError("document kind is invalid")
    raw_candidates = value.get("candidates")
    if not isinstance(raw_candidates, list) or len(raw_candidates) > 100:
        raise FinancialDocumentError("document candidates are invalid")
    candidates: List[Dict[str, Any]] = []
    for raw in raw_candidates:
        if not isinstance(raw, dict) or raw.get("kind") not in {"expense", "income"}:
            raise FinancialDocumentError("document candidate kind is invalid")
        amount = raw.get("amount_cents")
        if isinstance(amount, bool) or not isinstance(amount, int) or amount <= 0:
            raise FinancialDocumentError("document candidate amount is invalid")
        occurred_on = _iso_date(str(raw.get("occurred_on", "")))
        try:
            confidence = float(raw.get("confidence", 0))
        except (TypeError, ValueError) as exc:
            raise FinancialDocumentError(
                "document candidate confidence is invalid"
            ) from exc
        if confidence < 0 or confidence > 1:
            raise FinancialDocumentError("document candidate confidence is invalid")
        candidates.append(
            {
                "kind": raw["kind"],
                "amount_cents": amount,
                "description": str(raw.get("description", "")).strip()[:240],
                "category": str(raw.get("category", "outros")).strip()[:80] or "outros",
                "occurred_on": occurred_on,
                "confidence": round(confidence, 3),
            }
        )
    if not candidates:
        raise FinancialDocumentError("no financial entries were identified")
    return {"document_kind": document_kind, "candidates": candidates}


def _csv_candidates(payload: bytes) -> List[Dict[str, Any]]:
    text = _decode(payload)
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    rows = csv.DictReader(io.StringIO(text), dialect=dialect)
    if not rows.fieldnames:
        raise FinancialDocumentError("statement has no header")
    headers = {_plain(header).strip().lower(): header for header in rows.fieldnames}

    def column(*names: str) -> str:
        for name in names:
            if name in headers:
                return headers[name]
        raise FinancialDocumentError(f"statement column is missing: {names[0]}")

    date_key = column("data", "date", "data lancamento", "data do lancamento")
    description_key = column(
        "descricao", "description", "historico", "memo", "detalhes"
    )
    amount_key = column("valor", "amount", "value", "valor lancamento")
    type_key = next(
        (headers[name] for name in ("tipo", "type", "natureza") if name in headers),
        None,
    )
    candidates: List[Dict[str, Any]] = []
    for row in rows:
        if len(candidates) == 200:
            break
        if not any(str(value or "").strip() for value in row.values()):
            continue
        signed = _money_cents(str(row.get(amount_key) or ""))
        type_value = _plain(str(row.get(type_key) or "")).lower() if type_key else ""
        kind = "income" if signed > 0 else "expense"
        if type_value:
            if any(term in type_value for term in ("credito", "credit", "entrada")):
                kind = "income"
            elif any(term in type_value for term in ("debito", "debit", "saida")):
                kind = "expense"
        description = str(row.get(description_key) or "").strip()[:240]
        candidates.append(
            {
                "kind": kind,
                "amount_cents": abs(signed),
                "description": description,
                "category": _category(description, kind),
                "occurred_on": _iso_date(str(row.get(date_key) or "")),
                "confidence": 0.9,
            }
        )
    if not candidates:
        raise FinancialDocumentError("statement has no transactions")
    return candidates


def _ofx_value(block: str, tag: str) -> str:
    match = re.search(
        rf"<{tag}>\s*([^<\r\n]+)", block, flags=re.IGNORECASE | re.MULTILINE
    )
    return match.group(1).strip() if match else ""


def _ofx_candidates(payload: bytes) -> List[Dict[str, Any]]:
    text = _decode(payload)
    blocks = re.findall(
        r"<STMTTRN>(.*?)(?:</STMTTRN>|(?=<STMTTRN>|</BANKTRANLIST>))",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    candidates: List[Dict[str, Any]] = []
    for block in blocks[:200]:
        signed = _money_cents(_ofx_value(block, "TRNAMT"))
        transaction_type = _plain(_ofx_value(block, "TRNTYPE")).lower()
        kind = "income" if signed > 0 or transaction_type == "credit" else "expense"
        description = (
            _ofx_value(block, "MEMO")
            or _ofx_value(block, "NAME")
            or "Lançamento bancário"
        )[:240]
        raw_date = _ofx_value(block, "DTPOSTED")[:8]
        candidates.append(
            {
                "kind": kind,
                "amount_cents": abs(signed),
                "description": description,
                "category": _category(description, kind),
                "occurred_on": _iso_date(raw_date),
                "confidence": 0.98,
            }
        )
    if not candidates:
        raise FinancialDocumentError("OFX has no transactions")
    return candidates


def parse_statement(
    filename: str,
    content_type: str,
    payload: bytes,
) -> Dict[str, Any]:
    """Parse a bounded CSV/OFX statement without paid inference."""
    extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    normalized_type = content_type.lower().split(";", 1)[0].strip()
    if extension == "csv" or normalized_type in {"text/csv", "application/csv"}:
        candidates = _csv_candidates(payload)
    elif extension in {"ofx", "qfx"} or normalized_type in {
        "application/x-ofx",
        "application/ofx",
    }:
        candidates = _ofx_candidates(payload)
    else:
        raise FinancialDocumentError("document is not a CSV or OFX statement")
    return {"document_kind": "statement", "candidates": candidates}


__all__ = [
    "FinancialDocumentAnalyzer",
    "FinancialDocumentError",
    "FinancialDocumentStore",
    "OpenAIFinancialDocumentAnalyzer",
    "normalize_financial_analysis",
    "parse_statement",
]
