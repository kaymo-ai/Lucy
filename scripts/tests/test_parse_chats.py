import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from parse_chats import ChatParser

FIXTURE = Path(__file__).parent / "fixtures" / "sample_chat.txt"


def parse_fixture():
    parser = ChatParser()
    parser.parse_file(FIXTURE, "test-group")
    return parser


def texts(parser):
    return [m["text"] for m in parser.messages]


def test_ltr_prefixed_media_message_is_its_own_message():
    """WhatsApp prefixes media lines with U+200E; they must not be swallowed."""
    parser = parse_fixture()
    senders_times = [(m["sender"], m["timestamp"].strftime("%H:%M:%S"))
                     for m in parser.messages if m["timestamp"]]
    assert ("Marcus", "15:28:34") in senders_times


def test_ltr_media_message_does_not_corrupt_previous_message():
    parser = parse_fixture()
    wrench = [t for t in texts(parser) if "wrench adapter" in t]
    assert len(wrench) == 1
    assert "attached" not in wrench[0]
    assert "PHOTO" not in wrench[0]


def test_message_containing_the_word_added_is_kept():
    """SYSTEM_PATTERNS must not match real prose containing 'added'."""
    parser = parse_fixture()
    assert any("propane to the shopping list" in t for t in texts(parser))


def test_message_ending_in_left_is_kept():
    parser = parse_fixture()
    assert any(t.strip() == "Turn left" for t in texts(parser))


def test_genuine_system_messages_are_dropped():
    parser = parse_fixture()
    joined = " ".join(texts(parser))
    assert "created this group" not in joined
    assert "end-to-end encrypted" not in joined
    assert "added Dean Wexler" not in joined


def test_short_messages_are_kept():
    parser = parse_fixture()
    assert any(t.strip() == "ok" for t in texts(parser))


def test_genuine_multiline_message_is_joined():
    parser = parse_fixture()
    placed = [t for t in texts(parser) if "3 frontages" in t]
    assert len(placed) == 1
    assert "basically square" in placed[0]


def test_media_reference_is_preserved():
    parser = parse_fixture()
    media = [m for m in parser.messages if m.get("media_ref")]
    refs = {m["media_ref"] for m in media}
    assert "00000231-PHOTO-2022-08-24-15-28-33.jpg" in refs


def test_media_placeholder_without_filename_is_dropped():
    """'image omitted' etc. carry the same U+200E structural marker as
    administrative system notices and no <attached:> token, so they are
    dropped like any other system notice rather than kept as content."""
    parser = parse_fixture()
    assert not any(t.strip() == "image omitted" for t in texts(parser))


def test_four_digit_year_produces_a_timestamp():
    """A 4-digit year must parse, not silently become None."""
    parser = parse_fixture()
    matches = [m for m in parser.messages if "four digit year" in m["text"]]
    assert len(matches) == 1
    assert matches[0]["timestamp"] is not None
    assert matches[0]["timestamp"].year == 2022


def test_captioned_media_placeholder_keeps_caption_drops_suffix():
    """WhatsApp appends '<type> omitted' to the end of a real caption when
    the export was taken without media. The caption is genuine user content
    and must survive; only the trailing placeholder token is export noise."""
    parser = parse_fixture()
    matches = [t for t in texts(parser) if t.startswith("Almost there")]
    assert len(matches) == 1
    assert matches[0] == "Almost there"
    assert not matches[0].endswith(" ")
    assert "omitted" not in matches[0]


def test_body_that_is_only_a_placeholder_is_still_dropped():
    """A body with no real caption -- just the placeholder token -- must
    still be dropped, even though the token is no longer body-leading."""
    parser = parse_fixture()
    dropped = [m for m in parser.messages
               if m["timestamp"] and m["timestamp"].strftime("%H:%M:%S") == "10:05:00"]
    assert dropped == []
    assert not any("omitted" in t.lower() for t in texts(parser))


def test_attached_message_is_unaffected_by_placeholder_stripping():
    """A message with a real <attached:> token must be unaffected by the
    new placeholder-stripping logic -- its caption and media_ref both
    survive intact."""
    parser = parse_fixture()
    matches = [m for m in parser.messages
               if m.get("media_ref") == "00000232-PHOTO-2022-08-24-15-30-00.jpg"]
    assert len(matches) == 1
    assert matches[0]["text"] == "Fixed the drainage pump"
