"""
labeler — Human validation portal for OCR digit crops.

Provides:
  - exporter: batch PDF -> crop PNG -> index.jsonl pipeline
  - queue: priority-ordered, cursor-persisted validation queue
  - manifest: crash-safe append writer for manifest.jsonl + ImageFolder
  - server: Flask app for keyboard-only digit labeling
"""
