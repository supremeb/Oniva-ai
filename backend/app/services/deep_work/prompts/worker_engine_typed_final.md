## Final answer

After the save succeeds, call `complete_task` once. Its `result` is this object, and it is your whole answer (write no other text):

```json
{
  "summary": "Two or three sentences on what you found",
  "learning_count": 12,
  "source_count": 7,
  "top_findings": ["Most important finding", "Second most important finding"],
  "sources": [{"title": "Page title", "url": "https://example.com/page", "type": "web"}]
}
```

`learning_count` is the number the save reported (0 if you saved nothing). `sources` has at most 20 entries (none for `model_knowledge`). `top_findings` has at most five entries. If `complete_task` returns violations, fix them and call it again.
