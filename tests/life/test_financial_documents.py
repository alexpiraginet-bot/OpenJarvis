"""Financial attachment extraction without mutating the ledger."""

from types import SimpleNamespace

import pytest

from openjarvis.life.financial_documents import (
    FinancialDocumentError,
    FinancialDocumentStore,
    OpenAIFinancialDocumentAnalyzer,
    normalize_financial_analysis,
    parse_statement,
)


class _BudgetSpy:
    def __init__(self) -> None:
        self.reserved = []
        self.finalized = []
        self.released = []

    def reserve(self, user_id, model, max_microusd):
        self.reserved.append((user_id, model, max_microusd))
        return "reservation-1"

    def finalize(self, reservation, actual_microusd):
        self.finalized.append((reservation, actual_microusd))

    def release(self, reservation):
        self.released.append(reservation)


class _ResponsesSpy:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.calls = []
        self.error = error

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return SimpleNamespace(
            output_text=(
                '{"document_kind":"receipt","candidates":['
                '{"kind":"expense","amount_cents":8750,'
                '"description":"POSTO AVENIDA","category":"transporte",'
                '"occurred_on":"2026-08-14","confidence":0.96}]}'
            ),
            usage=SimpleNamespace(input_tokens=800, output_tokens=120),
        )


def test_openai_analyzer_uses_structured_image_input_and_budget() -> None:
    budget = _BudgetSpy()
    responses = _ResponsesSpy()
    analyzer = OpenAIFinancialDocumentAnalyzer(
        budget,
        client=SimpleNamespace(responses=responses),
    )

    result = analyzer.analyze(
        user_id="user-1",
        filename="comprovante.jpg",
        content_type="image/jpeg",
        payload=b"image-bytes",
    )

    call = responses.calls[0]
    image = call["input"][0]["content"][1]
    assert image["type"] == "input_image"
    assert image["image_url"].startswith("data:image/jpeg;base64,")
    assert call["text"]["format"]["strict"] is True
    assert budget.reserved == [("user-1", "gpt-5-mini", 500_000)]
    assert budget.finalized[0][0] == "reservation-1"
    assert budget.released == []
    assert result["candidates"][0]["amount_cents"] == 8750


def test_openai_analyzer_releases_budget_when_provider_fails() -> None:
    budget = _BudgetSpy()
    responses = _ResponsesSpy(error=RuntimeError("provider unavailable"))
    analyzer = OpenAIFinancialDocumentAnalyzer(
        budget,
        client=SimpleNamespace(responses=responses),
    )

    with pytest.raises(RuntimeError, match="provider unavailable"):
        analyzer.analyze(
            user_id="user-1",
            filename="comprovante.pdf",
            content_type="application/pdf",
            payload=b"pdf-bytes",
        )

    assert budget.finalized == []
    assert budget.released == ["reservation-1"]


def test_document_reservation_prevents_duplicate_paid_analysis(life, user) -> None:
    store = FinancialDocumentStore(life)

    first, first_owned = store.reserve(
        user.id,
        filename="comprovante.jpg",
        content_type="image/jpeg",
        sha256="a" * 64,
    )
    replay, replay_owned = store.reserve(
        user.id,
        filename="copia.jpg",
        content_type="image/jpeg",
        sha256="a" * 64,
    )

    assert first_owned is True
    assert replay_owned is False
    assert replay["id"] == first["id"]
    assert replay["status"] == "analyzing"

    store.release_reservation(user.id, first["id"])
    retried, retry_owned = store.reserve(
        user.id,
        filename="comprovante.jpg",
        content_type="image/jpeg",
        sha256="a" * 64,
    )
    assert retry_owned is True
    assert retried["id"] != first["id"]


def test_invalid_confidence_is_a_domain_error() -> None:
    with pytest.raises(FinancialDocumentError, match="confidence"):
        normalize_financial_analysis(
            {
                "document_kind": "receipt",
                "candidates": [
                    {
                        "kind": "expense",
                        "amount_cents": 100,
                        "description": "Teste",
                        "category": "outros",
                        "occurred_on": "2026-08-14",
                        "confidence": "not-a-number",
                    }
                ],
            }
        )


def test_csv_statement_parses_pt_br_money_and_direction() -> None:
    payload = (
        "Data;Descrição;Valor\n"
        "13/08/2026;POSTO AVENIDA;-245,90\n"
        "14/08/2026;PIX CLIENTE;1.500,00\n"
    ).encode()

    result = parse_statement("extrato.csv", "text/csv", payload)

    assert result["document_kind"] == "statement"
    assert result["candidates"] == [
        {
            "kind": "expense",
            "amount_cents": 24590,
            "description": "POSTO AVENIDA",
            "category": "transporte",
            "occurred_on": "2026-08-13",
            "confidence": 0.9,
        },
        {
            "kind": "income",
            "amount_cents": 150000,
            "description": "PIX CLIENTE",
            "category": "receita",
            "occurred_on": "2026-08-14",
            "confidence": 0.9,
        },
    ]


def test_ofx_statement_parses_transactions() -> None:
    payload = b"""
        <OFX><BANKMSGSRSV1><STMTTRNRS><STMTRS><BANKTRANLIST>
        <STMTTRN><TRNTYPE>DEBIT<DTPOSTED>20260812120000[-3:BRT]
        <TRNAMT>-89.50<MEMO>SUPERMERCADO CENTRAL</STMTTRN>
        </BANKTRANLIST></STMTRS></STMTTRNRS></BANKMSGSRSV1></OFX>
    """

    result = parse_statement("conta.ofx", "application/x-ofx", payload)

    assert result["candidates"][0]["kind"] == "expense"
    assert result["candidates"][0]["amount_cents"] == 8950
    assert result["candidates"][0]["category"] == "mercado"
    assert result["candidates"][0]["occurred_on"] == "2026-08-12"
