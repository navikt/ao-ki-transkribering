# Benchmark
3 modell(ar) × 7 konfigurasjonar = 21 kjøyringar
Ollama: http://localhost:11434

─── qwen3.6:35b ───────────────────────────────────────
ctx8192                  … 13.3s  (TTFT 8.39s, 20.0 tok/s)
ctx4096                  … 13.2s  (TTFT 8.36s, 19.5 tok/s)
ctx2048                  … 13.5s  (TTFT 8.12s, 21.8 tok/s)
ctx4096+max400           … 12.8s  (TTFT 7.83s, 17.5 tok/s)
ctx2048+max400           … 13.3s  (TTFT 8.38s, 20.5 tok/s)
ctx4096+temp0.1          … 12.6s  (TTFT 8.12s, 19.4 tok/s)
ctx2048+max400+t0.1      … 12.9s  (TTFT 7.81s, 21.4 tok/s)

Konfig                       TTFT    Total  Tokens   tok/s  Status
────────────────────────  ───────  ───────  ──────  ──────  ──────
ctx4096+temp0.1             8.12s    12.6s     244    19.4  ✅ 🏆
ctx4096+max400              7.83s    12.8s     224    17.5  ✅
ctx2048+max400+t0.1         7.81s    12.9s     275    21.4  ✅
ctx4096                     8.36s    13.2s     258    19.5  ✅
ctx8192                     8.39s    13.3s     266    20.0  ✅
ctx2048+max400              8.38s    13.3s     273    20.5  ✅
ctx2048                     8.12s    13.5s     294    21.8  ✅
─── qwen3:8b ──────────────────────────────────────────
ctx8192                  … 10.2s  (TTFT 4.67s, 24.0 tok/s)
ctx4096                  … 9.2s  (TTFT 2.80s, 30.6 tok/s)
ctx2048                  … 8.7s  (TTFT 2.80s, 29.9 tok/s)
ctx4096+max400           … 8.2s  (TTFT 2.79s, 29.5 tok/s)
ctx2048+max400           … 8.2s  (TTFT 2.79s, 29.8 tok/s)
ctx4096+temp0.1          … 8.2s  (TTFT 2.79s, 29.3 tok/s)
ctx2048+max400+t0.1      … 8.2s  (TTFT 2.80s, 29.1 tok/s)

Konfig                       TTFT    Total  Tokens   tok/s  Status
────────────────────────  ───────  ───────  ──────  ──────  ──────
ctx4096+max400              2.79s     8.2s     243    29.5  ✅ 🏆
ctx2048+max400              2.79s     8.2s     245    29.8  ✅
ctx4096+temp0.1             2.79s     8.2s     239    29.3  ✅
ctx2048+max400+t0.1         2.80s     8.2s     239    29.1  ✅
ctx2048                     2.80s     8.7s     260    29.9  ✅
ctx4096                     2.80s     9.2s     280    30.6  ✅
ctx8192                     4.67s    10.2s     245    24.0  ✅
─── qwen3.5:9b ────────────────────────────────────────
ctx8192                  … 12.2s  (TTFT 4.92s, 21.5 tok/s)
ctx4096                  … 10.3s  (TTFT 3.92s, 22.9 tok/s)
ctx2048                  … 9.9s  (TTFT 3.92s, 22.3 tok/s)
ctx4096+max400           … 10.8s  (TTFT 3.93s, 23.7 tok/s)
ctx2048+max400           … 10.5s  (TTFT 3.92s, 23.3 tok/s)
ctx4096+temp0.1          … 9.7s  (TTFT 3.93s, 22.1 tok/s)
ctx2048+max400+t0.1      … 8.7s  (TTFT 3.93s, 20.2 tok/s)

Konfig                       TTFT    Total  Tokens   tok/s  Status
────────────────────────  ───────  ───────  ──────  ──────  ──────
ctx2048+max400+t0.1         3.93s     8.7s     176    20.2  ✅ 🏆
ctx4096+temp0.1             3.93s     9.7s     215    22.1  ✅
ctx2048                     3.92s     9.9s     220    22.3  ✅
ctx4096                     3.92s    10.3s     236    22.9  ✅
ctx2048+max400              3.92s    10.5s     245    23.3  ✅
ctx4096+max400              3.93s    10.8s     257    23.7  ✅
ctx8192                     4.92s    12.2s     262    21.5  ✅

═════════════════════════════════════════════════════════════════
Samandrag — beste konfig per modell
═════════════════════════════════════════════════════════════════
qwen3:8b              ctx4096+max400            8.2s  29.5 tok/s
qwen3.5:9b            ctx2048+max400+t0.1       8.7s  20.2 tok/s
qwen3.6:35b           ctx4096+temp0.1           12.6s  19.4 tok/s

🏆  Raskast totalt: qwen3:8b med «ctx4096+max400» — 8.2s