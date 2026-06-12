SAMPLE_RATE        = 16000
FRAME_SAMPLES      = 160      # 10 ms per frame ved 16 kHz
ENERGI_TERSKEL     = 0.01     # RMS-terskel for tale (0–1 float32)
STILLHET_TERSKEL_S = 0.7      # sekunder stillhet → flush
MAKS_BUFFER_S      = 25.0     # sekunder maks buffer
MIN_TALE_S         = 0.3      # minimum tale for å sende til Whisper
