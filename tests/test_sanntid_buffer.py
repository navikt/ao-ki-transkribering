import numpy as np
import pytest

from worker.transkribering.sanntid import LydChunk, SegmentSamler, VadBuffer
from worker.transkribering.konstanter import SAMPLE_RATE


def test_large_packet_preserves_every_sample_across_multiple_flushes():
    pcm = np.linspace(0.1, 0.5, 22 * SAMPLE_RATE, dtype="float32")
    buf = VadBuffer()
    chunks = buf.legg_til(pcm)
    chunks.append(buf.flush_alt())
    assert [chunk.start_s for chunk in chunks] == [0, 9, 18]
    assert [len(chunk.pcm) / SAMPLE_RATE for chunk in chunks] == [10, 10, 4]
    reconstructed = np.concatenate([
        chunk.pcm[int(chunk.overlapp_s * SAMPLE_RATE):] for chunk in chunks
    ])
    np.testing.assert_array_equal(reconstructed, pcm)


def test_exact_cut_does_not_emit_overlap_only_chunk_on_stop():
    buf = VadBuffer()
    assert len(buf.legg_til(np.full(10 * SAMPLE_RATE, 0.1, dtype="float32"))) == 1
    assert buf.flush_alt() is None


def test_packet_tail_and_short_final_packet_are_preserved():
    pcm = np.full(10 * SAMPLE_RATE + 127, 0.1, dtype="float32")
    buf = VadBuffer()
    chunks = buf.legg_til(pcm)
    final = buf.flush_alt()
    assert len(chunks) == 1
    assert len(final.pcm) == SAMPLE_RATE + 127


def test_pause_flush_has_no_overlap_and_preserves_following_speech():
    buf = VadBuffer()
    pcm = np.concatenate([
        np.full(SAMPLE_RATE, 0.1, dtype="float32"),
        np.zeros(int(1.5 * SAMPLE_RATE), dtype="float32"),
        np.full(SAMPLE_RATE, 0.2, dtype="float32"),
    ])
    chunks = buf.legg_til(pcm)
    final = buf.flush_alt()
    assert len(chunks) == 1
    assert final.overlapp_s == 0
    assert final.start_s == 2.5
    np.testing.assert_array_equal(np.concatenate([chunks[0].pcm, final.pcm]), pcm)


def test_long_silence_does_not_create_chunks():
    buf = VadBuffer()
    assert buf.legg_til(np.zeros(30 * SAMPLE_RATE, dtype="float32")) == []
    assert buf.flush_alt() is None


def test_zero_overlap_does_not_retain_the_entire_previous_chunk(monkeypatch):
    from worker.transkribering import sanntid

    monkeypatch.setattr(sanntid, "OVERLAPP_S", 0)
    buf = VadBuffer()
    buf.legg_til(np.full(12 * SAMPLE_RATE, 0.1, dtype="float32"))
    final = buf.flush_alt()
    assert len(final.pcm) == 2 * SAMPLE_RATE
    assert final.start_s == 10


def result(texts):
    return {"tekst": " ".join(texts), "segmenter": [
        {"tekst": text, "start": 0.0, "slutt": 3.0, "taler": f"SPEAKER_0{i}"}
        for i, text in enumerate(texts)
    ]}


def chunk(start, overlap=0):
    return LydChunk(np.zeros(10 * SAMPLE_RATE), start, overlap)


def test_deduplication_across_speaker_segments_preserves_new_text_and_offsets():
    samler = SegmentSamler()
    samler.legg_til(result(["Dette er neste steg."]), chunk(0))
    output = samler.legg_til(result(["Neste", "steg. Vi avtaler mandag."]), chunk(9, 1))
    assert output["tekst"] == "Vi avtaler mandag."
    assert len(output["segmenter"]) == 1
    assert output["segmenter"][0]["taler"] == "SPEAKER_01"
    assert output["segmenter"][0]["start"] == 10.0


@pytest.mark.parametrize("start,overlap", [(10, 0), (20, 1)])
def test_repetition_without_shared_audio_is_preserved(start, overlap):
    samler = SegmentSamler()
    samler.legg_til(result(["Takk for sist."]), chunk(0))
    output = samler.legg_til(result(["Takk for sist. Hei."]), chunk(start, overlap))
    assert output["tekst"] == "Takk for sist. Hei."


def test_single_repeated_word_is_preserved():
    samler = SegmentSamler()
    samler.legg_til(result(["Ja."]), chunk(0))
    assert samler.legg_til(result(["Ja, det stemmer."]), chunk(9, 1))["tekst"] == "Ja, det stemmer."


def test_overlap_only_result_is_not_emitted_twice():
    samler = SegmentSamler()
    samler.legg_til(result(["Dette stemmer."]), chunk(0))
    assert samler.legg_til(result(["Dette stemmer."]), chunk(9, 1)) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("remote", [False, True])
async def test_websocket_dispatches_all_chunks_and_deduplicates_before_sending(monkeypatch, remote):
    from frontend.routes import sanntid as routes
    from shared.contracts.transcription import TranskripsjonSvar

    outputs = iter([
        result(["Dette er neste steg."]),
        result(["Neste steg. Vi avtaler mandag."]),
        result(["Avtaler mandag. Takk for i dag."]),
    ])
    audio_lengths = []

    def transcribe(pcm, *args):
        audio_lengths.append(len(pcm) / SAMPLE_RATE)
        return next(outputs), None

    async def transcribe_remote(pcm):
        output, _ = transcribe(pcm)
        return TranskripsjonSvar.model_validate(output)

    class Socket:
        def __init__(self):
            self.messages = iter([
                {"bytes": np.full(22 * SAMPLE_RATE, 0.1, dtype="<f4").tobytes()},
                {"text": '{"type":"stopp"}'},
            ])
            self.sent = []

        async def receive(self):
            return next(self.messages)

        async def send_json(self, message):
            self.sent.append(message)

        async def close(self):
            self.closed = True

    monkeypatch.setattr(routes, "hent_sanntidsmodell", lambda: None)
    monkeypatch.setattr(routes, "transkriber_pcm", transcribe)
    monkeypatch.setattr(routes, "transkriber_remote_pcm", transcribe_remote)
    socket = Socket()
    handler = routes._sanntid_remote if remote else routes._sanntid_lokal
    await handler(socket)
    assert socket.closed
    assert audio_lengths == [10, 10, 4]
    assert [message["tekst"] for message in socket.sent] == [
        "Dette er neste steg.", "Vi avtaler mandag.", "Takk for i dag.",
    ]
