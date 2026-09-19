import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

MODEL = Path(__file__).resolve().parent.parent / "models" / "embeddinggemma-300M-Q8_0.gguf"

pytestmark = pytest.mark.skipif(
    not MODEL.exists(), reason="embedding model not downloaded"
)


def cosine(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    return dot / (na * nb)


@pytest.fixture(scope="module")
def llm():
    from embed_corpus import load_model
    return load_model(str(MODEL))


def test_embedding_has_expected_dimension(llm):
    from embed_corpus import EMBED_DIM
    assert len(llm.embed("how much propane does the burner need")) == EMBED_DIM


def test_identical_text_embeds_identically(llm):
    a = llm.embed("the container delivery is booked for Thursday")
    b = llm.embed("the container delivery is booked for Thursday")
    assert cosine(a, b) > 0.999


def test_related_text_scores_higher_than_unrelated(llm):
    """The actual point of an embedding. A model that returns constant or
    near-random vectors passes every shape check and fails this one."""
    fuel_a = llm.embed("how much propane does the pancake burner need")
    fuel_b = llm.embed("what quantity of fuel for the camp stove")
    music = llm.embed("the DJ played deep house until sunrise")

    assert cosine(fuel_a, fuel_b) > cosine(fuel_a, music)


def test_vectors_are_not_degenerate(llm):
    """Guards against an all-zero or constant vector, which would make every
    cosine similarity identical and retrieval uniformly useless."""
    v = llm.embed("MOOP sweep starts at 9am on the Esplanade")
    assert max(v) != min(v)
    assert any(abs(x) > 1e-6 for x in v)
