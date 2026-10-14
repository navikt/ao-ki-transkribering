"""Opt-in cloud runtime comparison using the same NB-Whisper weights in FP16."""

import logging
import os
import tempfile
import time
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)


class CTranslate2Transkriberer:
    def __init__(self):
        from faster_whisper import WhisperModel

        source = Path(os.getenv("WHISPER_MODELL", "/models/nb-whisper-large"))
        target = Path(os.getenv("WHISPER_CT2_MODELL", "/models/nb-whisper-large-ct2-fp16"))
        if not (target / "model.bin").is_file():
            from ctranslate2.converters import TransformersConverter

            target.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(dir=target.parent, prefix="ct2-conversion-") as directory:
                output = Path(directory) / "model"
                logger.warning("Converting mounted NB-Whisper weights to CTranslate2 FP16")
                converter = TransformersConverter(str(source), load_as_float16=True,
                                                  copy_files=["tokenizer.json", "preprocessor_config.json"])
                converter.convert(str(output), quantization="float16")
                output.rename(target)
        self.enhet = "cuda"
        self.inference_concurrency = int(os.getenv("SPEECH_INFERENCE_CONCURRENCY", "2"))
        if not 1 <= self.inference_concurrency <= 8:
            raise ValueError("SPEECH_INFERENCE_CONCURRENCY must be between 1 and 8")
        self.model = WhisperModel(str(target), device="cuda", compute_type="float16",
                                  num_workers=self.inference_concurrency, cpu_threads=2, local_files_only=True)
        if self.model.feature_extractor.mel_filters.shape[0] != self.model.model.n_mels:
            raise RuntimeError("Converted Whisper preprocessor does not match model mel bins")

    def transkriber(self, pcm):
        if not len(pcm) or not np.any(pcm):
            return {"text": "", "chunks": []}
        started = time.monotonic()
        segments, _ = self.model.transcribe(
            pcm, language="no", task="transcribe", beam_size=5, temperature=0,
            word_timestamps=True, vad_filter=False, no_speech_threshold=None,
            log_prob_threshold=None, compression_ratio_threshold=None,
            condition_on_previous_text=False,
        )
        texts, chunks = [], []
        duration = len(pcm) / 16000
        for segment in segments:
            texts.append(segment.text)
            if segment.words:
                chunks.extend({"text": word.word, "timestamp": (max(0, min(word.start, duration)),
                                                                max(0, min(word.end, duration)))}
                              for word in segment.words)
            elif segment.text.strip():
                chunks.append({"text": segment.text, "timestamp": (max(0, min(segment.start, duration)),
                                                                      max(0, min(segment.end, duration)))})
        logger.warning("ct2_inference seconds=%.3f audio_seconds=%.3f", time.monotonic() - started, duration)
        return {"text": "".join(texts), "chunks": chunks}
