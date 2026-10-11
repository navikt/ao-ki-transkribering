# Norwegian Conversational Speech Test Data

Primary sources checked: 2026-10-11. Existing `testdata/transcriptions` are known inaccurate and must not be used as references; they were not read for this research.

## Candidate: NB Samtale

NB Samtale contains real conversational audio from podcasts and National Library live events: 24 hours, 69 speakers and 12,080 segments, with Bokmal and Nynorsk transcripts. The catalogue lists CC0 licensing and an [official 3.8 GB ZIP](https://www.nb.no/sbfil/taledata/nb_samtale.zip). This permits reuse and redistribution. [National Library catalogue](https://www.nb.no/sprakbanken/ressurskatalog/oai-nb-no-sbr-85/)

The annotations began as ASR output, then a human transcriber corrected the segments and text. A second person reviewed all manual transcriptions, followed by spelling and character checks. These are human-corrected references, not independently handwritten transcripts from scratch. The release provides individual 16 kHz, 16-bit WAV segments: most are under 20 seconds and none exceed 30 seconds, rather than whole podcast episodes. [Corpus documentation, pp. 3-5](https://www.nb.no/sbfil/taledata/NB_Samtale_About_the_corpus.pdf)

Metadata includes source-file IDs, start/end timestamps, segment order, duration, speaker IDs and previous/next overlap flags. Speaker IDs remain consistent across source files. The Hugging Face release exposes `annotations`, `verbatim` and `orthographic` text variants. These represent Norwegian speech and its written normalization, not translations into another language; select and document one variant before scoring. [Official dataset card](https://huggingface.co/datasets/Sprakbanken/nb_samtale)

Human review does not promise perfect accuracy. Unintelligible speech, unintelligible overlap and foreign-language passages can receive labels instead of word-for-word text. [Corpus documentation, p. 7](https://www.nb.no/sbfil/taledata/NB_Samtale_About_the_corpus.pdf)

## Proposed Test Subset

Select approximately 20-30 clips from the official `test` split, spanning podcasts/live events, dialects, both written standards and some overlap. Manually spot-check each selected transcript against its audio; record IDs, provenance, transcript variant and any adjudicated corrections. This is a proposal, not completed audio validation.

The split is not speaker-disjoint. [Dataset card: Data Splits](https://huggingface.co/datasets/Sprakbanken/nb_samtale#data-splits) An official test split alone does not establish that a particular model never encountered these recordings. Model-training overlap remains a separate check; no contamination evidence is established here.

Limitation: presegmented clips and speaker metadata do not establish a complete, continuous full-source diarisation benchmark. Evaluate episode-level speaker turns and overlap separately using continuous recordings and independently checked time-aligned references.

The initial research did not download data or change application code; the subsequent approved trial is recorded below.

## Local Trial, 2026-10-11

Following approval, the official Hugging Face test archives were downloaded and
their published SHA-256 checksums verified. Dataset revision:
`1c7028101858b854d3d568761778d8f63bb7e33f`.
The cached subset contains 30 non-overlapping podcast clips (328.744 seconds),
16 speakers and all five dialect groups: 24 Bokmal and six Nynorsk clips.
The corpus references were used without independently listening to validate
them. Existing `testdata/transcriptions` were not used.

CPU results, four Torch threads, orthographic references with case/punctuation
normalization; elapsed inference excludes model loading:

| Backend | Written norm | Clips | Reference words | WER | Deletion rate | Inference |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| NB-Whisper Large | Bokmal | 24 | 802 | 25.7% | 20.6% | 349.99s |
| NVIDIA Nemotron 3.5 ASR 0.6B | Bokmal | 24 | 802 | 63.8% | 26.1% | 16.64s |
| NB-Whisper Large | Nynorsk challenge | 6 | 170 | 42.4% | 20.6% | 67.61s |
| NVIDIA Nemotron 3.5 ASR 0.6B | Nynorsk challenge | 6 | 170 | 73.5% | 28.2% | 3.40s |

Whisper revision: `8c6249fdeeb4dcd05e5735a4c39640607eb6e4ac`.
Nemotron revision: `ea30d66debe3740a08b573244286791d423d6b3e`.
Runtime: Torch 2.13.0, Transformers 5.19.0.dev0 (isolated development revision
`55c3cc9f03cd4fbf2d27d382dcc8fcc544631f8e`). Whisper used the current raw batch
settings: language `no`, beam size 5, word timestamps, 30-second chunks and
5/2-second stride. Nemotron used full-clip generation with `nb-NO`.
Neither model was configured for Nynorsk-targeted output in this trial.

Interpretation: NB-Whisper matched these references substantially better in
this configuration. Some deletions are repetitions or conversational filler,
not necessarily loss of substantive information. The stock NVIDIA model was
about 21 times faster on the Bokmal clips, but made many substitutions as well
as deletions. Its development Transformers implementation has not been
cross-validated against native NeMo; do that before attributing this result
solely to the model or deciding it needs fine-tuning. No model was deployed or
application configuration changed. This is not a streaming or GPU benchmark.

Manifest: `.cache/nb-samtale/podcast-test.json`.
Full per-clip predictions, references and counts:
`.cache/benchmarks/nb-samtale-cpu.json`.
Preparation and rerun commands are documented in the README.
