"""Per-book local research library ("GraphRAG"): ingest sources, retrieve with a local judge,
cluster raw notes into themes, and build a citation-checked bibliography.

Everything runs against a local OpenAI-compatible server (LM Studio). Nothing leaves the machine
except the web pages you list in research/urls.txt, which are fetched once and saved locally.
"""
