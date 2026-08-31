# Dense retrieval experiment

Status: validated optional path; not yet the default submission runtime.

## Safety boundary

- `Agent(catalog_path)` remains `DenseMode.OFF` and preserves `mvp-0.701152`.
- `SHADOW` calls dense retrieval only on requests routed as Browsing and never
  adds its results to the candidate pool.
- `ON` fuses dense results into weighted RRF Top-200, but Official mode retains
  the validated Legacy Top-10 candidate set and uses the formal pipeline only
  as a bounded reranking signal.
- Dense backend failures are isolated and lexical retrieval continues.
- Buying-routed requests never invoke dense retrieval.
- Catalog vectors are bound to the frozen catalog SHA256 and every generated
  asset is checksummed.

The Development-mode experiment that allowed formal candidates to replace the
Legacy Top-10 reduced Hit@10 from 0.855 to 0.800. The Official guard therefore
must remain in place.

## Architecture

```text
Browsing request
  -> deterministic compact query text
  -> local MiniLM int8 ONNX query encoder
  -> normalized dot product against 50,000 x 384 float16 mmap
  -> dense Top-120
  -> weighted RRF Top-200 with BM25/category/use-case routes
  -> local constraint ranker
  -> guarded Top-10

Buying request
  -> existing lexical/constraint routes only
```

No vector database, network request, API key, generative LLM, Torch, or
Transformers runtime is required by the ONNX path.

## Assets and dependencies

Generated assets are intentionally outside Git until the submission packaging
limit and distribution mechanism are confirmed.

| Asset | Shape/content | Size observed |
| --- | --- | ---: |
| Catalog vectors | 50,000 x 384 float16 | 38.4 MB payload |
| ASIN row map + metadata | 50,000 IDs + checksums | about 0.55 MB |
| ONNX query encoder | arm64 int8 + x86 AVX2 int8 + tokenizer | about 45 MB |

Dependency manifests are isolated by purpose:

- `requirements-dense-build.txt`: build catalog embeddings;
- `requirements-dense-export.txt`: export both quantized ONNX models;
- `requirements-dense-runtime.txt`: NumPy, ONNX Runtime, and Tokenizers only.

The normal `requirements.txt` and default MVP remain unchanged.

## Reproduce generated assets

Run from the repository root. Replace `/tmp` paths with an approved persistent
artifact directory when preparing the final bundle.

```bash
python -m venv /tmp/shopping-copilot-dense-build
/tmp/shopping-copilot-dense-build/bin/pip install -r requirements-dense-build.txt
/tmp/shopping-copilot-dense-build/bin/python -m analysis.build_dense_assets \
  --catalog data/catalog.jsonl \
  --output /tmp/shopping-copilot-minilm-assets \
  --batch-size 256

python -m venv /tmp/shopping-copilot-onnx-export
/tmp/shopping-copilot-onnx-export/bin/pip install -r requirements-dense-export.txt
/tmp/shopping-copilot-onnx-export/bin/python -m analysis.export_onnx_query_encoder \
  --output /tmp/shopping-copilot-minilm-onnx-release
```

The tested model is `sentence-transformers/all-MiniLM-L6-v2` at revision
`1110a243fdf4706b3f48f1d95db1a4f5529b4d41`. The catalog binding is
`da979b05a68af864cb0dcf9ee6a81c010c7e66a57978ad286c7a2e005fc69a67`.
The model is distributed under Apache-2.0 according to its upstream model card;
retain its attribution and license notice in any submitted asset bundle.

## Run SHADOW or ON benchmark

```bash
python -m venv /tmp/shopping-copilot-dense-runtime
/tmp/shopping-copilot-dense-runtime/bin/pip install -r requirements-dense-runtime.txt

/tmp/shopping-copilot-dense-runtime/bin/python \
  -m analysis.dense_shadow_benchmark \
  --encoder onnx \
  --model-dir /tmp/shopping-copilot-minilm-onnx-release \
  --mode shadow \
  --runtime-mode official \
  --assets /tmp/shopping-copilot-minilm-assets \
  --output /tmp/dense-shadow-results.json
```

Change `--mode shadow` to `--mode on` only for the gated fusion run.

## Public-set results

All runs use the unchanged 200-session evaluator and frozen catalog.

| Configuration | Hit@10 | MRR | MTTC | Technical score |
| --- | ---: | ---: | ---: | ---: |
| Stable default OFF | 0.855000 | 0.495175 | 4.745 | 0.701152 |
| PyTorch SHADOW | 0.855000 | 0.495175 | 4.745 | 0.701152 |
| PyTorch ON, Official guard | 0.855000 | 0.498042 | 4.745 | 0.702013 |
| int8 ONNX ON, Official guard | 0.855000 | 0.497944 | 4.745 | 0.701983 |
| int8 ONNX ON, Development | 0.800000 | 0.378944 | 4.970 | 0.634283 |

The ONNX Official run kept every Hit gate:

| Scenario | Hit@10 | MRR | MTTC |
| --- | ---: | ---: | ---: |
| Browsing | 0.862500 | 0.486607 | 4.025 |
| Buying | 0.887500 | 0.514891 | 4.925 |
| Intent Override | 0.766667 | 0.437857 | 5.766667 |
| Boundary | 0.800000 | 0.633333 | 6.000 |

ONNX dense diagnostics over 920 turns:

- 683 attempted turns, 0 errors;
- 120 results per attempted turn;
- 80.417 candidates per turn were absent from lexical routes on average;
- 48.208 dense-evidenced candidates reached Top-200 on average;
- latency P50 12.857 ms, P95 28.156 ms, max 401.647 ms;
- full-process maximum RSS about 646 MB on the development machine, versus
  about 1.06 GB for the PyTorch ON prototype.

## Remaining release gates

1. Confirm the organizer accepts an approximately 84 MB generated local asset
   bundle and choose Git LFS, release artifact, or submission archive handling.
2. Run the ONNX path on the organizer-like x86 Python 3.12 environment and
   verify the AVX2 model, memory ceiling, and timeout.
3. Add a fail-open runtime loader so missing dependencies/assets select OFF and
   emit a machine-readable reason.
4. Re-run the clean-archive import, 117+ tests, demo, and public evaluator with
   network disabled.
5. Keep default OFF unless every release gate passes; never remove the Official
   Top-10 guard based on the current public evidence.
