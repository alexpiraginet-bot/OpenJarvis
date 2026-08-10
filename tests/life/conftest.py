"""Shared fixtures: a temporary Life database with two isolated clients."""

from __future__ import annotations

import pytest

from openjarvis.life import open_life


@pytest.fixture()
def life(tmp_path):
    """A Life context backed by a temporary database."""
    ctx = open_life(tmp_path / "life.db")
    yield ctx
    ctx.close()


@pytest.fixture()
def user(life):
    """A registered client."""
    return life.users.create_user(
        "alex@exemplo.com", "senha-forte-123", name="Alex Silva"
    )


@pytest.fixture()
def other_user(life):
    """A second client, for proving tenant isolation."""
    return life.users.create_user(
        "bruna@exemplo.com", "outra-senha-456", name="Bruna Costa"
    )
