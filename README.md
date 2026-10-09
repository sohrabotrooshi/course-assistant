# MBAX 6418 — Assignment 2: Course Assistant (RAG Q&A + Practice Quizzes)

A course assistant for MBAX 6418 built from the course's own materials —
the five lecture decks (Weeks 2–6) **plus PDF course files** (the Hermes
installation guide and the assignment handouts from Canvas). It **answers
questions** about the materials with exact slide/page citations and the
original diagrams as visual evidence, and **generates practice quizzes** —
42 multiple-choice questions in the shipped bank, plus an in-page generator
that builds a fresh quiz from whatever material (and whatever topic) a
student selects.

Everything runs through the class LLM endpoint
(`cyankiwi/Qwen3.6-35B-A3B-AWQ-4bit` served via vLLM at
`http://dobolyi.com:9001/v1`, thinking disabled, temperature 0). Retrieval
is pure stdlib Python: **BM25 keyword search + an LLM reranking pass**,
because the class endpoint serves chat completions only (verified: `POST
/v1/embeddings` returns 404). Students pick the documents and an optional
topic; retrieval, answers, and quiz generation all respect that selection.

## What the app accepts

| Format | Ingestion | Text | Images preserved |
|---|---|---|---|
| `.pptx` (lecture decks) | stdlib zip/XML parsing (`ingest_slides.py`) | slide text, per slide | yes — every image embedded in a slide is extracted |
| `.pdf` (syllabus, handouts, guides) | `pdf_tool.swift` (macOS PDFKit, built once) | per-page text layer (+ PDF outline as section labels) | yes — every page is rendered to an image |
| anything else | — | convert it to PDF or PPTX first | — |

The corpus keeps **both** the text and the original imagery: slide diagrams
are extracted from the decks (recompressed to ~1000px JPEGs so the repo
stays small — originals remain in the source files), and PDF pages are
rendered to page images with the exact layout. Rendered page/slide images
and extracted diagrams are shown as **visual evidence** under every answer
and quiz-feedback block, zoomable via a lightbox. Two honest limits:
images over 7MB are skipped as evidence (still in the source deck), and a
scanned PDF with no text layer is *browsable* (page images) but not
*searchable* — no OCR. To add materials (e.g. the syllabus): drop the PDF
into `data/course_files/` (or anywhere) and re-run `make_corpus.py` — new
documents appear in the chat selector and quiz generator automatically.

**The two surfaces** (both open straight from the repo — the bank works
offline; chat and quiz *generation* need the endpoint and your key, which
you paste once and stays in your browser):

- `course_chat.html` — chat with material checkboxes + topic filter; answers
  cite `[Week N, slide X]` / `[Document, page N]` and show the source
  excerpt + original images.
- `quiz_practice.html` — the 42-question bank (offline) **and** an in-page
  generator: pick documents + optional topic + count, and it writes,
  validates, and fixes one question per sampled slide/page, with a **fixed
  answer key** saved to your browser. Solutions stay hidden until you answer
  or click "Reveal solution".

Plus a CLI (`scripts/assist.py`) running the same pipeline.

**Source of the materials:** the course's own
`MBAX 6418 - Week {2..6} - *.pptx` decks and Canvas PDFs, converted to a
chunked corpus (**164 chunks across 7 documents**, ~7,600 words) committed
as `data/course_chunks.json`, with 194 evidence images in `data/media/` so
the repo is self-contained.

---

## Results at a glance

| Metric | Value |
|---|---|
| Corpus | **164 chunks · 7 documents** (5 decks + 2 PDFs) · 194 evidence images |
| Retrieval — BM25 finds the right source in top 8 | **18/18** (MRR@8 = 0.824) |
| Retrieval — reranking keeps it in top 4 | **18/18** (incl. 3 PDF-target cases) |
| Quiz bank | **42 questions** (6 per document), 0 dropped invalid |
| Quiz — answer-position distribution | A:15, B:9, C:6, D:12 (model bias removed, Q3) |
| Browser verification (harness) | 19/19 automated checks PASS (see Q4 / shot.mjs) |
| Out-of-scope behavior | refuses cleanly ("The course materials don't cover that.") |

> Retrieval numbers from `results/retrieval_metrics.json` (18 questions:
> 15 slide-targeted + 3 PDF-targeted), quiz numbers from
> `results/quiz_questions.json`, chat behavior from `results/qa_samples.json`
> and the screenshots below. The eval questions were written from the
> material with a known target source, so 18/18 is a sanity bound, not a
> claim about harder queries (see Q1).

**The chat surface** — a PDF question answered with page citations and the
original page render (with its diagram) opened as evidence:

![Chat — PDF answer with page citations and image evidence](screenshots/chat_answer.png)

**The quiz surface** — one question checked (verdict, rationale, source
excerpt) and one skipped via "Reveal solution", with the source slide's
own diagram as evidence:

![Quiz — checked answer with rationale and slide diagram evidence](screenshots/quiz_answered.png)

![Quiz — overview: bank tabs, stats strip, document groups](screenshots/quiz_initial.png)

**Quiz generated in the browser** — a fresh 4-question quiz built from
Week 5 only via "+ New quiz", with the fixed-key notice:

![Quiz — generated from the selected material](screenshots/quiz_generated.png)

**Evidence lightbox** — clicking any source image zooms it:

![Quiz — evidence lightbox](screenshots/quiz_lightbox.png)

![Chat — initial state with the materials selection bar](screenshots/chat_initial.png)

---

## Q1 — Why BM25 + LLM reranking instead of an embeddings pipeline?

The class endpoint serves **one chat model and no embeddings**
(`/v1/embeddings` → 404). Retrieval is therefore:

1. **Lexical BM25** (pure stdlib) over slide/page chunks — one chunk per
   slide or PDF page, preserving doc, page/slide, and section.
2. **An LLM reranking pass** over BM25's top candidates — the model sees
   the question plus each candidate's id/location and returns the most
   relevant ids. This stands in for the vector leg of the course's hybrid
   RAG pipeline (Week 5, slide 18) given what the endpoint offers.

**Evaluation** (`scripts/eval_retrieval.py`, saved in
`results/retrieval_metrics.json`): 18 questions — 15 slide-targeted, **3
PDF-targeted**. BM25 puts the target source in its top-8 **18/18**
(MRR@8 0.824) and reranking keeps it in the top-4 **18/18**. Example PDF
case: *"How do you install Hermes Agent on macOS?"* → the target page is
BM25 rank 1 and the chat answer cites *Hermes Agent Installation Guide
2026 v1, page 1* with the page render as evidence. The rerank's payoff is
pruning lexical noise: for *"What is retrieval augmented generation
(RAG)?"* the target (`w5-s10`) sits at BM25 rank 3 behind the "Why use
RAG?" and use-case slides — rerank promotes it into the top context.

**Honest limits.** The corpus is small and one-chunk-per-slide, so keyword
overlap is usually enough; the eval questions were authored from the
material (optimistic by design). Failure modes observed: (1) **rerank
volatility** — re-running a question can return a different top-4 (one run
of the Hermes *verification* question missed the page that actually covers
it, and the model honestly said the materials "don't cover that" rather
than inventing — grounded, but wrong); (2) questions with no lexical
overlap (*"what is the best restaurant in Boulder"*) retrieve noise chunks
that the answer step then refuses to use; (3) keyword search misses
paraphrases that embeddings would catch. A production deployment would add
an embedding model to the endpoint and go hybrid, as the slides describe.

## Q2 — How do I know the answers are grounded?

The prompt contract (`prompts/assistant_prompt.md`) forces: answer only
from supplied context; cite every claim as `[<loc>]` (`[Week 2, slide 15]`,
`[Hermes Agent Installation Guide 2026 v1, page 3]`); refuse when not
covered; flag thin coverage; never quote chunk ids. Real runs (verbatim
from `results/qa_samples.json`, saved with `--save`):

> **Q:** What is RAG and why would you use it instead of fine-tuning?
> **A:** **RAG (Retrieval-Augmented Generation)** is a technique that
> optimizes context by supplying relevant information from external sources
> when the model responds [Week 5, slide 13] … *access to external data*
> (recent events or private documents, which fine-tuning cannot easily
> provide without retraining [Week 5, slide 11]) … *grounding and
> verification* ([Week 5, slide 11]) … *flexibility* (draw on codebases,
> course materials, and other sources [Week 5, slide 12]). In contrast,
> fine-tuning adapts the model itself through additional training
> [Week 5, slide 13].

Every claim checks out verbatim against the cited slides (e.g. w5-s11:
*"RAG gives a model access to information beyond its training data,
including more recent or private information"*). The interesting part: in
another run, reranking dropped `w5-s13` from context — the model then said
the materials "don't cover the specific tradeoffs". Both behaviors are
grounded; neither invented the comparison.

> **Q:** What are the steps to install Hermes Agent on a Mac?
> **A:** a step-by-step answer citing pages 1–3 of *Hermes Agent
> Installation Guide 2026 v1*, with the page render shown as evidence
> (full text in `results/qa_samples.json`).

> **Q:** What is the best restaurant in Boulder for sushi?
> **A:** The course materials don't cover that.

One thing the prompt does **not** fix: the model will happily cite a chunk
that only *loosely* supports a claim if the text is plausible (standard
RAG weakness). The Q1 eval bounds the retrieval side; 4/4 sampled answers
were faithful to their cited sources, and one honest refusal was observed
(a case where retrieval dropped the right page — the answer said "don't
cover" instead of inventing).

## Q3 — How are quizzes made, selected, and kept fixed?

**The shipped bank** (`results/quiz_questions.json`, seed `20261009`,
temp 0): for each of the 7 documents, a seeded sample of 6 sources; the
model sees *only that one slide/page* and must write a question answerable
from it alone (4 options, exactly one correct, a rationale quoting the
source fact, and the anchor's chunk id). Hard validation (stem ≤ 320
chars, 4 distinct non-empty options, answer index in 0–3, rationale ≥ 10
chars, source id must equal the anchor) with one retry, then drop. One
question flaked on a transient server-side truncation and was recovered on
a targeted re-run (Q4 #4); the bank stands at 42/42.

**Position bias fixed:** the first generation put most correct answers on
B and none on D; a deterministic seeded option shuffle (same seed → same
layout, applied identically by the Python generator and the page's JS
generator) rebalanced the bank to **A:15, B:9, C:6, D:12**.

**Material + topic selection:** the generator and the chat bar filter the
corpus exactly like `scripts/retrieve.py`'s `--docs` / `--topic`: document
ids restrict the pool; a topic requires every non-stopword token to appear
in a chunk's title+text (plain, testable keyword intersection). Verified:
*"What is RAG …?"* against `week05` returns RAG slides; against `week04`
it returns only lexical noise that rerank prunes; a topic of `pizza`
matches nothing.

**Fixed answer key:** a generated quiz is an immutable snapshot — seed,
questions, option order, answer positions, and rationale are written once
to `localStorage` ("mbax6418-quizgen-v1") and never mutated; generating a
new quiz creates a new snapshot under a new seed. The shipped bank is
baked into the HTML and cannot drift. **Solutions stay hidden** until the
student answers (Check) *or* explicitly requests them (Reveal solution);
revealed questions count as skipped, not correct, in the score strip
(seen in the quiz-answered screenshot: 1 right, 1 skipped, 100% of
answered).

## Q4 — Bugs and issues hit along the way

1. **PDF pages rendered upside down.** The first page renderer drew into a
   manually-flipped CGContext and produced 180°-rotated pages. Fix: use
   AppKit's `page.thumbnail(of:for:)`, which handles PDF→bitmap
   orientation; verified visually.
2. **Stale compiled tool.** `ingest_pdf.py` reused `.tools/pdf_tool`
   whenever it existed, silently running a pre-outline build. Fix: rebuild
   when the Swift source is newer (mtime) and surface `swiftc` errors.
3. **The model echoed `chunk <id>` as the answer's source id** — the quiz
   generator rejected 6/6 Hermes-guide questions until the validator
   normalized the "chunk " label prefix. (Slides passed because their
   prompt phrasing differed slightly.)
4. **A transient quiz-generation flake.** One of 42 questions failed JSON
   validation in two consecutive runs on *different* chunks (server-side
   truncation), passing on re-run. Recovered by merging a targeted
   re-generation; retried-only-once remains (a second attempt is future
   work).
5. **My verification harness's regex lost its backslashes twice.** A poll
   expression checked for a tab named `Quiz … (4q)`; double-escaped
   backslashes on disk produced a page-side regex matching a literal
   backslash, so a *correct* tab never "matched" — the feature worked, the
   assertion couldn't see it. Fix: plain `startsWith`/`endsWith`, no regex
   escapes. Lesson: keep verification checks at arm's length from escaping.
6. **The first corpus schema (weeks only) couldn't express the
   requirements.** Schema v2 added `kind / doc / doc_id / loc / section /
   images` while keeping the old `w2-s15` ids, so existing eval targets and
   saved quiz sources stayed valid through the migration.
7. **Answer-position bias in bank questions** (16/30 on B, 0 on D
   originally) — fixed with a seeded per-question option shuffle (Q3).
8. **The user's running Chrome blocked local-browser automation.**
   `browser.use_real_profile` refused to copy the profile while Chrome held
   its SQLite locks. Fix: the headless-CDP harness (`scripts/shot.mjs`,
   isolated profile, no dependencies) that drives, asserts, and screenshots
   — it works next to an open Chrome and leaves no trace.
9. **Deck media is heavy.** Slides ship embedded GIF/PNG up to 30MB; ingest
   recompresses to ~1000px JPEGs, skips >7MB files as evidence, and the
   pages reference images by relative path so everything works from
   `file://`.
10. **Rerank volatility** (Q1) — identical questions can return different
    top-4 across runs; not fixed, documented, and why the eval measures hit
    rates and samples are saved verbatim rather than regenerated.
11. **Out-of-scope queries still cost tokens.** A sushi question retrieves
    noise chunks before the answer step refuses. Mitigation: the refusal
    path works (Q2); a smarter gate (skip rerank when all BM25 scores are
    near zero) is future work.
12. **A placeholder that looked like a timestamp.** `quiz_questions.json`'s
    `meta.generated_at` was literally `true` — a `json.dumps(True)` stub.
    Fixed to a real UTC ISO timestamp + model name.

## Files and how to reproduce

| Deliverable | File |
|---|---|
| Final report | `README.md` (this file) |
| Corpus (164 chunks, committed) | `data/course_chunks.json` |
| Corpus metadata (docs, counts) | `data/corpus_meta.json` |
| Evidence images (194, committed) | `data/media/` |
| Drop-in folder for new PDFs (e.g. the syllabus) | `data/course_files/` (git-ignored) |
| Assistant prompt (grounding contract) | `prompts/assistant_prompt.md` |
| Slide extractor (.pptx → chunks + media) | `scripts/ingest_slides.py` |
| PDF extractor (PDFKit; builds `.tools/pdf_tool`) | `scripts/ingest_pdf.py` + `scripts/pdf_tool.swift` |
| Corpus builder (slides + PDFs + merge) | `scripts/make_corpus.py` |
| Retrieval: BM25 + rerank, `--docs`/`--topic` filters | `scripts/retrieve.py` |
| Assistant CLI (REPL / `--once` / `--docs` / `--topic` / `--save`) | `scripts/assist.py` |
| Shared endpoint client | `scripts/llm.py` |
| Quiz generator (per-doc, docs/topic, validated, bias-fixed) | `scripts/generate_quiz.py` |
| Quiz page builder | `scripts/build_quiz_page.py` |
| Chat page builder | `scripts/build_chat_page.py` |
| Retrieval eval (18 target-source questions) | `scripts/eval_retrieval.py` |
| Browser verification + screenshots | `scripts/shot.mjs` |
| **Chat page** (self-contained; endpoint + key for live answers) | `course_chat.html` |
| **Quiz page** (bank offline; generator needs endpoint) | `quiz_practice.html` |
| Quiz bank — questions, fixed key, rationale, sources | `results/quiz_questions.json` |
| Retrieval eval numbers | `results/retrieval_metrics.json` |
| Sampled Q&A runs (verbatim) | `results/qa_samples.json` |
| Screenshots | `screenshots/*.png` |

Reproduce (macOS; Python 3 stdlib; Node only for the screenshot harness;
Xcode Command Line Tools only for PDF ingestion):

```bash
# 1. build the corpus: decks (default ~/CU Boulder/LLM) + PDFs
#    (the LLM folder + data/course_files/); compiles pdf_tool on first run
python3 scripts/make_corpus.py

# 2. try retrieval on any question, with or without filters
python3 scripts/retrieve.py --query "what is quantization?" --topic quantization
python3 scripts/retrieve.py --query "RAG" --docs week04      # lexical noise, rerank prunes
python3 scripts/retrieve.py --query "install Hermes"          # PDF page retrieval

# 3. ask the assistant — one shot, or an interactive REPL
python3 scripts/assist.py --once "What is a KV cache?" --save
python3 scripts/assist.py

# 4. regenerate the quiz bank, or target one document/topic
python3 scripts/generate_quiz.py                                 # 42 questions, fixed seed
python3 scripts/generate_quiz.py --docs week05 --topic rag --per-doc 4

# 5. rebuild the two HTML surfaces from the JSON
python3 scripts/build_quiz_page.py
python3 scripts/build_chat_page.py

# 6. retrieval eval (18 cases incl. PDFs) and browser verification
python3 scripts/eval_retrieval.py
node scripts/shot.mjs quiz-answered       # modes: quiz-initial, quiz-generate,
                                          # chat-initial, chat-answer "<question>"

# 7. open the deliverables
open course_chat.html      # pick materials/topic, set your API key once
open quiz_practice.html    # bank works offline; "+ New quiz" for fixed-key quizzes
```

Configuration: the endpoint and key are class-provided and default to
`http://dobolyi.com:9001/v1` / `6418` (`scripts/llm.py`); override with
`LLM_API_URL` / `LLM_API_KEY` / `LLM_MODEL`. The web pages never embed the
key — you paste it once; it lives in your browser's localStorage and is
sent only to the class endpoint (CORS-open, verified by preflight).
Generated quizzes also live in localStorage, so they survive reloads and
work offline once created.

## References

- Course materials (the corpus): *MBAX 6418 — LLM Fundamentals / Prompt
  Engineering / Serving and Debugging / Context Engineering and RAG /
  Multimodal Generative AI* decks, plus the course's PDF files (Hermes
  Agent Installation Guide; assignment handouts), David Dobolyi.
- Robertson, S., & Zaragoza, H. (2009). *The Probabilistic Relevance
  Framework: BM25 and Beyond.* Foundations and Trends in Information
  Retrieval. (BM25 scoring used for the lexical leg.)
- Liu et al. (2023). *Lost in the Middle: How Language Models Use Long
  Contexts.* TACL (cited by the Week 5 deck, slide 7).
- Muennighoff et al. (2022). *MTEB: Massive Text Embedding Benchmark*
  (named on Week 5, slide 19 — the deck's own advice to evaluate retrieval
  on your own documents is the basis for `eval_retrieval.py`).
- Apple PDFKit (macOS) — text-layer + page-render extraction used in
  `pdf_tool.swift` (the one non-stdlib, optional dependency; without it
  PDFs are skipped and everything else still works).
- Class endpoint: `cyankiwi/Qwen3.6-35B-A3B-AWQ-4bit` served via vLLM
  (OpenAI-compatible API), provided by the course.
