import sys
import struct
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from embed_corpus import pack_vector, unpack_vector, EMBED_DIM


def test_pack_produces_float32_little_endian():
    packed = pack_vector([1.0, -2.5, 0.0])
    assert packed == struct.pack('<3f', 1.0, -2.5, 0.0)


def test_round_trip_preserves_values():
    original = [0.125, -0.5, 1.0, 0.0]
    assert unpack_vector(pack_vector(original)) == original


def test_packed_length_matches_dimension():
    packed = pack_vector([0.0] * EMBED_DIM)
    assert len(packed) == EMBED_DIM * 4
