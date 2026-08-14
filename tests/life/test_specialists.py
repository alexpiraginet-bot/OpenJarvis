from openjarvis.life.specialists import (
    MAX_SPECIALISTS_PER_TURN,
    SPECIALIST_PROFILES,
    render_specialist_briefs,
    select_specialists,
    urgent_health_signal,
)


def test_each_specialist_has_context_rules_limits_and_evidence() -> None:
    assert set(SPECIALIST_PROFILES) == {
        "finance",
        "performance",
        "health",
        "nutrition",
        "executive",
        "family",
    }
    for profile in SPECIALIST_PROFILES.values():
        assert profile.mission
        assert profile.required_context
        assert profile.operating_rules
        assert profile.forbidden_actions
        assert profile.evidence_basis


def test_router_selects_only_relevant_bounded_specialists() -> None:
    selected = select_specialists(
        "Monte meu treino de corrida e adapte minha alimentação para a prova"
    )
    assert selected == ("performance", "nutrition")
    assert len(selected) <= MAX_SPECIALISTS_PER_TURN


def test_router_covers_all_six_specialists() -> None:
    examples = {
        "finance": "Quanto gastei no cartão?",
        "performance": "Como ajustar meu treino de corrida?",
        "health": "Quero organizar meus exames e medicamentos",
        "nutrition": "Planeje minhas refeições e hidratação",
        "executive": "Marque uma reunião no calendário",
        "family": "Lembre o aniversário da minha filha",
    }
    for profile_id, question in examples.items():
        assert profile_id in select_specialists(question)


def test_performance_specialist_routes_multimodal_training() -> None:
    for question in (
        "Monte minha semana de canoa",
        "Quero evoluir força e hipertrofia",
        "Ajuste meu pedal e natação",
    ):
        assert "performance" in select_specialists(question)


def test_router_uses_word_boundaries_and_has_stable_tie_breaking() -> None:
    assert select_specialists("Organize meu contato e escolha uma paisagem") == (
        "executive",
    )
    assert select_specialists("Quais países devo conhecer?") == ("executive",)
    assert select_specialists("Lembre de ligar para meu pai e minha mãe") == ("family",)
    question = "Revise meu orçamento e meu treino"
    assert select_specialists(question) == ("finance", "performance")
    assert select_specialists(question) == select_specialists(question)


def test_router_defaults_to_executive_for_general_request() -> None:
    assert select_specialists("O que merece minha atenção hoje?") == ("executive",)


def test_explicit_specialist_context_routes_an_ambiguous_question() -> None:
    rendered = render_specialist_briefs(
        "O que devo priorizar?", preferred_profile_id="finance"
    )

    assert "Diretor financeiro pessoal" in rendered
    assert "Chefe de gabinete" not in rendered


def test_render_includes_safety_limits_not_every_profile() -> None:
    rendered = render_specialist_briefs("Analise este exame e meu remédio")
    assert "Navegador de saúde" in rendered
    assert "diagnosticar, prescrever" in rendered
    assert "Diretor financeiro" not in rendered


def test_medical_and_financial_guardrails_are_explicit() -> None:
    finance = render_specialist_briefs("Revise meu saldo e faça um pagamento")
    assert "dinheiro é calculado em centavos" in finance
    assert "sem confirmação forte" in finance
    assert "inventar saldo" in finance

    health = render_specialist_briefs("Analise minha dor e este exame")
    assert "SAMU 192" in health
    assert "diagnosticar, prescrever" in health
    assert "trocar medicamento" in health

    performance = render_specialist_briefs("Monte meu treino com dor aguda")
    assert "treinar com dor aguda" in performance

    nutrition = render_specialist_briefs("Monte uma dieta terapêutica")
    assert "prescrever dieta terapêutica" in nutrition


def test_urgent_health_signal_is_conditional_safety_net() -> None:
    assert urgent_health_signal("Estou com dor súbita no peito agora") == "dor no peito"
    assert (
        urgent_health_signal("Minha fala está enrolada e meu rosto torto")
        == "possível sinal de AVC"
    )
    assert urgent_health_signal("Quero melhorar minha saúde") == ""
